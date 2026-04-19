"""Camada de abstração de plataforma para o Ghost.

Define uma interface `PlatformAdapter` com as operações específicas de SO
(esconder da captura, opacidade, hotkey global, etc.) e uma implementação
concreta `WindowsPlatform`. Port para Mac deve subclassar `PlatformAdapter`
criando `MacPlatform`.

Uso:
    from src.platform_adapter import get_platform
    plat = get_platform()
    plat.hide_from_capture(hwnd)
"""
from __future__ import annotations

import sys
from abc import ABC, abstractmethod
from collections.abc import Callable

from .logging_config import get_logger

log = get_logger(__name__)


class PlatformAdapter(ABC):
    """Interface mínima que cada SO precisa implementar."""

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
    """Implementação Windows — delega para src.win_focus."""

    def hide_from_capture(self, window_handle: int, enabled: bool = True) -> bool:
        from . import win_focus
        return win_focus.hide_from_capture(window_handle, enabled)

    def set_window_opacity(self, window_handle: int, alpha: float) -> bool:
        from . import win_focus
        return win_focus.set_window_opacity(window_handle, alpha)

    def hide_from_taskbar(self, window_handle: int) -> bool:
        from . import win_focus
        return win_focus.hide_from_taskbar(window_handle)

    def show_window(self, window_handle: int) -> bool:
        from . import win_focus
        return win_focus.show_window(window_handle)

    def hide_window(self, window_handle: int) -> bool:
        from . import win_focus
        return win_focus.hide_window(window_handle)

    def is_window_visible(self, window_handle: int) -> bool:
        from . import win_focus
        return win_focus.is_window_visible(window_handle)

    def make_non_activating(self, window_handle: int) -> None:
        from . import win_focus
        win_focus.make_non_activating(window_handle)

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


class MacPlatform(PlatformAdapter):
    """Stub para futuro port macOS. Todas as operações levantam NotImplementedError."""

    def _unsupported(self) -> None:
        raise NotImplementedError("Mac port ainda não implementado — veja ROADMAP.md")

    def hide_from_capture(self, window_handle: int, enabled: bool = True) -> bool:
        self._unsupported()
        return False

    def set_window_opacity(self, window_handle: int, alpha: float) -> bool:
        self._unsupported()
        return False

    def hide_from_taskbar(self, window_handle: int) -> bool:
        self._unsupported()
        return False

    def show_window(self, window_handle: int) -> bool:
        self._unsupported()
        return False

    def hide_window(self, window_handle: int) -> bool:
        self._unsupported()
        return False

    def is_window_visible(self, window_handle: int) -> bool:
        self._unsupported()
        return False

    def make_non_activating(self, window_handle: int) -> None:
        self._unsupported()

    def register_global_hotkey(self, combo: str, callback: Callable[[], None]) -> None:
        self._unsupported()


_INSTANCE: PlatformAdapter | None = None


def get_platform() -> PlatformAdapter:
    """Retorna um singleton da plataforma adequada ao SO atual."""
    global _INSTANCE
    if _INSTANCE is not None:
        return _INSTANCE

    if sys.platform == "win32":
        _INSTANCE = WindowsPlatform()
    elif sys.platform == "darwin":
        _INSTANCE = MacPlatform()
    else:
        raise RuntimeError(f"Plataforma não suportada: {sys.platform}")

    return _INSTANCE
