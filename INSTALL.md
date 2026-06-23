# Installing WhisperKey

A complete installation guide — the automatic installer, a manual step-by-step path,
distro-specific notes, GPU/CUDA setup, verification, and updating.

- [Requirements](#requirements)
- [Option A — Automatic install (recommended)](#option-a--automatic-install-recommended)
- [Option B — Manual install](#option-b--manual-install)
- [GPU / CUDA notes](#gpu--cuda-notes)
- [The `input` group (why log out/in)](#the-input-group-why-log-outin)
- [Wayland auto-paste: `ydotoold`](#wayland-auto-paste-ydotoold)
- [Verifying the install](#verifying-the-install)
- [Updating](#updating)
- [Uninstalling](#uninstalling)

---

## Requirements

- A modern Linux desktop. Developed on **Ubuntu/GNOME**; works on KDE and others.
- **Python 3.10+**.
- A microphone.
- **systemd** user services (standard on virtually every desktop distro).
- An **NVIDIA GPU is optional** — CPU-only works out of the box.
- For the **tray icon on GNOME**: the *AppIndicator* GNOME extension
  ([ubuntu-appindicators](https://extensions.gnome.org/extension/615/appindicator-support/)).
  KDE and most other desktops show it natively.

### System packages

The installer handles these for you on `apt` systems. They are listed here for manual
installs and non-Debian distros.

| Purpose | Debian/Ubuntu package(s) |
|---|---|
| Microphone capture (PortAudio) | `portaudio19-dev` |
| Python venv + headers | `python3-dev`, `python3-venv` |
| Tray icon (PyGObject + AppIndicator) | `python3-gi`, `gir1.2-ayatanaappindicator3-0.1` |
| Desktop notifications | `libnotify-bin` |
| **Wayland** clipboard + paste | `wl-clipboard`, `ydotool` |
| **X11** clipboard + paste | `xclip`, `xdotool` |

> **Fedora**: `portaudio-devel python3-devel python3-gobject libayatana-appindicator-gtk3 libnotify wl-clipboard ydotool` (or `xclip xdotool` on X11).
> **Arch**: `portaudio python-gobject libayatana-appindicator libnotify wl-clipboard ydotool` (or `xclip xdotool` on X11).

### Python packages

Installed into a dedicated venv from [`requirements.txt`](requirements.txt):
`faster-whisper`, `sounddevice`, `numpy`, `evdev`, `pystray`, `Pillow`, and `nvidia-ml-py`
(optional, GPU-only). **No PyTorch is required.**

---

## Option A — Automatic install (recommended)

```bash
git clone https://github.com/Rommelmith/whisperkey.git
cd whisperkey
./install.sh
```

The installer is interactive and explains every step. It will:

1. **Detect** your distro, CPU, RAM, GPU/VRAM, and session (Wayland/X11).
2. **Recommend a model + device** based on your hardware (you can override it).
3. Let you pick an **idle/memory mode** (`balanced` by default).
4. Let you set the **push-to-talk hotkey** (default `KEY_LEFTCTRL,KEY_LEFTMETA`).
5. Let you pick a **text-injection mode** (`auto-paste` by default).
6. **Install system packages** via `apt` (asks for `sudo`).
7. Create a **dedicated venv** at `./.venv` and install Python deps.
8. Offer to add you to the **`input` group** (needed for the hotkey).
9. On Wayland, optionally set up the **`ydotoold`** daemon for auto-paste.
10. Write your **config** to `~/.config/whisperkey/config.json`.
11. Render and enable the **systemd user services** (worker + tray + hotplug refresh).

### Non-interactive install

```bash
./install.sh --yes
```

Accepts all recommended defaults — handy for scripting or a fresh machine.

### After installing

- If you were just added to the `input` group, **log out and back in once**.
- Look for the **WhisperKey icon** in your system tray.
- Hold your hotkey and speak; release to transcribe.

---

## Option B — Manual install

For non-`apt` distros, or if you prefer to do it by hand.

### 1. Install system packages

Use the table in [Requirements](#system-packages) for your distro. Example (Ubuntu, Wayland):

```bash
sudo apt update
sudo apt install -y portaudio19-dev python3-dev python3-venv python3-gi \
  gir1.2-ayatanaappindicator3-0.1 libnotify-bin wl-clipboard ydotool
```

On X11, swap `wl-clipboard ydotool` for `xclip xdotool`.

### 2. Create the venv and install Python deps

```bash
cd whisperkey
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

### 3. Join the `input` group

```bash
sudo usermod -aG input "$USER"
# then LOG OUT and back in
```

### 4. Write a config

Create `~/.config/whisperkey/config.json`. A minimal CPU example:

```json
{
  "model": "small",
  "device": "cpu",
  "compute_type": "int8",
  "hotkey": ["KEY_LEFTCTRL", "KEY_LEFTMETA"],
  "idle_mode": "balanced",
  "model_idle_unload_seconds": 300,
  "preload_on_start": false,
  "injection_mode": "auto-paste"
}
```

See the [Configuration table](README.md#configuration) for every key. Any omitted key uses a
sensible default.

### 5. (Wayland only) Set up `ydotoold`

See [Wayland auto-paste](#wayland-auto-paste-ydotoold) below.

### 6. Install the systemd user services

The unit files are **templates** in `systemd/*.in` with `@PLACEHOLDERS@`. Render them with your
paths and session, then enable:

```bash
APP_DIR="$(pwd)"
VENV="$APP_DIR/.venv"
RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
UNIT_DIR="$HOME/.config/systemd/user"
mkdir -p "$UNIT_DIR"

for t in systemd/*.in; do
  dest="$UNIT_DIR/$(basename "${t%.in}")"
  sed -e "s#@APP_DIR@#$APP_DIR#g" \
      -e "s#@VENV@#$VENV#g" \
      -e "s#@RUNTIME_DIR@#$RUNTIME_DIR#g" \
      -e "s#@DISPLAY@#${DISPLAY:-:0}#g" \
      -e "s#@WAYLAND_DISPLAY@#${WAYLAND_DISPLAY:-wayland-0}#g" \
      -e "s#@SESSION_TYPE@#${XDG_SESSION_TYPE:-wayland}#g" \
      "$t" > "$dest"
done

chmod +x run-whisperkey.sh
systemctl --user daemon-reload
systemctl --user enable --now whisperkey.service whisperkey-tray.service whisperkey-input-refresh.path
```

> Prefer not to use systemd? Run the worker directly with `./run-whisperkey.sh` and the tray
> with `.venv/bin/python tray.py`.

---

## GPU / CUDA notes

WhisperKey runs on the **`ctranslate2`** engine (via faster-whisper), **not** PyTorch. For GPU
inference you need NVIDIA's CUDA + cuDNN runtime libraries available to `ctranslate2`.

- **It is safe to try.** If the config asks for `cuda` but the GPU/CUDA/cuDNN isn't usable, the
  worker logs a warning and **automatically falls back to CPU** (`compute_type=int8`) — the app
  still works.
- The recommended GPU compute type is `int8_float16` (good speed/quality/VRAM balance);
  `float16` is also available.
- To diagnose GPU issues, run:

  ```bash
  .venv/bin/python check_env.py --test-model
  ```

  It reports your GPU, the `ctranslate2` CUDA compute types it can see, and any model-load error
  (a missing cuDNN library is the most common cause).

> **Bleeding-edge GPUs (new architectures, recent CUDA):** the working
> `ctranslate2` + CUDA + cuDNN combination can be version-sensitive. If you already have a Python
> environment with a known-good GPU stack, point WhisperKey's `.venv` at it (it can be a symlink)
> rather than resolving fresh wheels.

---

## The `input` group (why log out/in)

The push-to-talk hotkey is read directly from `/dev/input` via **evdev**. This is what makes the
hotkey work identically on **Wayland and X11** without per-app configuration — but reading those
device files requires membership in the **`input`** group.

```bash
sudo usermod -aG input "$USER"
```

Group membership is only applied to **new login sessions**, so you must **log out and back in**
(a reboot also works) before the hotkey functions. Check with:

```bash
.venv/bin/python check_env.py        # confirms 'input' group + hotkey readiness
```

A `whisperkey-input-refresh.path` unit watches `/dev/input` and restarts the worker when you
hotplug a keyboard, so newly connected devices keep working.

---

## Wayland auto-paste: `ydotoold`

On Wayland, simulating **Ctrl+V** requires the `ydotool` daemon (`ydotoold`) to be running and
`/dev/uinput` to be writable. The automatic installer offers to create a `ydotoold` user service.
To do it manually:

```bash
cat > ~/.config/systemd/user/ydotoold.service <<'EOF'
[Unit]
Description=ydotoold — ydotool helper daemon
After=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/ydotoold
Restart=on-failure

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now ydotoold.service
sudo modprobe uinput     # if /dev/uinput is missing
```

If auto-paste can't reach `ydotoold`, the transcribed text is **always left on the clipboard**, so
you can paste manually with Ctrl+V. (On X11 this is handled by `xdotool` and needs no daemon.)

---

## Verifying the install

```bash
# Full environment check (hardware/session aware)
.venv/bin/python check_env.py

# Status of the running worker
.venv/bin/python main.py --ctl status

# Service state and live logs
systemctl --user status whisperkey.service whisperkey-tray.service
journalctl --user -u whisperkey.service -f
```

`check_env.py` prints a `[PASS]/[WARN]/[FAIL]` line per check and a final summary. Resolve any
`[FAIL]` items (and `[WARN]` items relevant to your setup) and you're ready to dictate.

---

## Updating

```bash
cd whisperkey
git pull
.venv/bin/pip install -r requirements.txt   # in case deps changed
systemctl --user restart whisperkey.service whisperkey-tray.service
```

If the systemd templates changed, re-run the render step from
[Manual install · step 6](#6-install-the-systemd-user-services) (or just re-run `./install.sh`).

---

## Uninstalling

```bash
./uninstall.sh
```

Removes the systemd user services and offers to delete your config and venv. It **leaves apt
packages and your `input`-group membership untouched** — remove those yourself if you want:

```bash
sudo gpasswd -d "$USER" input        # leave the input group (optional)
```
