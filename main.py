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

if sys.platform != "win32":
    raise RuntimeError(
        "Ghost currently ships for Windows only. A macOS port is planned — "
        "see the roadmap in README.md."
    )

# User-data folder — same across platforms, matches history.py/logging_config.py/config.py.
# Windows resolves ~ to %USERPROFILE% (e.g. C:\Users\<user>\.ghost).
USER_DATA = Path.home() / ".ghost"

def _resolve_resource_root() -> Path:
    """Find where bundled web/ + assets/ live at runtime.

    PyInstaller 6.x places data files under <app>/_internal/ in onedir mode
    and under a temp MEIPASS dir in onefile mode. sys._MEIPASS is the
    documented source of truth, but we also probe a few candidates as
    fallback so a layout surprise doesn't leave Ghost rendering a blank
    gray WebView window with no way to recover.
    """
    if not getattr(sys, "frozen", False):
        return Path(__file__).resolve().parent

    exe_dir = Path(sys.executable).parent
    candidates: list[Path] = []
    mei = getattr(sys, "_MEIPASS", None)
    if mei:
        candidates.append(Path(mei))
    candidates.append(exe_dir / "_internal")  # PyInstaller 6.x onedir layout
    candidates.append(exe_dir)                # older onedir layout / edge cases

    for cand in candidates:
        if (cand / "web" / "index.html").is_file():
            return cand
    # Last resort: fall back to first candidate (Ghost will fail loudly instead
    # of silently loading a blank window).
    return candidates[0] if candidates else exe_dir


if getattr(sys, "frozen", False):
    ROOT = _resolve_resource_root()
    USER_DATA.mkdir(parents=True, exist_ok=True)
    LOG_FILE = USER_DATA / "ghost.log"
else:
    ROOT = _resolve_resource_root()
    LOG_FILE = ROOT / "ghost.log"
WEB_INDEX = ROOT / "web" / "index.html"
WEB_RESPONSE = ROOT / "web" / "response.html"
WEB_DROPDOWN = ROOT / "web" / "dropdown.html"


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
        response_hwnd = 0
        dropdown_hwnd = 0

        def cb(hwnd, _):
            nonlocal response_hwnd, dropdown_hwnd
            try:
                _, wpid = win32process.GetWindowThreadProcessId(hwnd)
                if wpid != pid or hwnd == main_hwnd:
                    return True
                title = win32gui.GetWindowText(hwnd)
                if "Response" in title:
                    response_hwnd = hwnd
                elif "Dropdown" in title:
                    dropdown_hwnd = hwnd
            except Exception:
                pass
            return True

        win32gui.EnumWindows(cb, None)
        # Response popup gets the full treatment (capture-excluded + no-taskbar).
        if response_hwnd:
            try:
                api.set_response_hwnd(response_hwnd)
                hide_from_capture(response_hwnd, True)
                hide_from_taskbar(response_hwnd)
                print(f"[init] response HWND={response_hwnd}", flush=True)
            except Exception as e:
                print(f"[warn] response popup protect: {e}", flush=True)

        # Dropdown popup: capture-excluded BUT must retain the ability to gain
        # focus so its JS `blur` handler can auto-close on click-outside.
        # hide_from_taskbar sets WS_EX_TOOLWINDOW which suppresses activation,
        # so we skip it here.
        if dropdown_hwnd:
            try:
                api.set_dropdown_hwnd(dropdown_hwnd)
                hide_from_capture(dropdown_hwnd, True)
                print(f"[init] dropdown HWND={dropdown_hwnd}", flush=True)
            except Exception as e:
                print(f"[warn] dropdown popup protect: {e}", flush=True)
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


_SINGLE_INSTANCE_MUTEX = None
_SHOW_EVENT_NAME = "Global\\GhostShowEvent"
_INSTANCE_MUTEX_NAME = "Global\\GhostSingleInstance"


def _ensure_single_instance_windows() -> bool:
    """Windows: use a named mutex to detect a running Ghost. If found, signal it
    to show itself (via a named event the running instance listens on) and exit.
    Returns True when this IS the sole instance; returns False (and exits on Win)
    when another instance was already running.
    """
    if sys.platform != "win32":
        return True
    try:
        import win32event
        import win32api
        import winerror
        global _SINGLE_INSTANCE_MUTEX
        _SINGLE_INSTANCE_MUTEX = win32event.CreateMutex(None, False, _INSTANCE_MUTEX_NAME)
        if win32api.GetLastError() == winerror.ERROR_ALREADY_EXISTS:
            # Signal the existing instance to show itself, then exit.
            try:
                ev = win32event.OpenEvent(
                    win32event.EVENT_MODIFY_STATE, False, _SHOW_EVENT_NAME
                )
                win32event.SetEvent(ev)
            except Exception:
                pass
            print("[main] another Ghost is already running — asked it to show and exiting",
                  flush=True)
            sys.exit(0)
        return True
    except Exception as e:
        print(f"[main] single-instance check failed: {e}", flush=True)
        return True


def _watch_show_event_windows(hwnd_getter):
    """Windows: listen on the named event and, when signaled, bring Ghost to front.
    `hwnd_getter` is a zero-arg callable returning the current HWND (or 0)."""
    if sys.platform != "win32":
        return
    try:
        import win32event
        ev = win32event.CreateEvent(None, False, False, _SHOW_EVENT_NAME)
    except Exception as e:
        print(f"[main] show-event create failed: {e}", flush=True)
        return

    def runner():
        while True:
            try:
                win32event.WaitForSingleObject(ev, win32event.INFINITE)
                hwnd = hwnd_getter()
                if hwnd:
                    try:
                        show_window(hwnd)
                    except Exception as e:
                        print(f"[show-event] show_window failed: {e}", flush=True)
            except Exception as e:
                print(f"[show-event] wait error: {e}", flush=True)
                time.sleep(1)

    t = threading.Thread(target=runner, daemon=True, name="ghost-show-event")
    t.start()


def main():
    # Single-instance guard — exits if another Ghost is already running and
    # signals it to show itself. Mac: NSApplication handles double-launch by
    # default (brings existing instance to front).
    _ensure_single_instance_windows()

    # Redirect stderr to a log file so crashes are captured even under pythonw.
    try:
        sys.stderr = open(LOG_FILE, "w", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr
    except Exception:
        pass

    api = GhostAPI()
    _watch_show_event_windows(lambda: api._hwnd)

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
        width=540,
        height=600,
        x=-10000,
        y=-10000,
        frameless=True,
        on_top=True,
        resizable=True,
        hidden=True,
        background_color="#131313",
    )
    api.set_response_window(response_win)

    # Floating dropdown popup — third window used in compact mode to render
    # chip options outside the compact bar's bounds (a 200px-tall window can't
    # fit a 280px flyout). easy_drag=False + no drag handlers in dropdown.html
    # keep the popup from being moveable like a normal window; it behaves
    # like a native menu (auto-hides on blur/Esc).
    dropdown_win = webview.create_window(
        "Ghost Dropdown",
        str(WEB_DROPDOWN),
        js_api=api,
        width=240,
        height=300,
        x=-10000,
        y=-10000,
        frameless=True,
        easy_drag=False,
        on_top=True,
        resizable=False,
        hidden=True,
        background_color="#2d2d2d",
    )
    api.set_dropdown_window(dropdown_win)

    threading.Thread(target=_apply_window_tweaks,
                     args=(api, init_x, init_y, init_w, init_h),
                     daemon=True).start()

    debug_mode = "--debug" in sys.argv
    webview.start(debug=debug_mode, gui="edgechromium")


if __name__ == "__main__":
    main()
