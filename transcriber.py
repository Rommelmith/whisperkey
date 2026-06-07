"""transcriber.py — faster-whisper wrapper.

Design notes:
- Model stays resident in VRAM between calls (use gpu_manager.py to unload on idle).
- Warm-up on load: one dummy transcribe so CUDA JIT compiles on startup, not on the
  first real hotkey press (which would add 3-5s latency).
- Hallucination filter: drop short clips, low-logprob segments, and known bad phrases.
- load() / unload() are called by GpuManager — don't call them directly in normal flow.
"""

from __future__ import annotations

import numpy as np

from logger import log

SAMPLE_RATE = 16_000
MIN_DURATION_S = 0.4  # clips shorter than this are discarded before calling Whisper

# Phrases Whisper commonly hallucinates on silence/noise
HALLUCINATION_BLOCKLIST = frozenset([
    "thanks for watching",
    "thank you for watching",
    "please subscribe",
    "like and subscribe",
    "subtitles by",
    "subtitled by",
    "transcribed by",
    "amara.org",
    "www.",
    "http",
])


class Transcriber:
    def __init__(self, model_name: str, device: str, compute_type: str, cpu_threads: int = 0):
        self._model_name = model_name
        self._device = device
        self._compute_type = compute_type
        self._cpu_threads = cpu_threads
        self._model = None

    # ── lifecycle ──────────────────────────────────────────────────────────────

    def load(self) -> None:
        from faster_whisper import WhisperModel
        log.info(f"Loading {self._model_name} on {self._device} [{self._compute_type}]")
        self._model = WhisperModel(
            self._model_name,
            device=self._device,
            compute_type=self._compute_type,
            cpu_threads=self._cpu_threads,
        )
        self._warmup()
        log.info("Model ready.")

    def unload(self) -> None:
        if self._model is not None:
            del self._model
            self._model = None
            # ctranslate2 frees device memory when the model object is collected.
            import gc
            gc.collect()
            # If PyTorch happens to be installed (it is NOT a dependency), nudge the
            # CUDA allocator too — best effort, silently skipped otherwise.
            try:
                import torch  # noqa: optional
                if torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
            log.info("Model unloaded.")

    def is_loaded(self) -> bool:
        return self._model is not None

    # ── inference ──────────────────────────────────────────────────────────────

    def transcribe(self, audio: np.ndarray, language: str, beam_size: int, vad_filter: bool) -> str | None:
        if self._model is None:
            raise RuntimeError("Model not loaded — call load() first.")

        # Drop clips that are too short to contain speech
        if len(audio) < MIN_DURATION_S * SAMPLE_RATE:
            log.debug(f"Clip too short ({len(audio)/SAMPLE_RATE:.2f}s), skipping.")
            return None

        segments, _ = self._model.transcribe(
            audio,
            language=language,
            beam_size=beam_size,
            vad_filter=vad_filter,
        )

        parts = []
        for seg in segments:
            if seg.no_speech_prob > 0.6:
                log.debug(f"Dropped segment (no_speech_prob={seg.no_speech_prob:.2f}): {seg.text!r}")
                continue
            if seg.avg_logprob < -1.0:
                log.debug(f"Dropped segment (avg_logprob={seg.avg_logprob:.2f}): {seg.text!r}")
                continue
            parts.append(seg.text.strip())

        text = " ".join(parts).strip()

        if not text:
            return None

        lower = text.lower()
        for phrase in HALLUCINATION_BLOCKLIST:
            if phrase in lower:
                log.debug(f"Hallucination blocked: {text!r}")
                return None

        return text

    # ── internals ─────────────────────────────────────────────────────────────

    def _warmup(self) -> None:
        """One dummy transcribe to force CUDA kernel JIT compilation at load time."""
        log.debug("Running warm-up inference...")
        dummy = np.zeros(SAMPLE_RATE, dtype=np.float32)  # 1 second of silence
        list(self._model.transcribe(dummy, beam_size=1)[0])
        log.debug("Warm-up done.")
