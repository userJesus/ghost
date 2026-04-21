import ctypes
import threading
import time
from ctypes import wintypes

import win32api
import win32con
import win32gui

# --- SetWindowDisplayAffinity (Windows 10 2004+) ---
WDA_NONE = 0
WDA_MONITOR = 1
WDA_EXCLUDEFROMCAPTURE = 0x11

_user32 = ctypes.windll.user32
_SetWindowDisplayAffinity = _user32.SetWindowDisplayAffinity
_SetWindowDisplayAffinity.argtypes = [wintypes.HWND, wintypes.DWORD]
_SetWindowDisplayAffinity.restype = wintypes.BOOL


def hide_from_capture(hwnd: int, enabled: bool = True, force_redraw: bool = False) -> bool:
    """Exclude window from screen captures (screen sharing, recording, screenshots).

    Uses WDA_EXCLUDEFROMCAPTURE on Windows 10 2004+.

    force_redraw: if True, also nudges the window and flushes DWM to clear stale
    capture regions on other monitors. Only use this AFTER the window is fully
    initialized (calling it during startup can crash WebView2).
    """
    if not hwnd:
        return False
    flag = WDA_EXCLUDEFROMCAPTURE if enabled else WDA_NONE
    try:
        ok = _SetWindowDisplayAffinity(hwnd, flag)

        if not force_redraw:
            return bool(ok)

        try:
            rect = win32gui.GetWindowRect(hwnd)
            x, y = rect[0], rect[1]

            SWP_NOSIZE_L = 0x0001
            SWP_NOZORDER_L = 0x0004
            SWP_NOACTIVATE_L = 0x0010
            flags_nosize = SWP_NOSIZE_L | SWP_NOZORDER_L | SWP_NOACTIVATE_L

            win32gui.SetWindowPos(hwnd, 0, x + 1, y, 0, 0, flags_nosize)
            win32gui.SetWindowPos(hwnd, 0, x, y, 0, 0, flags_nosize)

            RDW_INVALIDATE = 0x0001
            RDW_ERASE = 0x0004
            RDW_ALLCHILDREN = 0x0080
            RDW_UPDATENOW = 0x0100
            _user32.RedrawWindow(
                hwnd, None, None,
                RDW_INVALIDATE | RDW_ERASE | RDW_ALLCHILDREN | RDW_UPDATENOW,
            )

            try:
                ctypes.windll.dwmapi.DwmFlush()
            except Exception:
                pass
        except Exception:
            pass
        return bool(ok)
    except Exception:
        return False


def set_dwm_shadow(hwnd: int, enabled: bool) -> bool:
    """Habilita/desabilita a sombra DWM que o Windows desenha ao redor da
    janela. Quando disabled, remove a borda escura que aparece ao redor
    mesmo com region clipping."""
    if not hwnd:
        return False
    try:
        DWMWA_NCRENDERING_POLICY = 2
        DWMNCRP_ENABLED = 2
        DWMNCRP_DISABLED = 1
        value = ctypes.c_int(DWMNCRP_ENABLED if enabled else DWMNCRP_DISABLED)
        ctypes.windll.dwmapi.DwmSetWindowAttribute(
            hwnd, DWMWA_NCRENDERING_POLICY,
            ctypes.byref(value), ctypes.sizeof(value),
        )
        return True
    except Exception as e:
        print(f"[win_focus] set_dwm_shadow error: {e}", flush=True)
        return False


def set_round_region(hwnd: int, w: int = 0, h: int = 0, corner: int = 0) -> bool:
    """Clippa a janela num retângulo arredondado centralizado.
    w, h: tamanho da região em pixels físicos. corner: raio dos cantos.
    Se w<=0 ou h<=0, desabilita (volta retangular).
    Pixels fora da região não são desenhados — OS-level transparency,
    compatível com WDA_EXCLUDEFROMCAPTURE.
    """
    if not hwnd:
        return False
    try:
        if w <= 0 or h <= 0:
            win32gui.SetWindowRgn(hwnd, 0, True)
            return True
        rect = win32gui.GetWindowRect(hwnd)
        win_w = rect[2] - rect[0]
        win_h = rect[3] - rect[1]
        if win_w <= 0 or win_h <= 0:
            return False
        left = (win_w - w) // 2
        top = (win_h - h) // 2
        right = left + w
        bottom = top + h
        gdi32 = ctypes.windll.gdi32
        if corner > 0:
            hrgn = gdi32.CreateRoundRectRgn(left, top, right, bottom, corner, corner)
        else:
            hrgn = gdi32.CreateEllipticRgn(left, top, right, bottom)
        if not hrgn:
            return False
        win32gui.SetWindowRgn(hwnd, hrgn, True)
        return True
    except Exception as e:
        print(f"[win_focus] set_round_region error: {e}", flush=True)
        return False


def set_color_key(hwnd: int, rgb_tuple) -> bool:
    """Aplica chroma-key transparency: pixels da cor EXATA viram transparentes.
    rgb_tuple: tupla (r, g, b). Passe None para desabilitar."""
    if not hwnd:
        return False
    try:
        ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        if rgb_tuple is None:
            # Desabilita: remove LAYERED + reseta atributos
            if ex_style & win32con.WS_EX_LAYERED:
                win32gui.SetWindowLong(
                    hwnd, win32con.GWL_EXSTYLE, ex_style & ~win32con.WS_EX_LAYERED
                )
            return True
        r, g, b = rgb_tuple
        # Windows usa 0x00BBGGRR (little-endian)
        colorref = (b << 16) | (g << 8) | r
        if not (ex_style & win32con.WS_EX_LAYERED):
            win32gui.SetWindowLong(
                hwnd, win32con.GWL_EXSTYLE, ex_style | win32con.WS_EX_LAYERED
            )
        win32gui.SetLayeredWindowAttributes(
            hwnd, colorref, 255, win32con.LWA_COLORKEY
        )
        return True
    except Exception as e:
        print(f"[win_focus] set_color_key error: {e}", flush=True)
        return False


def set_window_opacity(hwnd: int, alpha: float) -> bool:
    """Set window opacity 0.0 (transparent) to 1.0 (opaque).

    Uses WS_EX_LAYERED + SetLayeredWindowAttributes.
    Clamps alpha to [0.2, 1.0] so window stays clickable.
    """
    if not hwnd:
        return False
    try:
        alpha = max(0.2, min(1.0, float(alpha)))
        byte_alpha = int(alpha * 255)
        ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        if not (ex_style & win32con.WS_EX_LAYERED):
            win32gui.SetWindowLong(
                hwnd, win32con.GWL_EXSTYLE, ex_style | win32con.WS_EX_LAYERED
            )
        win32gui.SetLayeredWindowAttributes(
            hwnd, 0, byte_alpha, win32con.LWA_ALPHA
        )
        return True
    except Exception:
        return False


def hide_from_taskbar(hwnd: int) -> bool:
    """Apply WS_EX_TOOLWINDOW — removes window from the taskbar and Alt+Tab.

    Uses SetWindowPos+SWP_FRAMECHANGED to apply the style change without
    hiding/reshowing the window (hide/show breaks transparent windows).
    """
    if not hwnd:
        return False
    try:
        ex_style = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
        ex_style |= win32con.WS_EX_TOOLWINDOW
        ex_style &= ~win32con.WS_EX_APPWINDOW
        win32gui.SetWindowLong(hwnd, win32con.GWL_EXSTYLE, ex_style)
        win32gui.SetWindowPos(hwnd, 0, 0, 0, 0, 0, _SWP_FLAGS)
        return True
    except Exception:
        return False


def show_window(hwnd: int) -> bool:
    """Show and bring the window to front without stealing foreground aggressively."""
    if not hwnd:
        return False
    try:
        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        elif not win32gui.IsWindowVisible(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_SHOWNOACTIVATE)
        win32gui.SetWindowPos(
            hwnd, win32con.HWND_TOPMOST, 0, 0, 0, 0,
            SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE_
        )
        return True
    except Exception:
        return False


def hide_window(hwnd: int) -> bool:
    if not hwnd:
        return False
    try:
        win32gui.ShowWindow(hwnd, win32con.SW_HIDE)
        return True
    except Exception:
        return False


def is_window_visible(hwnd: int) -> bool:
    try:
        return bool(win32gui.IsWindowVisible(hwnd))
    except Exception:
        return False

WM_NCLBUTTONDOWN = 0x00A1
HTCAPTION = 2
VK_LBUTTON = 0x01
SWP_NOSIZE = 0x0001
SWP_NOZORDER_ = 0x0004
SWP_NOACTIVATE_ = 0x0010

SWP_NOMOVE = 0x0002
SWP_NOSIZE = 0x0001
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_FRAMECHANGED = 0x0020
SWP_ASYNCWINDOWPOS = 0x4000   # post the request; don't block on the owner thread
# SWP_ASYNCWINDOWPOS is the critical flag: hide_from_taskbar is called from
# our background poll thread, but the window belongs to pywebview's UI
# thread. Without ASYNC, SetWindowPos sends WM_NCCALCSIZE synchronously to
# the UI thread and BLOCKS our caller until WebView2 finishes whatever it's
# doing (cold init, shader compile, etc). On a fresh install with 10+ webview2
# helpers from other apps competing for resources, that block lasts long
# enough to trigger Windows' "não está respondendo" dialog. ASYNC posts the
# message to the UI thread's queue instead — the style change still applies
# (on next pump drain, usually milliseconds later) but our poll thread
# returns instantly.
_SWP_FLAGS = SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED | SWP_ASYNCWINDOWPOS


_GWL_EXSTYLE = -20
_WS_EX_NOACTIVATE = 0x08000000

_user32 = ctypes.windll.user32
_user32.GetWindowLongW.restype = ctypes.c_long
_user32.GetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int]
_user32.SetWindowLongW.restype = ctypes.c_long
_user32.SetWindowLongW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_long]


def make_non_activating(hwnd: int) -> None:
    """Apply WS_EX_NOACTIVATE via pure ctypes — no SetWindowPos/repaint."""
    try:
        current = _user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        new = current | _WS_EX_NOACTIVATE
        _user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, new)
    except Exception as e:
        print(f"[win_focus] make_non_activating error: {e}", flush=True)


def make_activating(hwnd: int) -> None:
    """Remove WS_EX_NOACTIVATE via pure ctypes — no SetWindowPos/repaint."""
    try:
        current = _user32.GetWindowLongW(hwnd, _GWL_EXSTYLE)
        new = current & ~_WS_EX_NOACTIVATE
        _user32.SetWindowLongW(hwnd, _GWL_EXSTYLE, new)
    except Exception as e:
        print(f"[win_focus] make_activating error: {e}", flush=True)


def get_foreground_hwnd() -> int:
    return win32gui.GetForegroundWindow()


def set_foreground(hwnd: int) -> bool:
    if not hwnd:
        return False
    try:
        win32gui.SetForegroundWindow(hwnd)
        return True
    except Exception:
        return False


def force_foreground(hwnd: int) -> bool:
    """Force a NOACTIVATE window to the foreground via AttachThreadInput trick.
    Bypasses Windows' anti-foreground-stealing restrictions.
    """
    if not hwnd:
        return False
    try:
        import ctypes

        import win32process
        fg_hwnd = win32gui.GetForegroundWindow()
        if fg_hwnd == hwnd:
            return True

        fg_thread, _ = win32process.GetWindowThreadProcessId(fg_hwnd) if fg_hwnd else (0, 0)
        our_thread = ctypes.windll.kernel32.GetCurrentThreadId()
        user32 = ctypes.windll.user32

        if fg_thread and fg_thread != our_thread:
            user32.AttachThreadInput(fg_thread, our_thread, True)
        try:
            user32.BringWindowToTop(hwnd)
            user32.SetForegroundWindow(hwnd)
            user32.SetFocus(hwnd)
        finally:
            if fg_thread and fg_thread != our_thread:
                user32.AttachThreadInput(fg_thread, our_thread, False)
        return True
    except Exception as e:
        print(f"[win_focus] force_foreground error: {e}", flush=True)
        return False


def start_drag(hwnd: int) -> None:
    """Legacy — kept for API compat."""
    pass


_drag_lock = threading.Lock()
_dragging = False


def drag_window_loop(hwnd: int) -> None:
    """Run a tight loop moving the window to follow the cursor while LMB is pressed.
    Should be invoked from a background thread so it doesn't block the UI.
    """
    global _dragging
    with _drag_lock:
        if _dragging or not hwnd:
            return
        _dragging = True
    try:
        rect = win32gui.GetWindowRect(hwnd)
        win_x, win_y = rect[0], rect[1]
        start_x, start_y = win32api.GetCursorPos()

        while True:
            state = win32api.GetAsyncKeyState(VK_LBUTTON)
            if state & 0x8000 == 0:
                break
            cx, cy = win32api.GetCursorPos()
            dx = cx - start_x
            dy = cy - start_y
            try:
                win32gui.SetWindowPos(
                    hwnd, 0, win_x + dx, win_y + dy, 0, 0,
                    SWP_NOSIZE | SWP_NOZORDER_ | SWP_NOACTIVATE_
                )
            except Exception:
                break
            time.sleep(0.008)
    finally:
        _dragging = False
