"""Markdown-related pure parsing helpers (code block extraction)."""
from __future__ import annotations

import re

CODE_BLOCK_RE = re.compile(r"```([a-zA-Z0-9_+\-]*)\n(.*?)```", re.DOTALL)


def extract_code_blocks(text: str) -> list[tuple[str, str]]:
    """Return list of (language, code) tuples from markdown code fences."""
    return [(lang.strip(), code.rstrip()) for lang, code in CODE_BLOCK_RE.findall(text)]


def strip_code_blocks(text: str) -> str:
    """Return text with code blocks replaced by a placeholder."""
    return CODE_BLOCK_RE.sub("[código abaixo]", text).strip()


def pick_main_code(blocks: list[tuple[str, str]]) -> tuple[str, str] | None:
    """Return the largest code block, assumed to be the main answer."""
    if not blocks:
        return None
    return max(blocks, key=lambda b: len(b[1]))
