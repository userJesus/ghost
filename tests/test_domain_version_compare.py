"""Tests for src/domain/version_compare.py — pure version parsing + comparison."""
from __future__ import annotations

import pytest

from src.domain.version_compare import is_newer, parse_version


class TestParseVersion:
    def test_basic_three_part(self):
        assert parse_version("1.2.3") == (1, 2, 3)

    def test_v_prefix_stripped(self):
        assert parse_version("v1.2.3") == (1, 2, 3)

    def test_pre_release_suffix(self):
        assert parse_version("1.2.3-beta") == (1, 2, 3)

    def test_four_part_capped_at_four(self):
        assert parse_version("1.2.3.4.5") == (1, 2, 3, 4)

    def test_empty_string_returns_zero(self):
        assert parse_version("") == (0,)

    def test_none_returns_zero(self):
        assert parse_version(None) == (0,)

    def test_no_digits_returns_zero(self):
        assert parse_version("abc") == (0,)

    def test_partial(self):
        assert parse_version("2") == (2,)


class TestIsNewer:
    """The updater's core decision logic — must never regress."""

    @pytest.mark.parametrize("candidate,current,expected", [
        # equal
        ("1.0.0", "1.0.0", False),
        ("1.1.10", "1.1.10", False),
        # patch bumps
        ("1.0.1", "1.0.0", True),
        ("1.1.11", "1.1.10", True),
        # downgrades
        ("1.1.9", "1.1.10", False),
        ("1.0.0", "1.1.0", False),
        # minor bumps
        ("1.2.0", "1.1.30", True),
        # major bumps
        ("2.0.0", "1.99.99", True),
        # the real-world walkthrough Ghost follows
        ("1.0.30", "1.0.29", True),
        ("1.1.0", "1.0.30", True),
    ])
    def test_compare(self, candidate, current, expected):
        assert is_newer(candidate, current) is expected, \
            f"is_newer({candidate!r}, {current!r}) expected {expected}"

    def test_v_prefix_handled(self):
        assert is_newer("v1.1.11", "v1.1.10") is True
