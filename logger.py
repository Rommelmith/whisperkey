"""logger.py — structured log to ~/.local/state/whisperkey/whisperkey.log"""

import logging
import sys
from pathlib import Path

LOG_PATH = Path.home() / ".local" / "state" / "whisperkey" / "whisperkey.log"


def setup() -> logging.Logger:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)

    fmt = logging.Formatter("%(asctime)s %(levelname)-8s %(message)s", datefmt="%Y-%m-%d %H:%M:%S")

    file_handler = logging.FileHandler(LOG_PATH)
    file_handler.setFormatter(fmt)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(fmt)

    log = logging.getLogger("whisperkey")
    log.setLevel(logging.DEBUG)
    log.addHandler(file_handler)
    log.addHandler(console_handler)
    log.propagate = False
    return log


log = setup()
