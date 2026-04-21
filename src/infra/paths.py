"""Centralized filesystem paths used across Ghost.

Before the refactor these constants were redeclared in every module that
touched disk (`history.py`, `config.py`, `main.py`, `logging_config.py`).
Having one source of truth prevents accidental divergence and makes the
`~/.ghost/` contract easy to audit.
"""
from __future__ import annotations

from pathlib import Path

USER_DATA: Path = Path.home() / ".ghost"
"""Root directory for all user-local state. Never deleted on update."""

LOG_FILE: Path = USER_DATA / "ghost.log"
"""Main application log (RotatingFileHandler target)."""

UPDATER_LOG_FILE: Path = USER_DATA / "updater.log"
"""Update-flow log. Captures the PowerShell helper transcript."""

CONFIG_FILE: Path = USER_DATA / "config.json"
"""User preferences (OpenAI key, selected model)."""

HISTORY_FILE: Path = USER_DATA / "history.json"
"""Persisted conversations."""

WEBVIEW_CACHE: Path = USER_DATA / "webview-cache"
"""Pinned WebView2 UserDataFolder (shared across sessions for warm start)."""


def ensure_user_data() -> Path:
    """Create the ~/.ghost directory if missing. Idempotent."""
    USER_DATA.mkdir(parents=True, exist_ok=True)
    return USER_DATA
