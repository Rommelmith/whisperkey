#!/usr/bin/env python3
"""
check_env.py — WhisperKey environment sanity check.

Hardware- and session-aware: it only runs NVIDIA checks if a GPU is present, and
checks the clipboard/paste tools for your actual session (Wayland or X11).

Usage:
  python check_env.py               # quick checks
  python check_env.py --test-model  # also load the configured model (may download)
"""

import argparse
import grp
import os
import shutil
import subprocess
import sys
from pathlib import Path

GREEN = "\033[92m"; YELLOW = "\033[93m"; RED = "\033[91m"; RESET = "\033[0m"; BOLD = "\033[1m"


def _pass(m): return f"{GREEN}[PASS]{RESET} {m}"
def _warn(m): return f"{YELLOW}[WARN]{RESET} {m}"
def _fail(m): return f"{RED}[FAIL]{RESET} {m}"


def _session() -> str:
    if os.environ.get("WAYLAND_DISPLAY") or os.environ.get("XDG_SESSION_TYPE", "").lower() == "wayland":
        return "wayland"
    return "x11" if os.environ.get("DISPLAY") else "wayland"


def _has_gpu() -> bool:
    return shutil.which("nvidia-smi") is not None


# ── checks ───────────────────────────────────────────────────────────────────────

def check_gpu():
    try:
        out = subprocess.check_output(
            ["nvidia-smi", "--query-gpu=name,memory.total", "--format=csv,noheader"],
            stderr=subprocess.DEVNULL, text=True).strip()
        return _pass(f"GPU: {out}"), True
    except Exception as e:
        return _warn(f"nvidia-smi present but failed: {e}"), False


def check_ctranslate2():
    """The real engine behind faster-whisper. Reports whichever backend works."""
    try:
        import ctranslate2
        ver = ctranslate2.__version__
        if _has_gpu():
            cuda = ctranslate2.get_supported_compute_types("cuda")
            if cuda:
                return _pass(f"ctranslate2 {ver} — CUDA compute types: {', '.join(cuda)}"), True
            return _warn(f"ctranslate2 {ver} — GPU present but no CUDA compute types "
                         f"(missing CUDA/cuDNN libs?). Will run on CPU."), False
        cpu = ctranslate2.get_supported_compute_types("cpu")
        return _pass(f"ctranslate2 {ver} — CPU compute types: {', '.join(cpu)}"), True
    except ImportError:
        return _fail("ctranslate2 not installed — run: pip install -r requirements.txt"), False
    except Exception as e:
        return _fail(f"ctranslate2 check failed: {e}"), False


def check_pydeps():
    missing = []
    for mod, pkg in [("faster_whisper", "faster-whisper"), ("sounddevice", "sounddevice"),
                     ("numpy", "numpy"), ("evdev", "evdev"), ("pystray", "pystray"), ("PIL", "Pillow")]:
        try:
            __import__(mod)
        except ImportError:
            missing.append(pkg)
    if missing:
        return _fail(f"Missing Python packages: {', '.join(missing)} — pip install -r requirements.txt"), False
    return _pass("Core Python packages importable"), True


def check_tray_gi():
    """pystray's tray backend needs system PyGObject (gi) + an AppIndicator typelib."""
    code = ("import gi; gi.require_version('Gtk','3.0');"
            "[gi.require_version(n,'0.1') for n in ('AyatanaAppIndicator3',)]")
    for py in (sys.executable, "/usr/bin/python3"):
        try:
            r = subprocess.run([py, "-c", code], capture_output=True, timeout=8)
            if r.returncode == 0:
                return _pass(f"PyGObject + AppIndicator available ({py})"), True
        except Exception:
            continue
    return _warn("PyGObject/AppIndicator not found — tray icon won't show. "
                 "Fix: sudo apt install python3-gi gir1.2-ayatanaappindicator3-0.1"), False


def check_input_group():
    try:
        if grp.getgrnam("input").gr_gid in os.getgroups():
            return _pass("User is in the 'input' group (hotkey will work)"), True
        return _warn("Not in 'input' group — hotkey fails without root. "
                     "Fix: sudo usermod -aG input $USER  (then log out/in)"), False
    except KeyError:
        return _warn("'input' group does not exist on this system"), False


def check_uinput():
    p = Path("/dev/uinput")
    if p.exists() and os.access(p, os.W_OK):
        return _pass("/dev/uinput writable (auto-paste will work)"), True
    if p.exists():
        return _warn("/dev/uinput exists but not writable — ydotoold usually handles this"), False
    return _warn("/dev/uinput missing — run: sudo modprobe uinput  (needed for Wayland auto-paste)"), False


def check_binary(name, purpose, apt):
    path = shutil.which(name)
    if path:
        return _pass(f"{name} found ({purpose})"), True
    return _warn(f"{name} not found — {purpose}. Fix: sudo apt install {apt}"), False


def check_model_load(model_name):
    import json
    cfg_path = Path.home() / ".config" / "whisperkey" / "config.json"
    device, compute = "cpu", "int8"
    if cfg_path.exists():
        cfg = json.loads(cfg_path.read_text())
        model_name = model_name or cfg.get("model")
        device, compute = cfg.get("device", device), cfg.get("compute_type", compute)
    model_name = model_name or "base"
    print(f"  Loading {model_name} on {device} [{compute}] (may download)…")
    try:
        from faster_whisper import WhisperModel
        m = WhisperModel(model_name, device=device, compute_type=compute)
        del m
        return _pass(f"WhisperModel({model_name}, {device}, {compute}) loaded"), True
    except Exception as e:
        return _fail(f"Model load failed: {e}"), False


def main():
    ap = argparse.ArgumentParser(description="WhisperKey environment check")
    ap.add_argument("--test-model", action="store_true", help="Also load the configured model")
    ap.add_argument("--model", default=None, help="Override model to test")
    args = ap.parse_args()

    session = _session()
    print(f"\n{BOLD}WhisperKey — Environment Check{RESET}  (session: {session})\n" + "─" * 52)

    checks = [check_pydeps, check_ctranslate2, check_tray_gi, check_input_group, check_uinput]
    if _has_gpu():
        checks.insert(1, check_gpu)
    if session == "wayland":
        checks += [lambda: check_binary("wl-copy", "Wayland clipboard", "wl-clipboard"),
                   lambda: check_binary("ydotool", "Wayland paste", "ydotool")]
    else:
        checks += [lambda: check_binary("xclip", "X11 clipboard", "xclip"),
                   lambda: check_binary("xdotool", "X11 paste", "xdotool")]

    results = []
    for fn in checks:
        msg, ok = fn()
        print(msg)
        results.append(ok)

    if args.test_model:
        print()
        msg, ok = check_model_load(args.model)
        print(msg)
        results.append(ok)

    n = sum(results); total = len(results)
    print("\n" + "─" * 52)
    if n == total:
        print(f"{GREEN}{BOLD}All {total} checks passed.{RESET} WhisperKey is ready.\n")
    else:
        print(f"{YELLOW}{BOLD}{n}/{total} passed.{RESET} Review the [WARN]/[FAIL] items above.\n")
    sys.exit(0 if n == total else 1)


if __name__ == "__main__":
    main()
