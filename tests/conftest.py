"""Shared fixtures for Ghost test suite.

The goal here is to isolate every test from the real ~/.ghost/ directory.
Both `history.py` and `config.py` read `Path.home()` at import time and build
module-level constants (HISTORY_DIR / HISTORY_FILE / USER_CONFIG_DIR /
USER_CONFIG_FILE). Patching `Path.home()` after import is useless, so we patch
those constants directly with monkeypatch.setattr.
"""
from __future__ import annotations

import pytest

from src import config as config_mod
from src import history as history_mod


@pytest.fixture
def tmp_ghost_home(tmp_path, monkeypatch):
    """Redirect history + user-config paths to a temporary .ghost dir.

    Returns the temporary .ghost directory path (not yet created — modules
    create it on first save).
    """
    ghost_dir = tmp_path / ".ghost"

    # history module
    monkeypatch.setattr(history_mod, "HISTORY_DIR", ghost_dir, raising=True)
    monkeypatch.setattr(
        history_mod, "HISTORY_FILE", ghost_dir / "history.json", raising=True
    )

    # config module
    monkeypatch.setattr(config_mod, "USER_CONFIG_DIR", ghost_dir, raising=True)
    monkeypatch.setattr(
        config_mod, "USER_CONFIG_FILE", ghost_dir / "config.json", raising=True
    )

    # Also scrub env vars so get_openai_key() fallback can't leak into tests.
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)

    return ghost_dir


@pytest.fixture
def sample_messages():
    """A minimal, realistic user+assistant exchange used by history tests."""
    return [
        {"role": "user", "text": "Como funciona o Alpine.js?"},
        {
            "role": "assistant",
            "text": "Alpine.js é um framework JS leve para reatividade inline.",
        },
        {"role": "user", "text": "Me dá um exemplo."},
        {"role": "assistant", "text": "```html\n<div x-data=...></div>\n```"},
    ]
