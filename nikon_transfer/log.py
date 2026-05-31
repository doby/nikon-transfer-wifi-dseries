"""Logging configuration for nikon_transfer."""

import logging
import sys
from datetime import datetime
from pathlib import Path

_FMT_FILE    = "%(asctime)s.%(msecs)03d [%(levelname)-5s] %(name)s: %(message)s"
_FMT_CONSOLE = "[%(levelname)-5s] %(message)s"
_DATE_FMT    = "%Y-%m-%d %H:%M:%S"

logger = logging.getLogger("nikon_transfer")


def setup(
    debug: bool = False,
    log_file: Path | None = None,
    auto_log: bool = True,
) -> Path | None:
    """
    Configure root logger for nikon_transfer.

    - Console: INFO normally, DEBUG when debug=True.
    - File:    always DEBUG.  Uses log_file if given, otherwise auto-generates
               nikon_transfer_YYYYMMDD_HHMMSS.log in the current directory
               (skipped when auto_log=False and log_file is None).

    Returns the resolved log file path, or None if no file was opened.
    """
    logger.setLevel(logging.DEBUG)
    logger.handlers.clear()

    # ── Console handler ──────────────────────────────────────────────────────
    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG if debug else logging.INFO)
    console.setFormatter(logging.Formatter(_FMT_CONSOLE))
    logger.addHandler(console)

    # ── File handler ─────────────────────────────────────────────────────────
    resolved: Path | None = None
    if log_file is not None or auto_log:
        if log_file is None:
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = Path(f"nikon_transfer_{stamp}.log")
        log_file = log_file.expanduser().resolve()
        log_file.parent.mkdir(parents=True, exist_ok=True)

        fh = logging.FileHandler(log_file, encoding="utf-8")
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(logging.Formatter(_FMT_FILE, datefmt=_DATE_FMT))
        logger.addHandler(fh)
        resolved = log_file

    return resolved
