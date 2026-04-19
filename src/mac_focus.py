"""macOS companion to `win_focus.py`.

Mirrors the same public API (`hide_from_capture`, `hide_from_taskbar`, etc.)
but backed by AppKit / PyObjC. Each function is imported lazily from AppKit
so the module is importable on non-Mac hosts (for static analysis, tests,
or CI runners building Windows artifacts).
"""
from __future__ import annotations

import sys

# ---- always-importable stubs (Windows / Linux / CI without PyObjC) --------
def _stub_bool(*_args, **_kwargs) -> bool:
    return False

def _stub_none(*_args, **_kwargs) -> None:
    return None

hide_from_capture = _stub_bool
hide_from_taskbar = _stub_bool
show_window = _stub_bool
hide_window = _stub_bool
is_window_visible = _stub_bool
make_non_activating = _stub_none
set_window_opacity = _stub_bool

def get_window_frame(*_a, **_kw):
    return None

def set_window_frame(*_a, **_kw):
    return False

def list_screens(*_a, **_kw):
    return []

def current_screen_for_window(*_a, **_kw):
    return None

def start_drag(*_a, **_kw):
    return False


if sys.platform == "darwin":
    # Real implementations — only imported on macOS so `pip install` on Windows
    # never needs PyObjC.
    from AppKit import (  # type: ignore[import-not-found]
        NSApp,
        NSApplication,
        NSApplicationActivationPolicyAccessory,
        NSEvent,
        NSFloatingWindowLevel,
        NSScreen,
    )
    from Foundation import NSMakeRect  # type: ignore[import-not-found]
    import threading as _threading
    import time as _time

    # NSWindowSharingType values (from <AppKit/NSWindow.h>):
    #   0 = NSWindowSharingNone       — invisible to screen capture
    #   1 = NSWindowSharingReadOnly   — default
    _SHARING_NONE = 0
    _SHARING_READ_ONLY = 1

    def _ghost_windows(match: str = "Ghost"):  # pragma: no cover - mac only
        """Return every NSWindow whose title contains `match`."""
        try:
            app = NSApplication.sharedApplication()
        except Exception:
            return []
        out = []
        for w in app.windows():
            try:
                if match in str(w.title()):
                    out.append(w)
            except Exception:
                continue
        return out

    def _hide_from_capture(_handle=None, enabled: bool = True) -> bool:  # pragma: no cover
        """Cross-process screen-share invisibility (setSharingType:)."""
        wins = _ghost_windows()
        sharing = _SHARING_NONE if enabled else _SHARING_READ_ONLY
        ok = False
        for w in wins:
            try:
                w.setSharingType_(sharing)
                ok = True
            except Exception:
                pass
        return ok

    def _hide_from_taskbar(_handle=None) -> bool:  # pragma: no cover
        """Hide the Ghost process from the Dock + Cmd-Tab switcher."""
        try:
            NSApp().setActivationPolicy_(NSApplicationActivationPolicyAccessory)
            return True
        except Exception:
            return False

    def _show_window(_handle=None) -> bool:  # pragma: no cover
        ok = False
        for w in _ghost_windows():
            try:
                w.orderFront_(None)
                ok = True
            except Exception:
                pass
        return ok

    def _hide_window(_handle=None) -> bool:  # pragma: no cover
        ok = False
        for w in _ghost_windows():
            try:
                w.orderOut_(None)
                ok = True
            except Exception:
                pass
        return ok

    def _is_window_visible(_handle=None) -> bool:  # pragma: no cover
        for w in _ghost_windows():
            try:
                if w.isVisible():
                    return True
            except Exception:
                continue
        return False

    def _make_non_activating(_handle=None) -> None:  # pragma: no cover
        """On Mac, we keep Ghost floating above regular windows without stealing focus."""
        for w in _ghost_windows():
            try:
                w.setLevel_(NSFloatingWindowLevel)
            except Exception:
                pass

    def _set_window_opacity(_handle=None, alpha: float = 1.0) -> bool:  # pragma: no cover
        ok = False
        for w in _ghost_windows():
            try:
                w.setAlphaValue_(float(alpha))
                ok = True
            except Exception:
                pass
        return ok

    # ---- Window frame helpers (Win32-compatible TOP-LEFT coordinates) ----
    #
    # The rest of Ghost's code uses TOP-LEFT screen coordinates (inherited
    # from Win32 where origin is top-left). macOS natively uses BOTTOM-LEFT
    # origin. We hide that conversion in this module so api.py doesn't have
    # to care — all helpers below accept and return top-left coordinates.

    def _primary_screen_height() -> float:  # pragma: no cover
        """Height of the screen with origin at (0, 0) — used as the Y-flip reference."""
        for s in NSScreen.screens():
            f = s.frame()
            if f.origin.x == 0 and f.origin.y == 0:
                return float(f.size.height)
        ms = NSScreen.mainScreen()
        return float(ms.frame().size.height) if ms else 1080.0

    def _bl_to_tl_y(y_bl: float, h: float, primary_h: float) -> float:
        return primary_h - y_bl - h

    def _tl_to_bl_y(y_tl: float, h: float, primary_h: float) -> float:
        return primary_h - y_tl - h

    def _get_window_frame(match: str = "Ghost"):  # pragma: no cover
        """Return (x, y, width, height) in TOP-LEFT coordinates, or None."""
        wins = _ghost_windows(match)
        if not wins:
            return None
        try:
            f = wins[0].frame()
            primary_h = _primary_screen_height()
            y_tl = _bl_to_tl_y(float(f.origin.y), float(f.size.height), primary_h)
            return (
                int(round(float(f.origin.x))),
                int(round(y_tl)),
                int(round(float(f.size.width))),
                int(round(float(f.size.height))),
            )
        except Exception:
            return None

    def _set_window_frame(x: int, y: int, w: int, h: int, match: str = "Ghost") -> bool:  # pragma: no cover
        """Set frame using TOP-LEFT coordinates. Applies to every matching NSWindow."""
        wins = _ghost_windows(match)
        if not wins:
            return False
        primary_h = _primary_screen_height()
        y_bl = _tl_to_bl_y(float(y), float(h), primary_h)
        rect = NSMakeRect(float(x), y_bl, float(w), float(h))
        ok = False
        for win in wins:
            try:
                win.setFrame_display_(rect, True)
                ok = True
            except Exception:
                pass
        return ok

    def _list_screens() -> list:  # pragma: no cover
        """Return all NSScreens normalized to TOP-LEFT Win32-style dicts.
        Each dict has: index, left, top, width, height, work_left, work_top, work_right, work_bottom."""
        primary_h = _primary_screen_height()
        out = []
        for i, s in enumerate(NSScreen.screens()):
            f = s.frame()
            vf = s.visibleFrame()
            y_tl = _bl_to_tl_y(float(f.origin.y), float(f.size.height), primary_h)
            vy_tl = _bl_to_tl_y(float(vf.origin.y), float(vf.size.height), primary_h)
            out.append({
                "index": i,
                "left": int(round(float(f.origin.x))),
                "top": int(round(y_tl)),
                "width": int(round(float(f.size.width))),
                "height": int(round(float(f.size.height))),
                "work_left": int(round(float(vf.origin.x))),
                "work_top": int(round(vy_tl)),
                "work_right": int(round(float(vf.origin.x) + float(vf.size.width))),
                "work_bottom": int(round(vy_tl + float(vf.size.height))),
            })
        return out

    def _current_screen_for_window(match: str = "Ghost"):  # pragma: no cover
        """Return the screen dict containing the Ghost window's center. Falls back to primary."""
        frame = _get_window_frame(match)
        screens = _list_screens()
        if frame and screens:
            cx = frame[0] + frame[2] // 2
            cy = frame[1] + frame[3] // 2
            for s in screens:
                if (s["left"] <= cx < s["left"] + s["width"] and
                        s["top"] <= cy < s["top"] + s["height"]):
                    return s
        return screens[0] if screens else None

    def _start_drag(match: str = "Ghost") -> bool:  # pragma: no cover
        """Begin a native drag loop on the first matching NSWindow.
        NSWindow.performWindowDragWithEvent_ runs AppKit's own drag tracking,
        which stops cleanly when the user releases the mouse."""
        try:
            wins = _ghost_windows(match)
            if not wins:
                return False
            evt = NSApp().currentEvent()
            if evt is None:
                return False
            wins[0].performWindowDragWithEvent_(evt)
            return True
        except Exception:
            return False

    # Replace stubs with real implementations
    hide_from_capture = _hide_from_capture  # type: ignore[assignment]
    hide_from_taskbar = _hide_from_taskbar  # type: ignore[assignment]
    show_window = _show_window              # type: ignore[assignment]
    hide_window = _hide_window              # type: ignore[assignment]
    is_window_visible = _is_window_visible  # type: ignore[assignment]
    make_non_activating = _make_non_activating  # type: ignore[assignment]
    set_window_opacity = _set_window_opacity    # type: ignore[assignment]
    get_window_frame = _get_window_frame              # type: ignore[assignment]
    set_window_frame = _set_window_frame              # type: ignore[assignment]
    list_screens = _list_screens                      # type: ignore[assignment]
    current_screen_for_window = _current_screen_for_window  # type: ignore[assignment]
    start_drag = _start_drag                          # type: ignore[assignment]
