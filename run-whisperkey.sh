#!/usr/bin/env bash
# Launches the WhisperKey worker using the project's own venv, resolved relative
# to this script so it works wherever the repo is cloned.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# Prefer a repo-local venv; fall back to whatever python3 is on PATH.
if [[ -x "$APP_DIR/.venv/bin/python" ]]; then
    PYTHON="$APP_DIR/.venv/bin/python"
else
    PYTHON="$(command -v python3)"
fi

exec "$PYTHON" "$APP_DIR/main.py" "$@"
