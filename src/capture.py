"""Backwards-compatible re-export of the moved low-level capture helpers.

Real implementation: `src.capture_pkg.screenshot`.
"""
from __future__ import annotations

from .capture_pkg.screenshot import (  # noqa: F401
    capture_fullscreen,
    capture_region,
    image_to_base64,
    image_to_data_url,
)
