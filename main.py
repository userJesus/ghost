import os
import sys
import threading
import time
from pathlib import Path

import webview
import win32gui
import win32process

from src.api import GhostAPI
from src.win_focus import (
    hide_from_capture,
    hide_from_taskbar,
    hide_window,
    is_window_visible,
    make_non_activating,
    show_window,
)

ROOT = Path(__file__).resolve().parent
WEB_INDEX = ROOT / "web" / "index.html"
WEB_RESPONSE = ROOT / "web" / "response.html"
LOG_FILE = ROOT / "ghost.log"


def _find_own_top_window() -> int:
    """Enumerate visible top-level windows belonging to this process, pick the best match."""
    pid = os.getpid()
    candidates: list[tuple[int, str]] = []

    def callback(hwnd, _):
        try:
            if not win32gui.IsWindowVisible(hwnd):
                return True
            _, wpid = win32process.GetWindowThreadProcessId(hwnd)
            if wpid != pid:
                return True
            title = win32gui.GetWindowText(hwnd)
            if title:
                candidates.append((hwnd, title))
        except Exception:
            pass
        return True

    win32gui.EnumWindows(callback, None)

    for hwnd, title in candidates:
        if "Ghost" in title:
            return hwnd
    return candidates[0][0] if candidates else 0


def _apply_window_tweaks(api: GhostAPI, init_x: int = 100, init_y: int = 100,
                         init_w: int = 580, init_h: int = 720):
    """Poll for our window's HWND until found (up to 10s)."""
    for attempt in range(50):
        time.sleep(0.2)
        hwnd = _find_own_top_window()
        if hwnd:
            api.set_hwnd(hwnd)
            print(f"[init] HWND={hwnd} (after {attempt + 1} attempts)", flush=True)
            # Center the window on the primary monitor work area
            # WITHOUT changing its size (pywebview handles DPI scaling)
            try:
                import win32api
                import win32gui
                # Read the window's actual size (DPI-scaled by pywebview)
                rect = win32gui.GetWindowRect(hwnd)
                cur_w = rect[2] - rect[0]
                cur_h = rect[3] - rect[1]
                # Primary monitor work area via MonitorFromPoint
                import win32con as _wc
                hmon = win32api.MonitorFromPoint((0, 0), _wc.MONITOR_DEFAULTTOPRIMARY)
                info = win32api.GetMonitorInfo(hmon)
                work = info.get("Work", (0, 0, 1920, 1080))
                wl, wt, wr, wb = work
                cx = wl + ((wr - wl) - cur_w) // 2
                cy = wt + ((wb - wt) - cur_h) // 2

                SWP_NOSIZE = 0x0001
                SWP_NOZORDER = 0x0004
                SWP_NOACTIVATE = 0x0010
                win32gui.SetWindowPos(hwnd, 0, cx, cy, 0, 0,
                                      SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE)
                print(f"[init] centered at {cx},{cy} (size {cur_w}x{cur_h}, work={work})", flush=True)
            except Exception as e:
                print(f"[init] center failed: {e}", flush=True)
            print(f"[init] hide_from_capture={hide_from_capture(hwnd, True)}", flush=True)
            print(f"[init] hide_from_taskbar={hide_from_taskbar(hwnd)}", flush=True)
            try:
                make_non_activating(hwnd)
                print("[init] NOACTIVATE applied (permanent)", flush=True)
            except Exception as e:
                print(f"[init] NOACTIVATE failed: {e}", flush=True)
            _register_global_hotkey(hwnd)
            # Also find the response popup HWND and protect it
            _apply_response_popup_tweaks(api)
            return
    print("[warn] HWND not found after 10s polling", flush=True)


def _apply_response_popup_tweaks(api: GhostAPI):
    import os as _os
    try:
        pid = _os.getpid()
        main_hwnd = api._hwnd
        popup_hwnd = 0

        def cb(hwnd, _):
            nonlocal popup_hwnd
            try:
                _, wpid = win32process.GetWindowThreadProcessId(hwnd)
                if wpid != pid or hwnd == main_hwnd:
                    return True
                title = win32gui.GetWindowText(hwnd)
                if "Response" in title:
                    popup_hwnd = hwnd
            except Exception:
                pass
            return True

        win32gui.EnumWindows(cb, None)
        if popup_hwnd:
            api.set_response_hwnd(popup_hwnd)
            try:
                hide_from_capture(popup_hwnd, True)
                hide_from_taskbar(popup_hwnd)
            except Exception as e:
                print(f"[warn] popup protect: {e}", flush=True)
            print(f"[init] popup HWND={popup_hwnd}", flush=True)
    except Exception as e:
        print(f"[warn] popup tweak error: {e}", flush=True)


def _register_global_hotkey(hwnd: int):
    """Ctrl+Shift+G toggles Ghost visibility."""
    try:
        from pynput import keyboard
    except Exception as e:
        print(f"[warn] pynput unavailable: {e}", flush=True)
        return

    def toggle():
        if is_window_visible(hwnd):
            hide_window(hwnd)
        else:
            show_window(hwnd)

    def runner():
        try:
            with keyboard.GlobalHotKeys({"<ctrl>+<shift>+g": toggle}) as h:
                h.join()
        except Exception as e:
            print(f"[warn] hotkey runner died: {e}", flush=True)

    t = threading.Thread(target=runner, daemon=True)
    t.start()
    print("[init] Global hotkey registered: Ctrl+Shift+G", flush=True)


def main():
    # Redirect stderr to a log file so crashes are captured even under pythonw.
    try:
        sys.stderr = open(LOG_FILE, "w", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr
    except Exception:
        pass

    api = GhostAPI()

    # Placeholder values; real centering is done via Win32 after the window exists
    init_w, init_h = 580, 720
    init_x, init_y = 100, 100

    window = webview.create_window(
        "Ghost",
        str(WEB_INDEX),
        js_api=api,
        width=init_w,
        height=init_h,
        x=init_x,
        y=init_y,
        min_size=(40, 40),
        frameless=True,
        easy_drag=False,
        on_top=True,
        resizable=True,
        background_color="#131313",
    )
    api.set_window(window)

    # Pre-create the response popup window (hidden AND off-screen).
    # We park it at x=-10000 so DWM doesn't reserve any visible region
    # that could render as a black rectangle in screen captures.
    response_win = webview.create_window(
        "Ghost Response",
        str(WEB_RESPONSE),
        js_api=api,
        width=420,
        height=540,
        x=-10000,
        y=-10000,
        frameless=True,
        on_top=True,
        resizable=True,
        hidden=True,
        background_color="#131313",
    )
    api.set_response_window(response_win)

    threading.Thread(target=_apply_window_tweaks,
                     args=(api, init_x, init_y, init_w, init_h),
                     daemon=True).start()

    debug_mode = "--debug" in sys.argv
    webview.start(debug=debug_mode, gui="edgechromium" if sys.platform == "win32" else None)


if __name__ == "__main__":
    main()
