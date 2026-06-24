#!/usr/bin/env python3
"""
main.py — WhisperKey worker.

hotkey → record → transcribe → inject (clipboard/paste).

A small control socket (control.py) lets the tray app (or the --ctl CLI) drive the
worker live: load/unload the model, pause/resume the hotkey, switch idle mode, and
read status. The model lifecycle is owned by GpuManager so VRAM use is explicit.

Usage:
  python main.py                       # run the worker (reads ~/.config/whisperkey/config.json)
  python main.py --list-keys           # list evdev key names usable as a hotkey
  python main.py --ctl status          # talk to a running worker over the control socket
  python main.py --ctl load|unload|pause|resume
  python main.py --ctl mode balanced   # performance | balanced | low
"""

from __future__ import annotations
import argparse
import json
import signal
import sys
import threading
import time

import numpy as np

import config as cfg_mod
import control
from audio import AudioRecorder
from gpu_manager import GpuManager
from hotkey import HotkeyListener
from injector import inject
from logger import log
from postprocess import apply as postprocess
from resource_detector import detect as detect_hw, recommend, vram_used_mb, mem_used_mb, cuda_available
from transcriber import Transcriber

# ── ANSI colours for terminal feedback ────────────────────────────────────────
RED = "\033[91m"; YELLOW = "\033[93m"; GREEN = "\033[92m"; RESET = "\033[0m"; BOLD = "\033[1m"


def main() -> None:
    parser = argparse.ArgumentParser(description="WhisperKey — local voice dictation")
    parser.add_argument("--list-keys", action="store_true", help="Print evdev key names and exit")
    parser.add_argument("--ctl", nargs="+", metavar="CMD",
                        help="Send a command to a running worker (status/load/unload/pause/resume/mode X) and exit")
    args = parser.parse_args()

    if args.list_keys:
        _list_keys()
        return

    if args.ctl:
        resp = control.send(" ".join(args.ctl))
        print(json.dumps(resp, indent=2))
        sys.exit(0 if resp.get("ok") else 1)

    _run_worker()


# ── the long-running worker ───────────────────────────────────────────────────

def _run_worker() -> None:
    cfg = cfg_mod.load()
    log.info(f"Config: model={cfg['model']} device={cfg['device']} compute={cfg['compute_type']} "
             f"idle_mode={cfg['idle_mode']} preload={cfg['preload_on_start']}")

    # ── hardware detection (informational on startup) ──────────────────────────
    hw = detect_hw()
    rec_model, _, _ = recommend(hw)
    if hw["gpu_name"]:
        log.info(f"GPU: {hw['gpu_name']}  VRAM: {hw['vram_gb']:.1f} GB")
    else:
        log.info("No GPU detected — running on CPU.")
    if rec_model != cfg["model"]:
        log.info(f"(Note: recommended model for this hardware is {rec_model!r}, "
                 f"but config says {cfg['model']!r})")

    # ── CUDA → CPU fallback ─────────────────────────────────────────────────────
    # If the config asks for CUDA but this machine can't actually run it (no GPU,
    # missing CUDA/cuDNN, unsupported arch), fall back to CPU so the app still works.
    device, compute_type = cfg["device"], cfg["compute_type"]
    if device == "cuda" and not cuda_available():
        log.warning("CUDA requested but unavailable — falling back to CPU (compute_type=int8).")
        device, compute_type = "cpu", "int8"
        if cfg["model"] in ("large-v3", "large-v3-turbo", "large-v2", "medium"):
            log.warning(f"Model {cfg['model']!r} is heavy on CPU; consider 'base' or 'small' "
                        f"in {cfg_mod.CONFIG_PATH} for faster dictation.")
    cfg["device"], cfg["compute_type"] = device, compute_type

    # ── set up components ──────────────────────────────────────────────────────
    transcriber = Transcriber(cfg["model"], device, compute_type, cfg.get("cpu_threads", 0))
    gpu_mgr = GpuManager(
        transcriber,
        cfg["idle_mode"],
        idle_seconds=cfg["model_idle_unload_seconds"],
        preload=cfg["preload_on_start"],
    )

    recorder = AudioRecorder(on_auto_stop=lambda audio: _handle_audio(audio, cfg, transcriber, gpu_mgr, state))

    # ── state shared between callbacks and the control server ──────────────────
    state = {"cancelled": False, "paused": False, "recording": False,
             "last_text": "", "last_chars": 0}

    def on_press() -> None:
        if state["paused"]:
            return
        state["cancelled"] = False
        state["recording"] = True
        print(f"\r{RED}{BOLD}● Recording...{RESET}  (release to transcribe, Esc to cancel)", end="", flush=True)
        recorder.start()

    def on_release() -> None:
        if not state["recording"]:
            return
        state["recording"] = False
        audio = recorder.stop()
        dur = recorder.duration()
        print(f"\r{YELLOW}◌ Transcribing...{RESET}  ({dur:.1f}s recorded)        ", end="", flush=True)

        if state["cancelled"]:
            return

        t = threading.Thread(
            target=_handle_audio,
            args=(audio, cfg, transcriber, gpu_mgr, state),
            daemon=True,
        )
        t.start()

    def on_cancel() -> None:
        state["cancelled"] = True
        state["recording"] = False
        recorder.stop()
        print(f"\r{YELLOW}✗ Cancelled.{RESET}                                        ")

    # ── control server (tray / --ctl) ──────────────────────────────────────────
    def _status(*_) -> dict:
        return {
            "running": True,
            "loaded": gpu_mgr.is_loaded(),
            "paused": state["paused"],
            "recording": state["recording"],
            "mode": gpu_mgr.mode,
            "model": cfg["model"],
            "device": cfg["device"],
            # On GPU report VRAM; on CPU report this worker's resident RAM.
            "vram_used_mb": vram_used_mb() if cfg["device"] == "cuda" else 0,
            "ram_used_mb": mem_used_mb() if cfg["device"] != "cuda" else 0,
            "last_chars": state["last_chars"],
        }

    def _pause(*_) -> dict:
        state["paused"] = True
        log.info("Dictation paused via control socket.")
        return {"paused": True}

    def _resume(*_) -> dict:
        state["paused"] = False
        log.info("Dictation resumed via control socket.")
        return {"paused": False}

    server = control.ControlServer({
        "status": _status,
        "load": lambda *_: (gpu_mgr.force_load(), {"loaded": gpu_mgr.is_loaded()})[1],
        "unload": lambda *_: (gpu_mgr.force_unload(), {"loaded": gpu_mgr.is_loaded()})[1],
        "pause": _pause,
        "resume": _resume,
        "mode": lambda arg: {"mode": (gpu_mgr.set_mode(arg) and gpu_mgr.mode)},
        "last": lambda *_: {"text": state["last_text"]},
    })

    # ── start hotkey listener ─────────────────────────────────────────────────
    listener = HotkeyListener(
        hotkey_names=cfg["hotkey"],
        grab=cfg["grab_hotkey"],
        on_press=on_press,
        on_release=on_release,
        on_cancel=on_cancel,
    )

    stop_event = threading.Event()
    shutdown_logged = False

    def shutdown(*_) -> None:
        nonlocal shutdown_logged
        if not shutdown_logged:
            log.info("Shutting down.")
            shutdown_logged = True
        stop_event.set()

    signal.signal(signal.SIGTERM, shutdown)
    signal.signal(signal.SIGINT, shutdown)

    try:
        listener.start()
    except Exception as e:
        log.error(f"Failed to start hotkey listener: {e}")
        sys.exit(1)

    server.start()

    hotkey_label = " + ".join(cfg["hotkey"]) if isinstance(cfg["hotkey"], list) else cfg["hotkey"]
    print(f"\n{GREEN}{BOLD}WhisperKey ready.{RESET}  Hold {hotkey_label} to dictate.\n")

    try:
        while not stop_event.wait(1):
            pass
    except KeyboardInterrupt:
        shutdown()
    finally:
        server.stop()
        listener.stop()


# ── transcription worker (runs in background thread) ──────────────────────────

def _handle_audio(audio: np.ndarray | None, cfg: dict, transcriber: Transcriber,
                  gpu_mgr: GpuManager, state: dict) -> None:
    if audio is None:
        print(f"\r{YELLOW}✗ No audio captured.{RESET}                          ")
        return

    t_start = time.monotonic()

    try:
        gpu_mgr.ensure_loaded()
        text = transcriber.transcribe(
            audio,
            language=cfg["language"],
            beam_size=cfg["beam_size"],
            vad_filter=cfg["vad_filter"],
        )
    except Exception as e:
        log.error(f"Transcription error: {e}")
        print(f"\r{RED}✗ Transcription failed: {e}{RESET}                    ")
        return
    finally:
        gpu_mgr.after_use()

    t_elapsed = time.monotonic() - t_start
    duration_s = len(audio) / 16_000

    if not text:
        log.info(f"No speech detected ({duration_s:.1f}s clip, {t_elapsed:.2f}s inference).")
        print(f"\r{YELLOW}✗ No speech detected.{RESET}                         ")
        return

    text = postprocess(text, cfg.get("command_substitutions", {}))

    state["last_text"] = text
    state["last_chars"] = len(text)

    log.info(f"Transcribed {duration_s:.1f}s → {len(text)} chars in {t_elapsed:.2f}s: {text!r}")
    print(f"\r{GREEN}✓{RESET} {text}")

    # inject into focused window
    injection_mode = cfg.get("injection_mode", "clipboard-only")
    if injection_mode != "terminal-only":
        try:
            inject(text, injection_mode, paste_delay_ms=cfg.get("paste_delay_ms", 150))
        except Exception as e:
            log.error(f"Injection error: {e}")


# ── helpers ────────────────────────────────────────────────────────────────────

def _list_keys() -> None:
    try:
        from evdev import ecodes
        keys = sorted(k for k in dir(ecodes) if k.startswith("KEY_"))
        print("Available hotkey names:")
        for k in keys:
            print(f"  {k}")
    except ImportError:
        print("evdev not installed — run install.sh first.")


if __name__ == "__main__":
    main()
