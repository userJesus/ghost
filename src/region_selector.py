"""Backwards-compatible re-export of the moved module.

Real implementation: `src.capture_pkg.region_picker`.

Wholesale re-export: every top-level name of the new module (including
private `_name` helpers and imported symbols) is copied into this
namespace, so any pre-refactor `from src.region_selector import X` keeps
working — whether X was public, private, or a re-imported symbol.
"""
from __future__ import annotations

from .capture_pkg import region_picker as _src

for _name in dir(_src):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_src, _name)

del _name, _src
