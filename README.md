<div align="center">

# 🎙️ WhisperKey

**Local, private, push-to-talk voice dictation for Linux.**

Hold a key, speak, release — your words are transcribed on-device and pasted into
whatever app is focused. No cloud, no account, no telemetry. A free, open-source
alternative to [Wispr Flow](https://wisprflow.ai/) for the Linux desktop.

[![License: GPL v3](https://img.shields.io/badge/License-GPLv3-blue.svg)](LICENSE)
![Platform: Linux](https://img.shields.io/badge/Platform-Linux-informational)
![Wayland + X11](https://img.shields.io/badge/Display-Wayland%20%2B%20X11-success)
![GPU or CPU](https://img.shields.io/badge/Runs%20on-GPU%20or%20CPU-orange)
![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-3776AB)

</div>

---

## Table of contents

- [Why WhisperKey](#why-whisperkey)
- [Features](#features)
- [How it works](#how-it-works)
- [Quick start](#quick-start)
- [Usage](#usage)
- [The tray menu](#the-tray-menu)
- [Configuration](#configuration)
- [Choosing a model](#choosing-a-model)
- [Troubleshooting](#troubleshooting)
- [FAQ](#faq)
- [Uninstall](#uninstall)
- [Contributing](#contributing)
- [License](#license)

---

## Why WhisperKey

| | |
|---|---|
| 🔒 **Private by design** | Audio is transcribed locally with [faster-whisper](https://github.com/SYSTRAN/faster-whisper). Nothing is uploaded — no cloud, no account, no telemetry. |
| ⚡ **Fast** | The model stays warm in memory, so dictation is near-instant on a GPU and very usable on CPU. |
| 🧠 **Resource-aware** | Lazy model loading, idle auto-unload, and a one-click *Stop* that frees **all** memory. Built for people who also train/run ML on the same box. |
| 🐧 **Works everywhere on Linux** | A kernel-level (evdev) hotkey works under **both Wayland and X11** — no per-app setup. |
| 🛠️ **No PyTorch required** | Runs on the lean `ctranslate2` engine. A clean CPU box needs only a handful of small wheels. |

## Features

- **Push-to-talk dictation** with a configurable hotkey or 2-key chord (default **Left&nbsp;Ctrl&nbsp;+&nbsp;Left&nbsp;Super**).
- **GPU *or* CPU**, with automatic hardware detection and a graceful **CUDA → CPU fallback** if drivers are missing.
- **System-tray control**: Start/Stop, Load/Unload model, Pause/Resume, switch idle mode, live VRAM/RAM readout, copy-last-transcription, open log, edit config.
- **Spoken commands** — say "new line", "comma", "period", etc. Fully configurable text substitutions.
- **Four injection modes** — auto-paste (Ctrl+V), typing (key-by-key), clipboard-only, or notify-only.
- **Hallucination filtering** for silence/noise via voice-activity detection (VAD).
- **Hotplug-safe** — automatically re-binds the hotkey when you plug in a new keyboard.
- **Clean install/uninstall** with hardware-tuned defaults and a built-in environment checker.

## How it works

Two small processes talk over a Unix socket in `$XDG_RUNTIME_DIR`:

```
 ┌─────────────────┐   socket: load / unload / pause / mode / status   ┌──────────────────────────┐
 │   tray.py       │ ────────────────────────────────────────────────▶ │   main.py  (the worker)  │
 │  (controller)   │                                                    │                          │
 │                 │   systemctl --user start / stop / restart          │  ├─ evdev hotkey (W+X11) │
 │  • pystray icon │ ────────────────────────────────────────────────▶ │  ├─ sounddevice mic cap. │
 │  • 0 VRAM, ~40MB│                                                    │  ├─ faster-whisper/ct2   │
 └─────────────────┘                                                    │  └─ clipboard + paste    │
                                                                        └──────────────────────────┘
```

The **tray never imports the ML stack**, so it stays tiny and holds zero GPU memory. The
**worker** owns the model, hotkey, and audio, and exposes a tiny control socket so the tray
(or the `--ctl` CLI) can drive it live. Both run as **systemd user services**.

## Quick start

```bash
git clone https://github.com/Rommelmith/whisperkey.git
cd whisperkey
./install.sh
```

The installer detects your hardware and session, creates a dedicated virtualenv, installs
system + Python dependencies, writes a hardware-appropriate config, and enables the systemd
user services. Use `./install.sh --yes` for a non-interactive install with smart defaults.

> [!IMPORTANT]
> If the installer added you to the **`input` group**, **log out and back in once** before the
> hotkey will work. This lets WhisperKey read the keyboard without root.

For a manual install, distro-specific notes, GPU/CUDA setup, and verification steps, see
**[INSTALL.md](INSTALL.md)**.

## Usage

1. **Hold** your hotkey (default **Left Ctrl + Left Super**) and speak.
2. **Release** to transcribe — the text is pasted into the focused window.
3. Press **Esc** while holding to cancel.

Control it from the **tray icon**, or from the CLI:

```bash
.venv/bin/python main.py --ctl status      # JSON status of the running worker
.venv/bin/python main.py --ctl load         # load the model into VRAM/RAM
.venv/bin/python main.py --ctl unload        # free it
.venv/bin/python main.py --ctl pause         # disable the hotkey
.venv/bin/python main.py --ctl resume
.venv/bin/python main.py --ctl mode low      # performance | balanced | low
.venv/bin/python main.py --list-keys         # list every evdev key name for hotkeys
```

Manage the services directly with systemd:

```bash
systemctl --user status  whisperkey.service        # the worker
systemctl --user restart whisperkey-tray.service   # the tray
journalctl --user -u whisperkey.service -f         # live worker logs
```

## The tray menu

The icon **colour** reflects state at a glance:

| Colour | Meaning |
|---|---|
| ⚪ **Grey** | Worker stopped |
| 🔵 **Blue** | Running, model unloaded (0 VRAM, armed) |
| 🟢 **Green** | Running, model loaded |
| 🟠 **Amber** | Paused (hotkey disabled) |
| 🔴 **Red** | Recording |

From the menu you can **Start/Stop/Restart** the worker, **Load/Unload** the model,
**Pause/Resume** the hotkey, pick an **idle mode**, **copy the last transcription**, and
**open the log** or **edit the config**.

> [!NOTE]
> On GNOME the tray icon needs the **AppIndicator** extension
> ([ubuntu-appindicators](https://extensions.gnome.org/extension/615/appindicator-support/)).
> KDE and most other desktops show it natively.

## Configuration

Settings live in `~/.config/whisperkey/config.json` (created by the installer, editable from
the tray). Restart the worker after editing: `systemctl --user restart whisperkey.service`.

| Key | Meaning |
|---|---|
| `model` | `tiny` · `base` · `small` · `medium` · `large-v3-turbo` · `large-v3` |
| `device` | `cuda` or `cpu` (auto-falls back to `cpu` if CUDA is unavailable) |
| `compute_type` | `int8` (CPU) · `int8_float16` / `float16` (GPU) |
| `cpu_threads` | CPU inference threads (installer defaults to half your cores) |
| `hotkey` | evdev key names, e.g. `["KEY_LEFTCTRL","KEY_LEFTMETA"]` — run `main.py --list-keys` |
| `grab_hotkey` | `true` to swallow the keys so they don't reach other apps (default `false`) |
| `language` | ISO code, e.g. `"en"` |
| `beam_size` | Decoding beam width (`1` is fastest) |
| `vad_filter` | `true` enables voice-activity detection (drops silence/hallucinations) |
| `idle_mode` | `performance` (always loaded) · `balanced` (unload when idle) · `low` (unload after each use) |
| `model_idle_unload_seconds` | Idle timeout for `balanced` mode |
| `preload_on_start` | `false` = armed but 0 memory until first dictation (**recommended**) |
| `injection_mode` | `auto-paste` · `typing` · `clipboard-only` · `notify-only` |
| `paste_delay_ms` | Delay before simulating Ctrl+V (gives the compositor time to register the clipboard) |
| `command_substitutions` | spoken phrase → text, e.g. `{"new line": "\n", "comma": ","}` |

## Choosing a model

| Model | Good for | Notes |
|---|---|---|
| `tiny` / `base` | CPU, low RAM | Real-time on most CPUs; lower accuracy |
| `small` | CPU (16&nbsp;GB+) or low-VRAM GPU | The sweet spot for accuracy vs. speed on CPU |
| `medium` | GPU (≈5&nbsp;GB VRAM) | Heavy on CPU |
| `large-v3-turbo` | GPU (≈10&nbsp;GB VRAM) | Best accuracy-to-speed on a capable GPU |
| `large-v3` | GPU, max accuracy | Slowest |

The installer picks a sensible default from your detected VRAM (or RAM on CPU). You can change
`model` in the config at any time — it's downloaded automatically on first use.

## Troubleshooting

Run the built-in checker first — it's hardware- and session-aware:

```bash
.venv/bin/python check_env.py             # quick checks
.venv/bin/python check_env.py --test-model  # also load the configured model
```

| Symptom | Fix |
|---|---|
| **Hotkey does nothing** | You're probably not in the `input` group yet, or haven't logged out/in since being added. `check_env.py` confirms. |
| **No tray icon (GNOME)** | Enable the AppIndicator extension, then `systemctl --user restart whisperkey-tray.service`. |
| **Auto-paste doesn't paste (Wayland)** | The ydotool daemon must be running: `systemctl --user status whisperkey-ydotoold` (or the distro's `ydotool` service). Text is always left on the clipboard as a fallback. |
| **Auto-paste doesn't paste (X11)** | Needs `xdotool` installed. |
| **GPU model fails to load** | It auto-falls back to CPU. Run `check_env.py --test-model` to see why (usually missing cuDNN). |
| **Anything else** | Check the log: `~/.local/state/whisperkey/whisperkey.log` (or *Open log* in the tray). |

## FAQ

**Does any audio leave my machine?**
No. Transcription is 100% local. The only network access is a one-time model download from
Hugging Face on first use.

**Do I need an NVIDIA GPU?**
No. CPU-only works out of the box (`tiny`/`base`/`small` are very usable). A GPU just makes
larger, more accurate models fast.

**Does it need PyTorch?**
No. faster-whisper uses the lean `ctranslate2` engine — no multi-gigabyte torch install.

**Wayland or X11?**
Both. The hotkey is read at the kernel level (evdev), and the paste backend is chosen per
session (`wl-clipboard`+`ydotool` on Wayland, `xclip`+`xdotool` on X11).

**Will it hog my GPU while I'm training models?**
Only if you tell it to. With `preload_on_start: false` and `balanced`/`low` idle mode, it holds
**zero VRAM** until you dictate and releases it afterwards. *Stop* in the tray frees everything.

## Uninstall

```bash
./uninstall.sh
```

Removes the systemd user services and (optionally) your config and venv. It leaves apt packages
and your `input`-group membership untouched.

## Contributing

Issues and PRs are welcome! Run the tests with:

```bash
.venv/bin/python -m unittest discover -s tests
```

Please keep the **tray free of any ML imports** (it must stay lightweight and hold no VRAM) and
prefer **lazy GPU loading + explicit memory release** — that resource-frugality is a core goal of
the project.

## License

[GPL-3.0](LICENSE) © Rommelmith
