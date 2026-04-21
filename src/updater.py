"""Backwards-compatible re-export of the moved update checker.

Real implementation: `src.services.update_service`.
`check()` and `check_async()` are preserved as module-level callables so any
external import path (tests, tooling, external scripts) keeps working.
"""
from __future__ import annotations

from .services.update_service import (
    UpdateInfo,
    UpdateService,
)
from .services.update_service import (
    _check as check,
)
from .services.update_service import (
    _check_async as check_async,
)

__all__ = ["UpdateInfo", "UpdateService", "check", "check_async"]
