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


if sys.platform == "darwin":
    # Real implementations — only imported on macOS so `pip install` on Windows
    # never needs PyObjC.
    from AppKit import (  # type: ignore[import-not-found]
        NSApp,
        NSApplication,
        NSApplicationActivationPolicyAccessory,
        NSFloatingWindowLevel,
    )

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

    # Replace stubs with real implementations
    hide_from_capture = _hide_from_capture  # type: ignore[assignment]
    hide_from_taskbar = _hide_from_taskbar  # type: ignore[assignment]
    show_window = _show_window              # type: ignore[assignment]
    hide_window = _hide_window              # type: ignore[assignment]
    is_window_visible = _is_window_visible  # type: ignore[assignment]
    make_non_activating = _make_non_activating  # type: ignore[assignment]
    set_window_opacity = _set_window_opacity    # type: ignore[assignment]
