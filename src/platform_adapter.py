"""Backwards-compatible re-export of the moved platform adapter.

Real implementation: `src.platform.adapter`.
"""
from __future__ import annotations

from .platform.adapter import (  # noqa: F401
    PlatformAdapter,
    WindowsPlatform,
    get_platform,
)
