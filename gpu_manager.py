"""gpu_manager.py — controls when the Whisper model is loaded/unloaded.

Three modes:
  performance  — model stays loaded forever (instant response, VRAM always used)
  balanced     — unload after N idle seconds (default), reload on next use
  low          — unload immediately after each transcription (max VRAM savings)

Resource-first: with preload_on_start=False the model is NOT loaded at startup
even in performance/balanced — the worker is armed but holds 0 VRAM until the
first dictation (or a manual force_load() from the tray).

Call ensure_loaded() before transcribing, after_use() when done.
The tray drives force_load() / force_unload() / set_mode() over the control socket.
"""

from __future__ import annotations
import threading
import time

from logger import log

VALID_MODES = ("performance", "balanced", "low")


class GpuManager:
    def __init__(self, transcriber, mode: str, idle_seconds: int = 300, preload: bool = False):
        self._t = transcriber
        self._mode = mode if mode in VALID_MODES else "balanced"
        self._idle_s = idle_seconds
        self._last_use: float = 0.0
        self._lock = threading.RLock()
        self._watcher_started = False

        if preload and self._mode in ("performance", "balanced"):
            self._t.load()
            self._last_use = time.monotonic()

        if self._mode == "balanced":
            self._start_idle_watcher()

    # ── properties ──────────────────────────────────────────────────────────────

    @property
    def mode(self) -> str:
        return self._mode

    def is_loaded(self) -> bool:
        return self._t.is_loaded()

    # ── normal transcription flow ───────────────────────────────────────────────

    def ensure_loaded(self) -> None:
        with self._lock:
            if not self._t.is_loaded():
                log.info("Loading model on demand...")
                self._t.load()
            self._last_use = time.monotonic()

    def after_use(self) -> None:
        with self._lock:
            self._last_use = time.monotonic()
            if self._mode == "low":
                self._t.unload()

    # ── manual control (from the tray / --ctl) ──────────────────────────────────

    def force_load(self) -> bool:
        with self._lock:
            if not self._t.is_loaded():
                self._t.load()
            self._last_use = time.monotonic()
            return True

    def force_unload(self) -> bool:
        with self._lock:
            self._t.unload()
            return True

    def set_mode(self, mode: str) -> bool:
        if mode not in VALID_MODES:
            raise ValueError(f"Unknown idle mode {mode!r}")
        with self._lock:
            self._mode = mode
            self._last_use = time.monotonic()
            if mode == "performance":
                if not self._t.is_loaded():
                    self._t.load()
            elif mode == "low":
                # next dictation loads on demand, unloads right after
                self._t.unload()
            elif mode == "balanced":
                self._start_idle_watcher()
            log.info(f"Idle mode set to {mode!r}.")
            return True

    # ── internals ─────────────────────────────────────────────────────────────

    def _start_idle_watcher(self) -> None:
        if self._watcher_started:
            return
        self._watcher_started = True

        def watch():
            while True:
                time.sleep(min(60, max(5, self._idle_s)))
                with self._lock:
                    if self._mode != "balanced":
                        continue
                    if self._t.is_loaded():
                        idle_s = time.monotonic() - self._last_use
                        if idle_s >= self._idle_s:
                            log.info(f"Idle for {idle_s/60:.1f} min — unloading model.")
                            self._t.unload()

        t = threading.Thread(target=watch, daemon=True, name="idle-watcher")
        t.start()
