"""Backwards-compatible re-export of the moved OpenAI client.

Real implementation: `src.integrations.openai_client`.

Wholesale re-export: every top-level name of the new module (including
private `_name` helpers and imported symbols) is copied into this
namespace, so any pre-refactor `from src.gpt_client import X` keeps
working — whether X was public, private, or a re-imported symbol.

Fixes the v1.1.12 regression where `from src.gpt_client import _has_image`
raised ImportError because the explicit re-export list skipped underscore
names.
"""
from __future__ import annotations

from .integrations import openai_client as _src

for _name in dir(_src):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_src, _name)

del _name, _src
