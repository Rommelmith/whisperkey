"""audio.py — microphone recording via sounddevice.

Records at 16 kHz mono — Whisper's native sample rate, so no resampling needed.
Streams into a list of chunks via callback; stop() returns a flat numpy array.
Hard cap at MAX_SECONDS to prevent runaway buffer if hotkey is accidentally held.
"""

from __future__ import annotations
import threading
import time

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16_000
MAX_SECONDS = 300  # 5-minute hard cap


class AudioRecorder:
    def __init__(self, on_auto_stop: callable | None = None):
        self._chunks: list[np.ndarray] = []
        self._stream: sd.InputStream | None = None
        self._lock = threading.Lock()
        self._start_time: float = 0.0
        self._on_auto_stop = on_auto_stop  # called if recording hits the time cap
        self._timer: threading.Timer | None = None

    def start(self) -> None:
        with self._lock:
            self._chunks = []
            self._start_time = time.monotonic()

        self._stream = sd.InputStream(
            samplerate=SAMPLE_RATE,
            channels=1,
            dtype="float32",
            callback=self._callback,
        )
        self._stream.start()

        # Auto-stop at MAX_SECONDS
        self._timer = threading.Timer(MAX_SECONDS, self._auto_stop)
        self._timer.daemon = True
        self._timer.start()

    def stop(self) -> np.ndarray | None:
        """Stop recording and return audio as a float32 numpy array, or None if empty."""
        if self._timer:
            self._timer.cancel()
            self._timer = None

        if self._stream:
            self._stream.stop()
            self._stream.close()
            self._stream = None

        with self._lock:
            chunks = self._chunks[:]
            self._chunks = []

        if not chunks:
            return None
        return np.concatenate(chunks).flatten()

    def duration(self) -> float:
        return time.monotonic() - self._start_time if self._start_time else 0.0

    # ── internals ─────────────────────────────────────────────────────────────

    def _callback(self, indata: np.ndarray, frames: int, t, status) -> None:
        with self._lock:
            self._chunks.append(indata.copy())

    def _auto_stop(self) -> None:
        from logger import log
        log.warning("Recording hit 5-minute cap — auto-stopping.")
        audio = self.stop()
        if self._on_auto_stop:
            self._on_auto_stop(audio)
