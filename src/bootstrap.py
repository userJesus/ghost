"""Startup helpers — log writer, native MessageBox, preflight, runtime check.

Extracted from `main.py` during the refactor. `main.py` imports these
functions to keep its own shape stable while the implementation details
live in the proper architectural layer:

    _slog / _show_error_box       → this module (startup-only primitives)
    _preflight_cleanup_webview2   → src.platform.windows.preflight.cleanup_webview2_state
    _check_webview2_runtime       → src.platform.windows.preflight.check_webview2_runtime

This leaves `main.py` small, readable, and easy to test indirectly via
`python -c "import main"` without performing OS work.
"""
from __future__ import annotations

import ctypes
import sys
from datetime import datetime

from .infra.paths import LOG_FILE, ensure_user_data

__all__ = [
    "check_webview2_runtime",
    "preflight_cleanup_webview2",
    "show_error_box",
    "slog",
]


def slog(msg: str) -> None:
    """Append a timestamped line to ~/.ghost/ghost.log so startup crashes can
    be diagnosed from a user's log even when stderr redirect failed.

    Name-kept identical (with leading underscore) in main.py via re-export to
    avoid touching its pattern-matched log lines (test watchers parse them).
    """
    try:
        ensure_user_data()
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] [startup] {msg}\n")
    except Exception:
        pass
    # Mirror to stderr (may be redirected to log file later).
    print(f"[startup] {msg}", flush=True)


def show_error_box(title: str, message: str) -> None:
    """Native Win32 MessageBox to surface startup errors to the user.

    Without this, a crash in `webview.create_window`/`webview.start` is
    silent and the user sees the app "not open" with no clue what happened.
    """
    if sys.platform != "win32":
        return
    try:
        # Win32 SDK constants — uppercase per Windows convention.
        MB_OK = 0x00000000         # noqa: N806
        MB_ICONERROR = 0x00000010  # noqa: N806
        MB_TOPMOST = 0x00040000    # noqa: N806
        ctypes.windll.user32.MessageBoxW(
            None, message, title, MB_OK | MB_ICONERROR | MB_TOPMOST
        )
    except Exception:
        pass


def preflight_cleanup_webview2() -> None:
    """Thin wrapper — the real work lives in the platform layer."""
    from .platform.windows.preflight import cleanup_webview2_state
    cleanup_webview2_state()


def check_webview2_runtime() -> bool:
    """Thin wrapper — the real work lives in the platform layer."""
    from .platform.windows.preflight import check_webview2_runtime as _check
    return _check()
