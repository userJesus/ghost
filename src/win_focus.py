"""Backwards-compatible re-export of the moved Win32 focus helpers.

Real implementation: `src.platform.windows.focus`.
Kept at this path for external compatibility and PyInstaller hiddenimports.
"""
from __future__ import annotations

from .platform.windows.focus import (  # noqa: F401
    HTCAPTION,
    WDA_EXCLUDEFROMCAPTURE,
    WDA_MONITOR,
    WDA_NONE,
    WM_NCLBUTTONDOWN,
    drag_window_loop,
    force_foreground,
    get_foreground_hwnd,
    hide_from_capture,
    hide_from_taskbar,
    hide_window,
    is_window_visible,
    make_activating,
    make_non_activating,
    set_color_key,
    set_dwm_shadow,
    set_foreground,
    set_round_region,
    set_window_opacity,
    show_window,
    start_drag,
)
