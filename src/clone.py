"""Backwards-compatible re-export of the moved web cloner.

Real implementation: `src.cloner.web_cloner`.

External callers (`src.api`) historically did `from .clone import WebCloner, clones_dir`.
This shim preserves that import surface.
"""
from __future__ import annotations

from .cloner.web_cloner import WebCloner, clones_dir

__all__ = ["WebCloner", "clones_dir"]
