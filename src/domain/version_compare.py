"""Version tuple parser + comparator extracted from updater.py.

Pure function — no network, no SDK. Easy to unit-test.
"""
from __future__ import annotations

import re


def parse_version(tag: str) -> tuple[int, ...]:
    """Turn 'v1.2.3' / '1.2.3-beta' into a sortable tuple.

    Non-numeric segments are skipped. Always returns at least one element.
    """
    nums = re.findall(r"\d+", tag or "")
    if not nums:
        return (0,)
    return tuple(int(n) for n in nums[:4])


def is_newer(candidate: str, current: str) -> bool:
    """True if `candidate` is a higher version than `current`."""
    return parse_version(candidate) > parse_version(current)
