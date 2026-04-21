"""Backwards-compatible re-export of the moved region picker.

Real implementation: `src.capture_pkg.region_picker`.
"""
from __future__ import annotations

from .capture_pkg.region_picker import select_region  # noqa: F401
