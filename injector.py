"""injector.py — paste transcribed text into the focused window.

Works on both Wayland and X11; the clipboard + paste backend is chosen from the
session type at runtime.

  Wayland:  wl-copy  + ydotool key ctrl+v   (ydotoold must be running)
  X11:      xclip    + xdotool key ctrl+v

Injection modes (config "injection_mode"):
  auto-paste     — copy to clipboard, then simulate Ctrl+V.
  clipboard-only — copy to clipboard only; user pastes manually.
  notify-only    — desktop notification, no clipboard interaction.

We deliberately do NOT restore the previous clipboard contents: that async race
caused earlier dictations to paste stale text. The last dictation stays on the
clipboard, which is the least surprising behaviour.
"""

from __future__ import annotations
import functools
import os
import shutil
import subprocess
import time

from logger import log


@functools.lru_cache(maxsize=1)
def _ydotool_named_keys() -> bool:
    """Whether the installed ydotool uses named key sequences for `key`.

    The two ydotool families take mutually incompatible `key` arguments:
      • 0.1.x (Debian/Ubuntu):  named, e.g.  ``ydotool key ctrl+v``
      • 1.x   (Fedora/Arch/...): numeric ``keycode:state``, e.g. ``29:1 47:1 ...``
    Feed 0.1.x the numeric form and it types literal digits ("2442"); feed 1.x the
    named form and it errors out. We detect the family from `key --help`: only the
    0.1.x help describes plus-separated named sequences. Default to named (the
    Debian/Ubuntu norm) if detection fails.
    """
    try:
        out = subprocess.run(["ydotool", "key", "--help"],
                             capture_output=True, text=True, timeout=2)
        text = (out.stdout + out.stderr).lower()
    except (FileNotFoundError, subprocess.SubprocessError):
        return True
    if not text:
        return True
    # 1.x help talks about keycodes/states; 0.1.x talks about "plus"-joined names.
    if "plus" in text or "alt+" in text:
        return True
    if "keycode" in text or "state" in text or "pressed" in text:
        return False
    return True


def _is_wayland() -> bool:
    if os.environ.get("WAYLAND_DISPLAY"):
        return True
    if os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return True
    # Fall back to X11 only if there's clearly an X display.
    return not os.environ.get("DISPLAY")


def inject(text: str, mode: str, paste_delay_ms: int = 150) -> None:
    if mode == "auto-paste":
        _auto_paste(text, paste_delay_ms)
    elif mode == "clipboard-only":
        if _to_clipboard(text):
            log.info("Text copied to clipboard.")
    elif mode == "typing":
        if _type_text(text):
            log.info(f"Typed {len(text)} chars via ydotool type.")
    elif mode == "notify-only":
        _notify(text[:120])
    else:
        log.warning(f"Unknown injection mode {mode!r} — falling back to clipboard-only.")
        _to_clipboard(text)


# ── high-level paste ────────────────────────────────────────────────────────────

def _auto_paste(text: str, paste_delay_ms: int) -> None:
    if not _to_clipboard(text):
        _notify("WhisperKey: could not copy text to the clipboard")
        return

    # Give the compositor a moment to register the new selection before pasting.
    time.sleep(max(0, paste_delay_ms) / 1000)

    if _send_paste():
        log.info(f"Pasted {len(text)} chars via clipboard + Ctrl+V.")
    else:
        _notify("WhisperKey: text copied — press Ctrl+V to paste")


# ── backends ─────────────────────────────────────────────────────────────────────

def _to_clipboard(text: str) -> bool:
    if _is_wayland():
        return _run_clipboard(["wl-copy"], text, "wl-copy",
                              hint="sudo apt install wl-clipboard")
    # X11: prefer xclip, fall back to xsel
    if shutil.which("xclip"):
        return _run_clipboard(["xclip", "-selection", "clipboard"], text, "xclip",
                              hint="sudo apt install xclip")
    return _run_clipboard(["xsel", "--clipboard", "--input"], text, "xsel",
                          hint="sudo apt install xsel")


def _type_text(text: str) -> bool:
    if _is_wayland():
        return _run_paste(["ydotool", "type", text], "ydotool",
                          hint="ydotool type failed")
    return _run_paste(["xdotool", "type", "--clearmodifiers", text], "xdotool",
                      hint="xdotool type failed")


def _send_paste() -> bool:
    if _is_wayland():
        if _ydotool_named_keys():
            # ydotool 0.1.x: named key sequence.
            cmd = ["ydotool", "key", "--delay", "50", "ctrl+v"]
        else:
            # ydotool 1.x: numeric keycode:state — 29=LEFTCTRL, 47=V.
            cmd = ["ydotool", "key", "29:1", "47:1", "47:0", "29:0"]
        return _run_paste(cmd, "ydotool",
                          hint="ydotool/ydotoold not running — text left on clipboard")
    return _run_paste(["xdotool", "key", "--clearmodifiers", "ctrl+v"], "xdotool",
                      hint="xdotool not found — text left on clipboard")


# ── process helpers ──────────────────────────────────────────────────────────────

def _run_clipboard(cmd: list[str], text: str, name: str, hint: str) -> bool:
    try:
        proc = subprocess.Popen(cmd, stdin=subprocess.PIPE,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        proc.communicate(text.encode(), timeout=2)
        if proc.returncode not in (0, None):
            log.error(f"{name} exited with code {proc.returncode}")
            return False
        return True
    except FileNotFoundError:
        log.error(f"{name} not found. Install it: {hint}")
    except subprocess.TimeoutExpired:
        proc.kill(); proc.wait()
        log.error(f"{name} did not accept clipboard data within 2 seconds")
    return False


def _run_paste(cmd: list[str], name: str, hint: str) -> bool:
    try:
        subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL,
                       stderr=subprocess.PIPE, timeout=3)
        return True
    except FileNotFoundError:
        log.warning(hint)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as e:
        stderr = (getattr(e, "stderr", b"") or b"").decode().strip()
        log.error(f"{name} paste failed: {stderr or e}")
    return False


def _notify(msg: str) -> None:
    try:
        subprocess.run(["notify-send", "WhisperKey", msg], capture_output=True)
    except Exception:
        pass
