"""Backwards-compatible re-export of the moved logging module.

Real implementation: `src.infra.logging_setup` + `src.infra.paths`.

Wholesale re-export of the new module, plus a compatibility `_log_dir`
helper that pre-refactor callers may reference directly.
"""
from __future__ import annotations

from pathlib import Path

from .infra import logging_setup as _src

for _name in dir(_src):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_src, _name)


def _log_dir() -> Path:
    """Pre-refactor helper preserved for compat. Returns ~/.ghost/."""
    from .infra.paths import USER_DATA
    return USER_DATA


del _name, _src
