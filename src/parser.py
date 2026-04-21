"""Backwards-compatible re-export of the moved markdown parser."""
from __future__ import annotations

from .domain.markdown_parser import (
    CODE_BLOCK_RE,
    extract_code_blocks,
    pick_main_code,
    strip_code_blocks,
)

__all__ = ["CODE_BLOCK_RE", "extract_code_blocks", "strip_code_blocks", "pick_main_code"]
