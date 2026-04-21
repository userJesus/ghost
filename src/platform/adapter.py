"""Platform abstraction layer for Ghost (Windows-only for now).

Defines `PlatformAdapter` ABC; only `WindowsPlatform` is implemented.
A macOS port is planned — see the roadmap in README.md. Until then,
`get_platform()` raises on non-Windows hosts so the app fails loudly
instead of silently misbehaving.

Usage:
    from src.platform.adapter import get_platform
    plat = get_platform()
    plat.hide_from_capture(hwnd)
"""
from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from collections.abc import Callable

from src.infra.logging_setup import get_logger

log = get_logger(__name__)


class PlatformAdapter(ABC):
    """Minimum interface each OS must implement."""

    @abstractmethod
    def hide_from_capture(self, window_handle: int, enabled: bool = True) -> bool: ...

    @abstractmethod
    def set_window_opacity(self, window_handle: int, alpha: float) -> bool: ...

    @abstractmethod
    def hide_from_taskbar(self, window_handle: int) -> bool: ...

    @abstractmethod
    def show_window(self, window_handle: int) -> bool: ...

    @abstractmethod
    def hide_window(self, window_handle: int) -> bool: ...

    @abstractmethod
    def is_window_visible(self, window_handle: int) -> bool: ...

    @abstractmethod
    def make_non_activating(self, window_handle: int) -> None: ...

    @abstractmethod
    def register_global_hotkey(self, combo: str, callback: Callable[[], None]) -> None: ...


class WindowsPlatform(PlatformAdapter):
    """Windows implementation — delegates to platform.windows.focus."""

    def hide_from_capture(self, window_handle: int, enabled: bool = True) -> bool:
        from .windows import focus
        return focus.hide_from_capture(window_handle, enabled)

    def set_window_opacity(self, window_handle: int, alpha: float) -> bool:
        from .windows import focus
        return focus.set_window_opacity(window_handle, alpha)

    def hide_from_taskbar(self, window_handle: int) -> bool:
        from .windows import focus
        return focus.hide_from_taskbar(window_handle)

    def show_window(self, window_handle: int) -> bool:
        from .windows import focus
        return focus.show_window(window_handle)

    def hide_window(self, window_handle: int) -> bool:
        from .windows import focus
        return focus.hide_window(window_handle)

    def is_window_visible(self, window_handle: int) -> bool:
        from .windows import focus
        return focus.is_window_visible(window_handle)

    def make_non_activating(self, window_handle: int) -> None:
        from .windows import focus
        focus.make_non_activating(window_handle)

    def register_global_hotkey(self, combo: str, callback: Callable[[], None]) -> None:
        import threading
        try:
            from pynput import keyboard
        except ImportError as exc:
            log.warning("pynput unavailable, hotkey disabled: %s", exc)
            return

        def runner() -> None:
            try:
                with keyboard.GlobalHotKeys({combo: callback}) as h:
                    h.join()
            except Exception as exc:
                log.warning("hotkey runner died: %s", exc)

        t = threading.Thread(target=runner, daemon=True, name="ghost-hotkey")
        t.start()
        log.info("global hotkey registered: %s", combo)


_INSTANCE: PlatformAdapter | None = None


def get_platform() -> PlatformAdapter:
    """Return a singleton of the current platform. Only Windows is supported
    right now — macOS is on the roadmap."""
    global _INSTANCE
    if _INSTANCE is not None:
        return _INSTANCE
    if sys.platform == "win32":
        _INSTANCE = WindowsPlatform()
        return _INSTANCE
    raise RuntimeError(
        f"Platform not yet supported: {sys.platform}. "
        "Ghost currently ships for Windows only; a macOS port is planned. "
        "See the roadmap in README.md."
    )
