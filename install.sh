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
echo "  clipboard-only — copy only; you press Ctrl+V yourself"
echo "  notify-only    — just show a notification"
echo
INJECTION_MODE=$(ask_val "  Injection mode" "auto-paste")
ok "Injection mode: $INJECTION_MODE"

# ── step 6: system packages ───────────────────────────────────────────────────
hr; info "Step 6 — System packages (apt)"; hr; echo
APT_PKGS="portaudio19-dev python3-dev python3-venv python3-gi gir1.2-ayatanaappindicator3-0.1 libnotify-bin"
if [[ "$SESSION_TYPE" == "wayland" ]]; then
    APT_PKGS="$APT_PKGS wl-clipboard ydotool"
else
    APT_PKGS="$APT_PKGS xclip xdotool"
fi
echo "  Needed: PortAudio (mic), Python dev/venv, PyGObject + AppIndicator (tray icon),"
echo "          and the clipboard/paste tools for your $SESSION_TYPE session."
echo "  Packages: $APT_PKGS"
echo
if command -v apt-get &>/dev/null; then
    if ask "  Install system packages now? (sudo)"; then
        sudo apt-get update -qq
        sudo apt-get install -y $APT_PKGS
        ok "System packages installed."
    else
        warn "Skipped — install these manually or the app/tray may not work."
    fi
else
    warn "Non-apt distro detected. Install the equivalents of: $APT_PKGS"
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
if id -nG "$USER" | grep -qw input; then
    ok "Already in 'input' group."
else
    if ask "  Add $USER to the 'input' group? (sudo)"; then
        sudo usermod -aG input "$USER"
        warn "Added — LOG OUT and back in before the hotkey will work."
    else
        warn "Skipped — the hotkey will not work without 'input' group membership."
    fi
fi

# ── step 9: ydotoold (Wayland auto-paste only) ────────────────────────────────
if [[ "$SESSION_TYPE" == "wayland" && "$INJECTION_MODE" == "auto-paste" ]]; then
    hr; info "Step 9 — ydotoold daemon (Wayland paste)"; hr; echo
    echo "  ydotool needs its daemon running to inject Ctrl+V on Wayland."
    if ask "  Set up ydotoold as a user service?"; then
        mkdir -p "$UNIT_DIR"
        cat > "$UNIT_DIR/ydotoold.service" <<EOF
[Unit]
Description=ydotoold — ydotool helper daemon
After=graphical-session.target

[Service]
Type=simple
ExecStart=$(command -v ydotoold || echo /usr/bin/ydotoold)
Restart=on-failure

[Install]
WantedBy=default.target
EOF
        systemctl --user daemon-reload
        systemctl --user enable --now ydotoold.service || warn "Could not start ydotoold yet."
        ok "ydotoold service enabled."
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
    systemctl --user enable --now whisperkey.service whisperkey-tray.service whisperkey-input-refresh.path
    ok "Services enabled. Look for the WhisperKey icon in your tray."
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
