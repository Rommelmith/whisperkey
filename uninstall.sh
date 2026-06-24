#!/usr/bin/env bash
# uninstall.sh — remove WhisperKey's services and (optionally) its config + venv.
# Does NOT remove apt packages or your 'input' group membership.
set -euo pipefail

BOLD="\033[1m"; RESET="\033[0m"; GREEN="\033[92m"; YELLOW="\033[93m"
ok()   { echo -e "${GREEN}${BOLD}[ OK ]${RESET}  $*"; }
warn() { echo -e "${YELLOW}${BOLD}[WARN]${RESET}  $*"; }

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UNIT_DIR="$HOME/.config/systemd/user"
CONFIG_DIR="$HOME/.config/whisperkey"

ask() { read -rp "$(echo -e "${BOLD}$1${RESET} [y/N] ") " a; [[ "${a,,}" =~ ^y ]]; }

echo "Stopping and disabling WhisperKey services…"
for unit in whisperkey-tray.service whisperkey.service whisperkey-input-refresh.path whisperkey-input-refresh.service; do
    systemctl --user disable --now "$unit" 2>/dev/null || true
    rm -f "$UNIT_DIR/$unit"
done
systemctl --user daemon-reload
ok "Services removed."

if ask "Also remove the ydotoold user service WhisperKey created?"; then
    # whisperkey-ydotoold.service is the current name; ydotoold.service is the
    # legacy name from older installs. The distro's own ydotool.service is left
    # untouched (we didn't create it).
    for unit in whisperkey-ydotoold.service ydotoold.service; do
        systemctl --user disable --now "$unit" 2>/dev/null || true
        rm -f "$UNIT_DIR/$unit"
    done
    systemctl --user daemon-reload
    ok "ydotoold service removed."
fi

if ask "Remove config at $CONFIG_DIR?"; then
    rm -rf "$CONFIG_DIR"
    ok "Config removed."
fi

if ask "Remove the Python venv at $APP_DIR/.venv?"; then
    rm -rf "$APP_DIR/.venv"
    ok "venv removed."
fi

echo
echo "Done. The repo folder, apt packages, and your 'input' group membership were left as-is."
