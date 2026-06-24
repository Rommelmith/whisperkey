#!/usr/bin/env bash
# install.sh — WhisperKey installer
#
# Detects your hardware and session, sets up a dedicated Python venv, installs
# system + Python dependencies, writes a hardware-appropriate config, and installs
# the systemd user services (worker + tray). Works on GPU and CPU-only machines,
# on Wayland and X11. Every step explains itself and asks before doing anything
# that needs sudo.
#
# Usage:  ./install.sh            (interactive)
#         ./install.sh --yes      (accept all defaults, non-interactive)

set -euo pipefail

# ── flags ──────────────────────────────────────────────────────────────────────
ASSUME_YES=0
[[ "${1:-}" == "--yes" || "${1:-}" == "-y" ]] && ASSUME_YES=1

# ── colours ──────────────────────────────────────────────────────────────────────
BOLD="\033[1m"; RESET="\033[0m"
GREEN="\033[92m"; YELLOW="\033[93m"; RED="\033[91m"; CYAN="\033[96m"
info()  { echo -e "${CYAN}${BOLD}[INFO]${RESET}  $*"; }
ok()    { echo -e "${GREEN}${BOLD}[ OK ]${RESET}  $*"; }
warn()  { echo -e "${YELLOW}${BOLD}[WARN]${RESET}  $*"; }
die()   { echo -e "${RED}${BOLD}[ERR ]${RESET}  $*"; exit 1; }
hr()    { printf '%.0s─' {1..60}; echo; }

ask() {  # ask "prompt" [default y|n] -> 0 yes / 1 no
    local prompt="$1" default="${2:-y}"
    (( ASSUME_YES )) && { [[ "$default" == "n" ]] && return 1 || return 0; }
    local options="[Y/n]"; [[ "$default" == "n" ]] && options="[y/N]"
    while true; do
        read -rp "$(echo -e "${BOLD}$prompt${RESET} $options ") " ans
        ans="${ans:-$default}"
        case "${ans,,}" in
            y|yes) return 0 ;; n|no) return 1 ;;
            *) echo "  Please type y or n." ;;
        esac
    done
}

ask_val() {  # ask_val "prompt" "default" -> echoes value
    local prompt="$1" default="$2" val
    (( ASSUME_YES )) && { echo "$default"; return; }
    echo -en "${BOLD}$prompt${RESET} [${default}] " >/dev/tty
    read -r val </dev/tty
    echo "${val:-$default}"
}

# ── locations & environment ───────────────────────────────────────────────────
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_PATH="$APP_DIR/.venv"
CONFIG_DIR="$HOME/.config/whisperkey"
CONFIG_FILE="$CONFIG_DIR/config.json"
UNIT_DIR="$HOME/.config/systemd/user"

RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
SESSION_TYPE="${XDG_SESSION_TYPE:-wayland}"
WAYLAND_VAL="${WAYLAND_DISPLAY:-wayland-0}"
DISPLAY_VAL="${DISPLAY:-:0}"

echo
echo -e "${BOLD}${CYAN}╔══════════════════════════════════════╗${RESET}"
echo -e "${BOLD}${CYAN}║          WhisperKey Installer         ║${RESET}"
echo -e "${BOLD}${CYAN}║   Local voice dictation for Linux     ║${RESET}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════╝${RESET}"
echo

# ── step 1: detect hardware & session ─────────────────────────────────────────
hr; info "Step 1 — Detecting hardware & session"; hr
DISTRO=$(. /etc/os-release 2>/dev/null && echo "$PRETTY_NAME" || echo "Unknown Linux")
CPU_CORES=$(nproc)
RAM_GB=$(awk '/MemTotal/ {printf "%.0f", $2/1024/1024}' /proc/meminfo)
GPU_NAME="None"; VRAM_GB=0
if command -v nvidia-smi &>/dev/null; then
    GPU_NAME=$(nvidia-smi --query-gpu=name --format=csv,noheader 2>/dev/null | head -1 || echo "None")
    VRAM_MIB=$(nvidia-smi --query-gpu=memory.total --format=csv,noheader 2>/dev/null | head -1 | tr -dc '0-9' || echo 0)
    VRAM_GB=$(( ${VRAM_MIB:-0} / 1024 ))
fi
echo
printf "  %-16s %s\n" "Distro:"  "$DISTRO"
printf "  %-16s %s cores, %s GB RAM\n" "CPU:" "$CPU_CORES" "$RAM_GB"
printf "  %-16s %s%s\n" "GPU:" "$GPU_NAME" "$([[ "$GPU_NAME" != "None" ]] && echo " (${VRAM_GB} GB VRAM)")"
printf "  %-16s %s\n" "Session:" "$SESSION_TYPE"
echo

# ── step 2: pick model / device ───────────────────────────────────────────────
hr; info "Step 2 — Model & device"; hr; echo
if [[ "$GPU_NAME" != "None" && "$VRAM_GB" -ge 1 ]]; then
    DEVICE="cuda"; COMPUTE_TYPE="int8_float16"
    if   (( VRAM_GB >= 10 )); then REC_MODEL="large-v3-turbo"
    elif (( VRAM_GB >= 5  )); then REC_MODEL="medium"
    elif (( VRAM_GB >= 2  )); then REC_MODEL="small"
    else                           REC_MODEL="base"; fi
    REASON="NVIDIA GPU with ${VRAM_GB} GB VRAM"
else
    DEVICE="cpu"; COMPUTE_TYPE="int8"
    if   (( RAM_GB >= 16 )); then REC_MODEL="small"
    elif (( RAM_GB >= 8  )); then REC_MODEL="base"
    else                          REC_MODEL="tiny"; fi
    REASON="CPU-only (${RAM_GB} GB RAM) — int8 keeps it fast and light"
fi
echo -e "  Recommended: ${BOLD}$REC_MODEL${RESET} on ${BOLD}$DEVICE${RESET} (${BOLD}$COMPUTE_TYPE${RESET})"
echo -e "  Reason: $REASON"
echo "  Models by accuracy (and cost):  tiny < base < small < medium < large-v3-turbo < large-v3"
echo
if ask "  Use the recommended model ($REC_MODEL)?"; then
    MODEL="$REC_MODEL"
else
    MODEL=$(ask_val "  Model name" "$REC_MODEL")
    COMPUTE_TYPE=$(ask_val "  Compute type" "$COMPUTE_TYPE")
fi
# CPU thread count: half the cores is a good latency/throughput balance.
CPU_THREADS=$(( CPU_CORES / 2 )); (( CPU_THREADS < 1 )) && CPU_THREADS=1
ok "Model: $MODEL  device: $DEVICE  compute: $COMPUTE_TYPE  cpu_threads: $CPU_THREADS"

# ── step 3: idle mode ─────────────────────────────────────────────────────────
hr; info "Step 3 — Idle / memory management"; hr; echo
echo "  balanced     — unload the model after a few minutes idle (default)"
echo "  performance  — keep it loaded always (instant, holds memory)"
echo "  low          — unload after every dictation (minimum memory, ~2-5s per use)"
echo
IDLE_MODE=$(ask_val "  Idle mode (balanced/performance/low)" "balanced")
IDLE_SECONDS=300
[[ "$IDLE_MODE" == "balanced" ]] && IDLE_SECONDS=$(ask_val "  Unload after how many seconds idle" "300")
ok "Idle mode: $IDLE_MODE (${IDLE_SECONDS}s)"

# ── step 4: hotkey ────────────────────────────────────────────────────────────
hr; info "Step 4 — Push-to-talk hotkey"; hr; echo
echo "  Hold the hotkey to dictate, release to transcribe. Default is a 2-key chord."
echo "  Common single keys: KEY_RIGHTCTRL, KEY_RIGHTALT, KEY_PAUSE, KEY_F13"
echo "  (Run 'python main.py --list-keys' for every name.)"
echo
HOTKEY=$(ask_val "  Hotkey (one name, or two comma-separated for a chord)" "KEY_LEFTCTRL,KEY_LEFTMETA")
# Build a JSON array from the comma list.
HOTKEY_JSON=$(python3 -c "import json,sys; print(json.dumps([k.strip() for k in sys.argv[1].split(',') if k.strip()]))" "$HOTKEY")
ok "Hotkey: $HOTKEY_JSON"

# ── step 5: injection mode ────────────────────────────────────────────────────
hr; info "Step 5 — Text injection"; hr; echo
echo "  auto-paste     — copy + simulate Ctrl+V (needs ydotool on Wayland / xdotool on X11)"
echo "  typing         — type the text key-by-key (needs ydotool on Wayland / xdotool on X11)"
echo "  clipboard-only — copy only; you press Ctrl+V yourself"
echo "  notify-only    — just show a notification"
echo
INJECTION_MODE=$(ask_val "  Injection mode" "auto-paste")
ok "Injection mode: $INJECTION_MODE"

# ── step 6: system packages ───────────────────────────────────────────────────
hr; info "Step 6 — System packages"; hr; echo

# Package names differ across distro families, so pick them from the detected
# package manager. CLIP_PKGS is the same on every family; APPIND_PKGS lists tray
# candidates in order (we install the first that works). We install tolerantly so
# one unavailable package can't abort the whole run across the spread of versions.
if [[ "$SESSION_TYPE" == "wayland" ]]; then CLIP_PKGS="wl-clipboard ydotool"; else CLIP_PKGS="xclip xdotool"; fi
PKG_MGR=""; PKG_UPDATE=""; PKG_INSTALL=""; CORE_PKGS=""; APPIND_PKGS=""
if command -v apt-get &>/dev/null; then
    PKG_MGR="apt"
    PKG_UPDATE="sudo apt-get update -qq"
    PKG_INSTALL="sudo apt-get install -y"
    CORE_PKGS="build-essential portaudio19-dev python3-dev python3-venv python3-gi libnotify-bin $CLIP_PKGS"
    APPIND_PKGS="gir1.2-ayatanaappindicator3-0.1 gir1.2-appindicator3-0.1"
elif command -v dnf &>/dev/null; then
    PKG_MGR="dnf"
    PKG_INSTALL="sudo dnf install -y"
    CORE_PKGS="gcc portaudio-devel python3-devel python3-gobject libnotify $CLIP_PKGS"
    APPIND_PKGS="libayatana-appindicator-gtk3 libappindicator-gtk3"
elif command -v pacman &>/dev/null; then
    PKG_MGR="pacman"
    PKG_INSTALL="sudo pacman -S --needed --noconfirm"
    CORE_PKGS="base-devel portaudio python-gobject libnotify $CLIP_PKGS"
    APPIND_PKGS="libayatana-appindicator"
elif command -v zypper &>/dev/null; then
    PKG_MGR="zypper"
    PKG_INSTALL="sudo zypper install -y"
    CORE_PKGS="gcc portaudio-devel python3-devel python3-gobject libnotify-tools $CLIP_PKGS"
    APPIND_PKGS="libayatana-appindicator3-1 typelib-1_0-AyatanaAppIndicator3-0_1"
fi

# Tolerant install: try the whole list at once (fast path); if that fails on any
# package, retry one-by-one so the rest still get installed.
pm_install() {  # pm_install <space-separated packages>
    $PKG_INSTALL $1 >/dev/null 2>&1 && return 0
    local p rc=1
    for p in $1; do
        if $PKG_INSTALL "$p" >/dev/null 2>&1; then ok "installed $p"; rc=0
        else warn "could not install '$p' (skipping)"; fi
    done
    return $rc
}

if [[ -z "$PKG_MGR" ]]; then
    warn "No supported package manager found (apt/dnf/pacman/zypper)."
    warn "Install the equivalents of: $CORE_PKGS  + a GTK3 AppIndicator typelib."
else
    echo "  Manager:  $PKG_MGR"
    echo "  Needed:   compiler tools, PortAudio (mic), Python dev/venv, PyGObject,"
    echo "            libnotify, AppIndicator (tray), and $SESSION_TYPE clipboard/paste tools."
    echo "  Packages: $CORE_PKGS"
    echo "  Tray:     $APPIND_PKGS  (first available)"
    echo
    if ask "  Install system packages now? (sudo)"; then
        if [[ -n "$PKG_UPDATE" ]]; then
            $PKG_UPDATE || warn "package index update failed — continuing."
        fi
        pm_install "$CORE_PKGS" && ok "Core packages installed." \
                                || warn "Some core packages were skipped — check the warnings above."
        appind_ok=0
        for pkg in $APPIND_PKGS; do
            if $PKG_INSTALL "$pkg" >/dev/null 2>&1; then ok "AppIndicator: installed $pkg"; appind_ok=1; break; fi
        done
        (( appind_ok )) || warn "No AppIndicator package installed — the tray icon may not appear (the app still works)."
    else
        warn "Skipped — install these manually or the app/tray may not work."
    fi
fi

# ── step 7: Python venv + packages ────────────────────────────────────────────
hr; info "Step 7 — Python environment"; hr; echo
echo "  Creating a dedicated venv at: $VENV_PATH"
if [[ ! -d "$VENV_PATH" ]]; then
    python3 -m venv "$VENV_PATH"
fi
if ask "  Install Python dependencies into the venv?"; then
    "$VENV_PATH/bin/pip" install --upgrade pip -q
    "$VENV_PATH/bin/pip" install -r "$APP_DIR/requirements.txt"
    ok "Python dependencies installed."
else
    warn "Skipped — run: $VENV_PATH/bin/pip install -r $APP_DIR/requirements.txt"
fi

# ── step 8: input group (evdev hotkey) ────────────────────────────────────────
hr; info "Step 8 — Keyboard access (input group)"; hr; echo
echo "  WhisperKey reads the hotkey via /dev/input (works on Wayland). This needs"
echo "  your user to be in the 'input' group. You must log out/in after adding it."
echo
INPUT_GROUP_CHANGED=0
INPUT_ACCESS_READY=0
if ! getent group input >/dev/null; then
    warn "The 'input' group does not exist on this system."
    warn "That is common in containers/Codespaces; real desktop installs usually have it."
    warn "Skipping keyboard group setup. The hotkey needs /dev/input access to work."
elif id -nG "$USER" | grep -qw input; then
    INPUT_ACCESS_READY=1
    ok "Already in 'input' group."
else
    if ask "  Add $USER to the 'input' group? (sudo)"; then
        if sudo usermod -aG input "$USER"; then
            INPUT_GROUP_CHANGED=1
            warn "Added — LOG OUT and back in before the hotkey will work."
        else
            warn "Could not add $USER to the 'input' group. The hotkey will not work until this is fixed."
        fi
    else
        warn "Skipped — the hotkey will not work without 'input' group membership."
    fi
fi

# ── step 9: ydotoold (Wayland auto-paste / typing) ────────────────────────────
# Both auto-paste (ydotool key ctrl+v) and typing (ydotool type) drive ydotool,
# which needs its daemon running on Wayland.
if [[ "$SESSION_TYPE" == "wayland" && ( "$INJECTION_MODE" == "auto-paste" || "$INJECTION_MODE" == "typing" ) ]]; then
    hr; info "Step 9 — ydotoold daemon (Wayland $INJECTION_MODE)"; hr; echo
    echo "  ydotool needs its daemon running to inject keystrokes on Wayland."
    if ask "  Set up ydotoold as a user service?"; then
        mkdir -p "$UNIT_DIR"
        # Older WhisperKey installs created a plain "ydotoold.service". A second
        # daemon fighting over /tmp/.ydotool_socket makes paste fail intermittently,
        # so retire the legacy unit before we (re)establish a single daemon.
        if [[ -f "$UNIT_DIR/ydotoold.service" ]]; then
            systemctl --user disable --now ydotoold.service 2>/dev/null || true
            rm -f "$UNIT_DIR/ydotoold.service"
            systemctl --user daemon-reload
        fi
        if systemctl --user list-unit-files ydotool.service --no-legend 2>/dev/null | awk '{print $1}' | grep -qx ydotool.service; then
            systemctl --user enable --now ydotool.service || warn "Could not start distro ydotool.service yet."
            ok "Using distro ydotool.service."
        else
            cat > "$UNIT_DIR/whisperkey-ydotoold.service" <<EOF
[Unit]
Description=WhisperKey ydotoold helper daemon
After=graphical-session.target

[Service]
Type=simple
ExecStart=$(command -v ydotoold || echo /usr/bin/ydotoold)
Restart=on-failure

[Install]
WantedBy=default.target
EOF
            systemctl --user daemon-reload
            systemctl --user enable --now whisperkey-ydotoold.service || warn "Could not start whisperkey-ydotoold yet."
            ok "WhisperKey ydotoold service enabled."
        fi
    fi
fi

# ── step 10: write config ─────────────────────────────────────────────────────
hr; info "Step 10 — Writing config"; hr; echo
mkdir -p "$CONFIG_DIR"
PRELOAD="false"
cat > "$CONFIG_FILE" <<EOF
{
  "model": "$MODEL",
  "compute_type": "$COMPUTE_TYPE",
  "device": "$DEVICE",
  "cpu_threads": $CPU_THREADS,
  "hotkey": $HOTKEY_JSON,
  "grab_hotkey": false,
  "language": "en",
  "beam_size": 1,
  "vad_filter": true,
  "idle_mode": "$IDLE_MODE",
  "model_idle_unload_seconds": $IDLE_SECONDS,
  "preload_on_start": $PRELOAD,
  "injection_mode": "$INJECTION_MODE",
  "paste_delay_ms": 150,
  "command_substitutions": {
    "new line": "\n",
    "new paragraph": "\n\n",
    "comma": ",",
    "period": ".",
    "full stop": ".",
    "question mark": "?",
    "exclamation mark": "!",
    "open bracket": "(",
    "close bracket": ")"
  }
}
EOF
ok "Config written to $CONFIG_FILE"

# ── step 11: install systemd services ─────────────────────────────────────────
hr; info "Step 11 — systemd services (worker + tray)"; hr; echo
render_unit() {  # render_unit <template> <dest>
    sed -e "s#@APP_DIR@#$APP_DIR#g" \
        -e "s#@VENV@#$VENV_PATH#g" \
        -e "s#@RUNTIME_DIR@#$RUNTIME_DIR#g" \
        -e "s#@DISPLAY@#$DISPLAY_VAL#g" \
        -e "s#@WAYLAND_DISPLAY@#$WAYLAND_VAL#g" \
        -e "s#@SESSION_TYPE@#$SESSION_TYPE#g" \
        "$1" > "$2"
}
if ask "  Install and enable the worker + tray services?"; then
    mkdir -p "$UNIT_DIR"
    render_unit "$APP_DIR/systemd/whisperkey.service.in"               "$UNIT_DIR/whisperkey.service"
    render_unit "$APP_DIR/systemd/whisperkey-tray.service.in"          "$UNIT_DIR/whisperkey-tray.service"
    render_unit "$APP_DIR/systemd/whisperkey-input-refresh.service.in" "$UNIT_DIR/whisperkey-input-refresh.service"
    render_unit "$APP_DIR/systemd/whisperkey-input-refresh.path.in"    "$UNIT_DIR/whisperkey-input-refresh.path"
    chmod +x "$APP_DIR/run-whisperkey.sh"
    systemctl --user daemon-reload
    if (( INPUT_ACCESS_READY )); then
        systemctl --user enable --now whisperkey.service whisperkey-tray.service whisperkey-input-refresh.path
        ok "Services enabled. Look for the WhisperKey icon in your tray."
    else
        systemctl --user enable whisperkey.service whisperkey-tray.service whisperkey-input-refresh.path
        if (( INPUT_GROUP_CHANGED )); then
            warn "Services enabled but not started because your new 'input' group membership is not active yet."
            warn "Log out and back in, then start WhisperKey from the tray or run:"
            echo "       systemctl --user start whisperkey.service whisperkey-tray.service whisperkey-input-refresh.path"
        else
            warn "Services enabled but not started because keyboard input access is not ready."
            warn "Fix /dev/input permissions, then start WhisperKey from the tray or run:"
            echo "       systemctl --user start whisperkey.service whisperkey-tray.service whisperkey-input-refresh.path"
        fi
    fi
else
    warn "Skipped. Run the worker manually with: $APP_DIR/run-whisperkey.sh"
fi

# ── done ──────────────────────────────────────────────────────────────────────
hr
echo -e "${GREEN}${BOLD}Installation complete!${RESET}"
echo
echo "  • If you were just added to the 'input' group, LOG OUT and back in."
echo "  • Hold your hotkey and speak; release to transcribe."
echo "  • Control everything from the tray icon, or:  $VENV_PATH/bin/python $APP_DIR/main.py --ctl status"
echo "  • Sanity check anytime:  $VENV_PATH/bin/python $APP_DIR/check_env.py"
echo
