"""Backwards-compatible re-export of the moved scroll-capture helpers.

Real implementation: `src.capture_pkg.scroll`.
"""
from __future__ import annotations

from .capture_pkg.scroll import (  # noqa: F401
    capture_monitor,
    list_monitors,
    scroll_and_capture,
    stitch_vertical,
)
