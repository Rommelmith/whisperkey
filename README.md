# WhisperKey

**Local, private, push-to-talk voice dictation for Linux** — a free alternative to
Wispr Flow, which has no Linux version. Hold a hotkey, speak, release, and your words
are transcribed by [faster-whisper](https://github.com/SYSTRAN/faster-whisper) and
pasted into whatever app is focused. Nothing leaves your machine.

Runs on **Wayland and X11**, on **GPU or CPU**, and ships with a system-tray app to
start/stop it, load/unload the model, and watch resource use — built so it stays out
of the way when you need your GPU for other work.

![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)
![Platform: Linux](https://img.shields.io/badge/Platform-Linux-informational)

---

## Why

- **Private by design** — audio is transcribed locally; no cloud, no account, no telemetry.
- **Fast** — the model stays warm in memory; dictation is near-instant on a GPU and very usable on CPU.
- **Resource-aware** — lazy model loading, idle auto-unload, and a one-click "stop" that frees all memory. Ideal if you also do ML/training on the same box.
- **Works everywhere on Linux** — kernel-level hotkey (evdev) works under both Wayland and X11.

## Features

- Push-to-talk dictation with a configurable hotkey chord (default **Left Ctrl + Left Super**).
- GPU **or** CPU, with automatic hardware detection and graceful CUDA→CPU fallback.
- System-tray control: **Start/Stop**, **Load/Unload model**, **Pause/Resume**, **idle mode**, live VRAM/RAM readout, copy-last-transcription.
- Spoken commands (e.g. say "new line", "comma", "period") — fully configurable.
- Hallucination filtering for silence/noise, and a 5-minute recording safety cap.
- Clean uninstall.

## Requirements

- A modern Linux desktop (developed on Ubuntu/GNOME; works on KDE and others).
- Python 3.10+.
- For the **tray icon on GNOME**: the *AppIndicator* extension
  ([`ubuntu-appindicators`](https://extensions.gnome.org/extension/615/appindicator-support/)
  or [KStatusNotifierItem/AppIndicator Support](https://extensions.gnome.org/extension/615/appindicator-support/)).
  KDE/others show it natively.
- An NVIDIA GPU is optional. CPU-only works out of the box.

## Install

```bash
git clone https://github.com/Rommelmith/whisperkey.git
cd whisperkey
./install.sh
```

The installer detects your hardware and session, creates a dedicated virtualenv,
installs system + Python dependencies, writes a hardware-appropriate config, and
enables the systemd user services. Run `./install.sh --yes` for a non-interactive
install with defaults.

After install, **if it added you to the `input` group, log out and back in** (needed
once so WhisperKey can read the hotkey without root).

### What gets installed

| Component | What it is |
|---|---|
| `whisperkey.service` (user) | the worker: hotkey → record → transcribe → paste |
| `whisperkey-tray.service` (user) | the tray control icon |
| venv at `./.venv` | isolated Python deps (faster-whisper, etc.) |
| `~/.config/whisperkey/config.json` | your settings |

System packages: `portaudio19-dev`, `python3-gi`, `gir1.2-ayatanaappindicator3-0.1`,
plus `wl-clipboard`+`ydotool` (Wayland) **or** `xclip`+`xdotool` (X11).

## Usage

1. Hold your hotkey (default **Left Ctrl + Left Super**) and speak.
2. Release to transcribe; the text is pasted into the focused window.
3. Press **Esc** while holding to cancel.

Control it from the **tray icon**, or the CLI:

```bash
.venv/bin/python main.py --ctl status     # JSON status
.venv/bin/python main.py --ctl load        # load model into VRAM/RAM
.venv/bin/python main.py --ctl unload       # free it
.venv/bin/python main.py --ctl pause        # disable the hotkey
.venv/bin/python main.py --ctl resume
.venv/bin/python main.py --ctl mode low     # performance | balanced | low
```

Tray icon colour: **grey** stopped · **blue** running · **green** model loaded ·
**amber** paused · **red** recording.

## Configuration

`~/.config/whisperkey/config.json`:

| Key | Meaning |
|---|---|
| `model` | `tiny`/`base`/`small`/`medium`/`large-v3-turbo`/`large-v3` |
| `device` | `cuda` or `cpu` (auto-falls back to `cpu` if CUDA is unavailable) |
| `compute_type` | `int8` (CPU), `int8_float16`/`float16` (GPU) |
| `cpu_threads` | CPU inference threads |
| `hotkey` | evdev key names, e.g. `["KEY_LEFTCTRL","KEY_LEFTMETA"]` (`python main.py --list-keys`) |
| `idle_mode` | `performance` (always loaded) · `balanced` (unload when idle) · `low` (unload after each use) |
| `model_idle_unload_seconds` | balanced-mode idle timeout |
| `preload_on_start` | `false` = armed but 0 memory until first dictation (recommended) |
| `injection_mode` | `auto-paste` · `clipboard-only` · `notify-only` |
| `command_substitutions` | spoken phrase → text, e.g. `"new line": "\n"` |

### Picking a model on CPU

`tiny`/`base` are real-time on most CPUs; `small` is more accurate but slower.
`medium` and `large-*` are best left to GPUs. The installer picks a sensible default
from your RAM.

## Troubleshooting

- **Hotkey does nothing** → you're probably not in the `input` group yet, or haven't logged out/in since. Check: `python check_env.py`.
- **No tray icon (GNOME)** → enable the AppIndicator GNOME extension, then `systemctl --user restart whisperkey-tray.service`.
- **Auto-paste doesn't paste** → Wayland needs `ydotoold` running (`systemctl --user status ydotoold`); X11 needs `xdotool`. The text is always left on the clipboard as a fallback.
- **GPU model fails to load** → it auto-falls back to CPU; run `python check_env.py --test-model` to see why (often missing cuDNN).
- **Everything else** → `python check_env.py` and the log at `~/.local/state/whisperkey/whisperkey.log`.

## Uninstall

```bash
./uninstall.sh
```

Removes the services and (optionally) your config and venv. Leaves apt packages and
group membership alone.

## How it works

Two small processes talk over a Unix socket in `$XDG_RUNTIME_DIR`:

```
tray.py  ──socket (load/unload/pause/mode)──▶  main.py (worker)
   │                                              ├─ evdev hotkey (Wayland + X11)
   └── systemctl start/stop ───────────────▶      ├─ sounddevice mic capture
                                                   ├─ faster-whisper / ctranslate2
                                                   └─ clipboard + paste injector
```

The tray never imports the ML stack, so it stays tiny and holds no GPU memory.

## Contributing

Issues and PRs welcome. Run the tests with:

```bash
.venv/bin/python -m unittest discover -s tests
```

## License

[GPL-3.0](LICENSE) © Rommelmith
