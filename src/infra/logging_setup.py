"""Logging bootstrap — consolidated entry point for stdlib `logging`.

This is the file formerly at `src/logging_config.py`. The original path is
preserved as a shim so existing imports continue to work.

Goals:
  * One rotating file handler pointed at ~/.ghost/ghost.log
  * One stderr stream handler
  * `get_logger(__name__)` pattern everywhere
  * Noisy libraries (urllib3, httpx, openai, PIL, httpcore) clamped to WARNING
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

from .paths import LOG_FILE, ensure_user_data

_DEFAULT_FORMAT = "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s"
_DATE_FORMAT = "%H:%M:%S"

_configured = False


def _parse_level(value: str | int | None) -> int:
    if value is None:
        return logging.INFO
    if isinstance(value, int):
        return value
    v = str(value).strip().upper()
    if v.isdigit():
        return int(v)
    return getattr(logging, v, logging.INFO)


def configure(level: int | str | None = None, log_file: Path | None = None) -> None:
    """Configure the root logger. Idempotent; subsequent calls are no-ops."""
    global _configured
    if _configured:
        return

    lvl = _parse_level(level)

    root = logging.getLogger()
    root.setLevel(lvl)

    # Flush any handlers a lib (pywebview, etc.) installed at import time.
    for h in list(root.handlers):
        root.removeHandler(h)

    formatter = logging.Formatter(_DEFAULT_FORMAT, datefmt=_DATE_FORMAT)

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    stream.setLevel(lvl)
    root.addHandler(stream)

    target_file = log_file or LOG_FILE
    try:
        ensure_user_data()
        file_handler = RotatingFileHandler(
            target_file, maxBytes=2 * 1024 * 1024,
            backupCount=3, encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(lvl)
        root.addHandler(file_handler)
    except Exception as exc:
        root.warning("file log disabled: %s", exc)

    for noisy in ("urllib3", "httpx", "openai", "PIL", "httpcore"):
        logging.getLogger(noisy).setLevel(max(lvl, logging.WARNING))

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Main factory. Ensures configure() has run before returning a logger."""
    if not _configured:
        configure(os.environ.get("GHOST_LOG_LEVEL"))
    return logging.getLogger(name)
