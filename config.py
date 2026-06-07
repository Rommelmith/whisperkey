"""config.py — load and save ~/.config/whisperkey/config.json

Key reconciliation
------------------
Earlier code read ``gpu_idle_mode`` / ``gpu_idle_minutes``, but the config the
installer/codex actually wrote uses ``idle_mode`` / ``model_idle_unload_seconds``
/ ``cpu_threads``. We standardize on the latter (seconds give finer control and
match the file on disk) and accept the old names as a fallback via ``load()`` so
nothing breaks for an old config.
"""

import json
from pathlib import Path

CONFIG_PATH = Path.home() / ".config" / "whisperkey" / "config.json"

DEFAULTS = {
    "model": "large-v3-turbo",
    "compute_type": "float16",
    "device": "cuda",
    "cpu_threads": 4,
    "hotkey": ["KEY_LEFTCTRL", "KEY_LEFTMETA"],
    "grab_hotkey": False,
    "language": "en",
    "beam_size": 1,
    "vad_filter": True,
    # Idle / VRAM management
    "idle_mode": "balanced",            # performance | balanced | low
    "model_idle_unload_seconds": 300,   # balanced: unload after this much idle
    "preload_on_start": False,          # resource-first: arm without loading VRAM
    # Text injection
    "injection_mode": "auto-paste",
    "paste_delay_ms": 150,
    "command_substitutions": {
        "new line": "\n",
        "newline": "\n",
        "new paragraph": "\n\n",
        "newparagraph": "\n\n",
        "comma": ",",
        "period": ".",
        "full stop": ".",
        "question mark": "?",
        "exclamation mark": "!",
        "open bracket": "(",
        "close bracket": ")",
    },
}

VALID_IDLE_MODES = ("performance", "balanced", "low")


def load() -> dict:
    if not CONFIG_PATH.exists():
        return DEFAULTS.copy()
    with open(CONFIG_PATH) as f:
        data = json.load(f)

    # Accept legacy key names so an old config keeps working.
    if "idle_mode" not in data and "gpu_idle_mode" in data:
        data["idle_mode"] = data["gpu_idle_mode"]
    if "model_idle_unload_seconds" not in data and "gpu_idle_minutes" in data:
        data["model_idle_unload_seconds"] = int(data["gpu_idle_minutes"]) * 60

    cfg = {**DEFAULTS, **data}

    if cfg["idle_mode"] not in VALID_IDLE_MODES:
        cfg["idle_mode"] = "balanced"
    return cfg


def save(cfg: dict) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, indent=2)
