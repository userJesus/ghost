"""Backwards-compatible re-export of the moved module.

Real implementation: `src.recording.meeting_processor`.

Wholesale re-export: every top-level name of the new module (including
private `_name` helpers and imported symbols) is copied into this
namespace, so any pre-refactor `from src.meeting_processor import X` keeps
working — whether X was public, private, or a re-imported symbol.
"""
from __future__ import annotations

from .recording import meeting_processor as _src

for _name in dir(_src):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_src, _name)

del _name, _src
