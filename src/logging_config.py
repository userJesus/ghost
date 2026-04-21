"""Backwards-compatible re-export of the moved logging module.

The real implementation lives at `src.infra.logging_setup`. This shim
preserves the pre-refactor import path for any external code, tests,
or PyInstaller hiddenimports that still reference `src.logging_config`.
"""
from __future__ import annotations

from .infra.logging_setup import configure, get_logger

__all__ = ["configure", "get_logger"]
