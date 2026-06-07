#!/usr/bin/env python3
"""
tray.py — WhisperKey tray controller.

A tiny, always-on system-tray app (AppIndicator via pystray). It NEVER imports
torch/faster-whisper, so it stays small (~40 MB) and holds zero VRAM — it just
drives the worker:

  • Start / Stop running   → systemctl --user start|stop whisperkey.service
                             (Stop fully frees the worker's model + RAM, per design)
  • Load / Unload model    → control socket  (independent of pause)
  • Pause / Resume hotkey  → control socket  (independent of model)
  • Idle mode              → control socket  (performance | balanced | low)
  • Copy last transcription, Open log, Edit config, Restart worker, Quit tray

The icon colour reflects state at a glance:
  grey  = worker stopped      blue = running, model unloaded
  green = model loaded        amber = paused          red = recording

PyGObject (gi) only exists in the system site-packages, so we add it to the path
before importing pystray (whose AppIndicator backend needs gi). venv + system are
both Python 3.12, so this just works.
"""

from __future__ import annotations
import os
import subprocess
import sys
import threading
import time
from pathlib import Path


def _ensure_gi_importable() -> None:
    """pystray's AppIndicator backend needs PyGObject (gi), which lives in the
    system site-packages, not the venv. Locate it dynamically (works across distros)
    and add it to sys.path. If it really isn't installed, fail with a clear hint.
    """
    try:
        import gi  # noqa: F401
        return
    except ModuleNotFoundError:
        pass
    for py in ("/usr/bin/python3", "/usr/bin/python", shutil_which("python3")):
        if not py:
            continue
        try:
            out = subprocess.run(
                [py, "-c", "import gi, os; print(os.path.dirname(os.path.dirname(gi.__file__)))"],
                capture_output=True, text=True, timeout=5,
            )
            path = out.stdout.strip()
            if path and os.path.isdir(path) and path not in sys.path:
                sys.path.append(path)
                import gi  # noqa: F401
                return
        except Exception:
            continue
    sys.stderr.write(
        "WhisperKey tray: PyGObject (gi) not found.\n"
        "Install it:  sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1\n"
    )
    raise SystemExit(1)


def shutil_which(name):
    import shutil
    return shutil.which(name)


_ensure_gi_importable()

import pystray
from PIL import Image, ImageDraw

import control

SERVICE = "whisperkey.service"
POLL_SECONDS = 2.0
LOG_PATH = Path.home() / ".local" / "state" / "whisperkey" / "whisperkey.log"
CONFIG_PATH = Path.home() / ".config" / "whisperkey" / "config.json"

# state colour palette
COLOURS = {
    "stopped": (120, 120, 120),
    "idle":    (60, 130, 240),
    "loaded":  (40, 190, 90),
    "paused":  (235, 170, 30),
    "recording": (225, 60, 60),
}


# ── icon rendering ───────────────────────────────────────────────────────────────

def _make_icon(colour) -> Image.Image:
    img = Image.new("RGBA", (64, 64), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.ellipse((8, 8, 56, 56), fill=colour + (255,))
    # little "mic" notch so it reads as dictation, not just a dot
    d.rounded_rectangle((28, 18, 36, 38), radius=4, fill=(255, 255, 255, 230))
    d.line((32, 38, 32, 46), fill=(255, 255, 255, 230), width=3)
    d.line((24, 46, 40, 46), fill=(255, 255, 255, 230), width=3)
    return img


_ICON_CACHE = {k: _make_icon(v) for k, v in COLOURS.items()}


# ── shared status (refreshed by the poll thread) ────────────────────────────────

class State:
    def __init__(self):
        self.running = False
        self.loaded = False
        self.paused = False
        self.recording = False
        self.mode = "balanced"
        self.device = "cuda"
        self.vram = 0
        self.ram = 0
        self.last_chars = 0

    def colour_key(self) -> str:
        if not self.running:
            return "stopped"
        if self.recording:
            return "recording"
        if self.paused:
            return "paused"
        if self.loaded:
            return "loaded"
        return "idle"

    def status_line(self) -> str:
        if not self.running:
            return "● Stopped"
        bits = ["● Running"]
        bits.append("model loaded" if self.loaded else "model off")
        if self.paused:
            bits.append("PAUSED")
        if self.device == "cuda" and self.vram:
            bits.append(f"{self.vram} MB VRAM")
        elif self.device != "cuda":
            bits.append(f"CPU{f' · {self.ram} MB' if self.ram else ''}")
        return "  ·  ".join(bits)


STATE = State()


# ── shell helpers ───────────────────────────────────────────────────────────────

def _systemctl(*args) -> None:
    subprocess.run(["systemctl", "--user", *args],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _service_active() -> bool:
    r = subprocess.run(["systemctl", "--user", "is-active", SERVICE],
                       capture_output=True, text=True)
    return r.stdout.strip() == "active"


def _notify(msg: str) -> None:
    subprocess.run(["notify-send", "-a", "WhisperKey", "WhisperKey", msg],
                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


def _xdg_open(path) -> None:
    subprocess.Popen(["xdg-open", str(path)],
                     stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)


# ── menu actions ─────────────────────────────────────────────────────────────────

def act_start(icon, _):
    _systemctl("start", SERVICE)
    _notify("Starting dictation worker…")
    _refresh_soon(icon)


def act_stop(icon, _):
    _systemctl("stop", SERVICE)
    _notify("Stopped — worker and model VRAM freed.")
    _refresh_soon(icon)


def act_restart(icon, _):
    _systemctl("restart", SERVICE)
    _notify("Restarting worker…")
    _refresh_soon(icon)


def act_load(icon, _):
    if STATE.running:
        control.send("load")
        _notify("Loading model into VRAM…")
        _refresh_soon(icon)


def act_unload(icon, _):
    if STATE.running:
        control.send("unload")
        _notify("Model unloaded — VRAM freed.")
        _refresh_soon(icon)


def act_pause(icon, _):
    if STATE.running:
        control.send("pause")
        _notify("Dictation paused (hotkey disabled).")
        _refresh_soon(icon)


def act_resume(icon, _):
    if STATE.running:
        control.send("resume")
        _notify("Dictation resumed.")
        _refresh_soon(icon)


def make_set_mode(mode):
    def _action(icon, _):
        if STATE.running:
            control.send(f"mode {mode}")
            _notify(f"Idle mode → {mode}")
            _refresh_soon(icon)
    return _action


def act_copy_last(icon, _):
    if not STATE.running:
        return
    resp = control.send("last")
    text = resp.get("text") or ""
    if not text:
        _notify("No transcription yet.")
        return
    try:
        p = subprocess.Popen(["wl-copy"], stdin=subprocess.PIPE)
        p.communicate(text.encode(), timeout=2)
        _notify(f"Copied last transcription ({len(text)} chars).")
    except Exception:
        _notify("Could not copy to clipboard.")


def act_open_log(icon, _):
    _xdg_open(LOG_PATH)


def act_edit_config(icon, _):
    _xdg_open(CONFIG_PATH)


def act_quit(icon, _):
    icon.stop()


# ── dynamic menu ─────────────────────────────────────────────────────────────────

def build_menu() -> pystray.Menu:
    Item = pystray.MenuItem

    def mode_checked(mode):
        return lambda item: STATE.running and STATE.mode == mode

    return pystray.Menu(
        Item(lambda item: STATE.status_line(), None, enabled=False),
        pystray.Menu.SEPARATOR,
        Item("Start running", act_start, visible=lambda i: not STATE.running),
        Item("Stop running", act_stop, visible=lambda i: STATE.running),
        Item("Restart worker", act_restart, visible=lambda i: STATE.running),
        pystray.Menu.SEPARATOR,
        Item(lambda item: ("Unload model" if STATE.loaded else "Load model"),
             lambda icon, item: act_unload(icon, item) if STATE.loaded else act_load(icon, item),
             enabled=lambda i: STATE.running),
        Item(lambda item: ("Resume dictation" if STATE.paused else "Pause dictation"),
             lambda icon, item: act_resume(icon, item) if STATE.paused else act_pause(icon, item),
             enabled=lambda i: STATE.running),
        pystray.MenuItem("Idle mode", pystray.Menu(
            Item("performance — always loaded", make_set_mode("performance"),
                 checked=mode_checked("performance"), radio=True),
            Item("balanced — unload when idle", make_set_mode("balanced"),
                 checked=mode_checked("balanced"), radio=True),
            Item("low — unload after each use", make_set_mode("low"),
                 checked=mode_checked("low"), radio=True),
        ), enabled=lambda i: STATE.running),
        pystray.Menu.SEPARATOR,
        Item("Copy last transcription", act_copy_last, enabled=lambda i: STATE.running),
        Item("Open log", act_open_log),
        Item("Edit config", act_edit_config),
        pystray.Menu.SEPARATOR,
        Item("Quit tray", act_quit),
    )


# ── status polling ───────────────────────────────────────────────────────────────

def _apply_status() -> None:
    STATE.running = _service_active()
    if STATE.running:
        st = control.send("status", timeout=2.0)
        if st.get("ok"):
            STATE.loaded = st.get("loaded", False)
            STATE.paused = st.get("paused", False)
            STATE.recording = st.get("recording", False)
            STATE.mode = st.get("mode", STATE.mode)
            STATE.device = st.get("device", STATE.device)
            STATE.vram = st.get("vram_used_mb", 0)
            STATE.ram = st.get("ram_used_mb", 0)
            STATE.last_chars = st.get("last_chars", 0)
        else:
            # service active but socket not up yet (still starting)
            STATE.loaded = STATE.paused = STATE.recording = False
    else:
        STATE.loaded = STATE.paused = STATE.recording = False
        STATE.vram = 0


def _poll_loop(icon: pystray.Icon) -> None:
    while True:
        try:
            prev = (STATE.running, STATE.colour_key())
            _apply_status()
            icon.icon = _ICON_CACHE[STATE.colour_key()]
            icon.title = f"WhisperKey — {STATE.status_line()}"
            icon.menu = build_menu()
            icon.update_menu()
            _ = prev
        except Exception:
            pass
        time.sleep(POLL_SECONDS)


def _refresh_soon(icon: pystray.Icon) -> None:
    """Nudge a status refresh shortly after an action (systemctl/socket settle)."""
    def _later():
        time.sleep(0.6)
        try:
            _apply_status()
            icon.icon = _ICON_CACHE[STATE.colour_key()]
            icon.title = f"WhisperKey — {STATE.status_line()}"
            icon.menu = build_menu()
            icon.update_menu()
        except Exception:
            pass
    threading.Thread(target=_later, daemon=True).start()


def main() -> None:
    _apply_status()
    icon = pystray.Icon(
        "whisperkey",
        icon=_ICON_CACHE[STATE.colour_key()],
        title=f"WhisperKey — {STATE.status_line()}",
        menu=build_menu(),
    )
    threading.Thread(target=_poll_loop, args=(icon,), daemon=True).start()
    icon.run()


if __name__ == "__main__":
    main()
