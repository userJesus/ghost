import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

import webview
import win32gui
import win32process

from src.api import GhostAPI
from src.bootstrap import (
    preflight_cleanup_webview2 as _bootstrap_preflight,
    check_webview2_runtime as _bootstrap_check_runtime,
)
from src.infra.paths import USER_DATA  # single-source-of-truth ~/.ghost path
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
    """Poll for our window's HWND, apply protection styles WHILE the window
    is parked off-screen, then move it on-screen in a single SetWindowPos.

    Cold-init message-pump starvation — the root cause of "Ghost.exe não
    está respondendo" on cold boot + fresh install — is avoided because
    the window never occupies visible screen real-estate during the 3–10s
    that WebView2 spends in its startup blocking code. From the user's
    perspective, there is simply "nothing" until Ghost is ready; there is
    no unresponsive-looking window for Windows to stamp with the (Não
    Respondendo) overlay, and therefore nothing to force-kill. (The
    v1.1.19 `hidden=True` + on_ready attempt at the same goal failed on
    some setups because the window never got un-hidden; off-screen
    sidesteps that class of bugs because the window is treated as visible
    throughout — only its coordinates are unusual.)

    Protection-styles-before-on-screen ordering also closes a subtle
    screen-capture leak that existed pre-1.1.26: pywebview would paint
    1–2 frames at the intended rect before the tweaks thread got around
    to applying WDA_EXCLUDEFROMCAPTURE. Screen-share tools could
    theoretically catch that flash. Now WDA lands while the window is
    still off-screen, so the FIRST pixel the user (or a capture tool)
    ever sees is already protected.

    The final SetWindowPos uses SWP_ASYNCWINDOWPOS so that if WebView2's
    pump is still cold, we post-and-return instead of blocking our own
    thread on a drained UI thread (same pattern as hide_from_taskbar
    since 1.1.8).

    Poll budget reduced from 50×200ms = 10s to 30×100ms = 3s. If the
    HWND hasn't appeared in 3s something is very wrong; the safety
    fallback started alongside this thread will call window.move()
    via pywebview as last-ditch recovery so the user never ends up
    staring at a blank desktop."""
    hwnd = 0
    attempt = 0
    for attempt in range(30):
        time.sleep(0.1)
        hwnd = _find_own_top_window()
        if hwnd:
            break

    if not hwnd:
        print("[warn] HWND not found after 3s polling — safety timer will handle move", flush=True)
        try:
            _apply_response_popup_tweaks(api)
        except Exception as e:
            print(f"[warn] popup tweak fallback error: {e}", flush=True)
        return

    api.set_hwnd(hwnd)
    print(f"[init] HWND={hwnd} (after {attempt + 1} attempts)", flush=True)

    # Protection styles — each wrapped individually so one failure
    # doesn't prevent the others (and, critically, doesn't prevent the
    # on-screen move that follows).
    try:
        print(f"[init] hide_from_capture={hide_from_capture(hwnd, True)}", flush=True)
    except Exception as e:
        print(f"[init] hide_from_capture failed: {e}", flush=True)
    try:
        print(f"[init] hide_from_taskbar={hide_from_taskbar(hwnd)}", flush=True)
    except Exception as e:
        print(f"[init] hide_from_taskbar failed: {e}", flush=True)
    try:
        make_non_activating(hwnd)
        print("[init] NOACTIVATE applied (permanent)", flush=True)
    except Exception as e:
        print(f"[init] NOACTIVATE failed: {e}", flush=True)

    # Now move the window on-screen. ASYNC so we don't block if the pump
    # is mid-WebView2-cold-init. SWP_NOZORDER keeps pywebview's on_top
    # z-order; SWP_NOACTIVATE avoids stealing foreground at startup.
    try:
        SWP_NOZORDER_LOCAL = 0x0004
        SWP_NOACTIVATE_LOCAL = 0x0010
        SWP_ASYNCWINDOWPOS_LOCAL = 0x4000
        win32gui.SetWindowPos(
            hwnd, 0, int(init_x), int(init_y), int(init_w), int(init_h),
            SWP_NOZORDER_LOCAL | SWP_NOACTIVATE_LOCAL | SWP_ASYNCWINDOWPOS_LOCAL,
        )
        print(f"[init] moved on-screen to ({init_x},{init_y}) {init_w}x{init_h}", flush=True)
    except Exception as e:
        print(f"[init] on-screen move failed: {e}", flush=True)

    try:
        _register_global_hotkey(hwnd)
    except Exception as e:
        print(f"[init] hotkey register failed: {e}", flush=True)

    try:
        _apply_response_popup_tweaks(api)
    except Exception as e:
        print(f"[warn] popup tweak error: {e}", flush=True)


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
        # hide_from_taskbar's SetWindowPos is async (SWP_ASYNCWINDOWPOS) since
        # 1.1.8 so this no longer blocks on a cold WebView2 thread.
        if response_hwnd:
            try:
                api.set_response_hwnd(response_hwnd)
                hide_from_capture(response_hwnd, True)
                hide_from_taskbar(response_hwnd)
                print(f"[init] response HWND={response_hwnd}", flush=True)
            except Exception as e:
                print(f"[warn] response popup protect: {e}", flush=True)

        # Dropdown popup: full treatment (capture-excluded + no-taskbar).
        # WS_EX_TOOLWINDOW only removes from taskbar + Alt+Tab — it does NOT
        # suppress activation, so the JS `blur` handler that closes the
        # dropdown on click-outside keeps working. (Earlier comment here
        # claimed otherwise and was wrong: the main Ghost window has
        # TOOLWINDOW applied and receives focus fine.)
        if dropdown_hwnd:
            try:
                api.set_dropdown_hwnd(dropdown_hwnd)
                hide_from_capture(dropdown_hwnd, True)
                hide_from_taskbar(dropdown_hwnd)
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


def _release_instance_mutex():
    """Release the single-instance mutex handle right now. Called from
    close_app so a rapid "close → reopen" doesn't have to wait for our
    webview2 cleanup (up to ~1.5s) before the new Ghost can acquire the
    mutex. The mutex is normally released at process exit; closing it
    explicitly here shaves off the cleanup window and avoids the "first
    click didn't open" symptom users saw on fast reopen."""
    global _SINGLE_INSTANCE_MUTEX
    if _SINGLE_INSTANCE_MUTEX is None:
        return
    try:
        import win32api
        try:
            win32api.CloseHandle(_SINGLE_INSTANCE_MUTEX)
        except Exception:
            pass
        _SINGLE_INSTANCE_MUTEX = None
    except Exception:
        pass


def _ensure_single_instance_windows() -> bool:
    """Windows: use a named mutex to detect a running Ghost.

    If another instance is detected, signal the named event so the running
    Ghost shows itself, then exit. Otherwise acquire the mutex and proceed.

    Retry logic: if the mutex appears held, we wait up to ~1.5s for it to be
    released. The previous Ghost may have JUST called `close_app` (taskkill /T
    is near-instant but Windows takes ~300-800ms to fully reclaim named-mutex
    handles after the owner dies). Without this retry, a rapid close → reopen
    hits the short window where the mutex still exists from the dying instance,
    signals a non-existent listener, and exits — so the user sees "nothing
    happened" and has to click again.
    """
    if sys.platform != "win32":
        return True
    try:
        import win32api
        import win32event
        import winerror
        global _SINGLE_INSTANCE_MUTEX

        for attempt in range(8):  # 8 × 200ms = 1.6s max wait
            _SINGLE_INSTANCE_MUTEX = win32event.CreateMutex(
                None, False, _INSTANCE_MUTEX_NAME
            )
            if win32api.GetLastError() != winerror.ERROR_ALREADY_EXISTS:
                return True  # We got it — we're the sole instance
            # Close our handle before retrying (otherwise we'd leak)
            try:
                win32api.CloseHandle(_SINGLE_INSTANCE_MUTEX)
            except Exception:
                pass
            _SINGLE_INSTANCE_MUTEX = None
            if attempt < 7:
                time.sleep(0.2)

        # After ~1.6s the mutex is still held → there really is a live Ghost.
        # Signal it to show itself and exit.
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


# =============================================================================
# Startup bulletproofing
# =============================================================================

# _slog and _show_error_box are thin aliases into src.bootstrap so there's a
# single implementation of each. The underscore names are preserved because
# several log-line consumers (CLAUDE.md regression watchlist, grep patterns in
# operational runbooks) match on "[startup] ..." lines emitted by _slog.
from src.bootstrap import slog as _slog, show_error_box as _show_error_box  # noqa: E402,I001


def _preflight_cleanup_webview2() -> None:
    """Kill zombie WebView2 helpers + sweep orphan caches before fresh init.

    Implementation lives in `src.platform.windows.preflight` — this function
    is a thin wrapper preserved for call-site stability.
    """
    _bootstrap_preflight()


_GHOST_JOB_HANDLE = None  # keep the Job Object handle alive for process lifetime


def _assign_process_to_kill_on_close_job() -> bool:
    """Attach this process to a Windows Job Object with KILL_ON_JOB_CLOSE.
    When the process dies by ANY means — clean os._exit, crash, SIGKILL,
    Task Manager "End Task", Task Manager "End Process Tree", pulling the
    plug — Windows automatically terminates every other process in the job.
    That's the ONLY way to guarantee msedgewebview2.exe children don't
    survive as zombies holding locks on `%USERPROFILE%\\.ghost\\webview-cache`,
    which was blocking the next launch after a force-close.

    Graceful-close cleanup (close_app) and the update flow already kill
    helpers explicitly; this Job Object is the safety net that covers the
    THIRD case — the user who alt-f4s three times / kills from Task Manager
    / hits a Windows Error Reporting crash / loses power. We skip this in
    dev (non-frozen) so that re-running main.py from the venv doesn't nuke
    unrelated webview2 processes the IDE or Teams is using.

    Returns True on success. Failure is non-fatal: the app still works, but
    without the cascade-kill guarantee.
    """
    global _GHOST_JOB_HANDLE
    if not getattr(sys, "frozen", False):
        # Dev mode: skip — don't kill webview2 helpers owned by VS Code /
        # Outlook / Teams when the developer stops the debugger.
        return False
    try:
        import ctypes
        from ctypes import wintypes

        kernel32 = ctypes.windll.kernel32

        # Windows SDK constants
        JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
        JobObjectExtendedLimitInformation = 9

        class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("PerProcessUserTimeLimit", ctypes.c_longlong),
                ("PerJobUserTimeLimit", ctypes.c_longlong),
                ("LimitFlags", wintypes.DWORD),
                ("MinimumWorkingSetSize", ctypes.c_size_t),
                ("MaximumWorkingSetSize", ctypes.c_size_t),
                ("ActiveProcessLimit", wintypes.DWORD),
                ("Affinity", ctypes.c_size_t),
                ("PriorityClass", wintypes.DWORD),
                ("SchedulingClass", wintypes.DWORD),
            ]

        class IO_COUNTERS(ctypes.Structure):
            _fields_ = [
                ("ReadOperationCount", ctypes.c_ulonglong),
                ("WriteOperationCount", ctypes.c_ulonglong),
                ("OtherOperationCount", ctypes.c_ulonglong),
                ("ReadTransferCount", ctypes.c_ulonglong),
                ("WriteTransferCount", ctypes.c_ulonglong),
                ("OtherTransferCount", ctypes.c_ulonglong),
            ]

        class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
            _fields_ = [
                ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                ("IoInfo", IO_COUNTERS),
                ("ProcessMemoryLimit", ctypes.c_size_t),
                ("JobMemoryLimit", ctypes.c_size_t),
                ("PeakProcessMemoryUsed", ctypes.c_size_t),
                ("PeakJobMemoryUsed", ctypes.c_size_t),
            ]

        # Typing hints for ctypes so return values aren't auto-truncated to int32.
        kernel32.CreateJobObjectW.restype = wintypes.HANDLE
        kernel32.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel32.SetInformationJobObject.restype = wintypes.BOOL
        kernel32.GetCurrentProcess.restype = wintypes.HANDLE
        kernel32.CloseHandle.restype = wintypes.BOOL

        job = kernel32.CreateJobObjectW(None, None)
        if not job:
            _slog("job: CreateJobObjectW failed")
            return False

        info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
        info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
        if not kernel32.SetInformationJobObject(
            job, JobObjectExtendedLimitInformation,
            ctypes.byref(info), ctypes.sizeof(info),
        ):
            kernel32.CloseHandle(job)
            _slog("job: SetInformationJobObject failed")
            return False

        if not kernel32.AssignProcessToJobObject(job, kernel32.GetCurrentProcess()):
            # Windows returns ERROR_ACCESS_DENIED (5) when the process is
            # already in a job that forbids breakaway. Modern Windows 10+
            # supports nested jobs so this is rare, but if it happens we
            # just skip — the app still works, we just lose cascade-kill.
            err = ctypes.get_last_error()
            kernel32.CloseHandle(job)
            _slog(f"job: AssignProcessToJobObject failed (err={err}) — process may already be in a parent job")
            return False

        # Keep the handle alive for the lifetime of the process. When the
        # process exits, this handle closes, triggering KILL_ON_JOB_CLOSE
        # which terminates every other process in the job.
        _GHOST_JOB_HANDLE = job
        _slog("job: KILL_ON_JOB_CLOSE assigned — orphan webview2 zombies are now impossible")
        return True
    except Exception as e:
        _slog(f"job: setup error (non-fatal): {e}")
        return False


def _check_webview2_runtime() -> bool:
    """Return True if the WebView2 runtime is installed.

    Implementation lives in `src.platform.windows.preflight` — thin wrapper
    preserved for call-site stability.
    """
    return _bootstrap_check_runtime()


def main():
    # Intercept sub-mode flags BEFORE any Ghost-app init. capture_area spawns
    # sys.executable with --region-selector to run the tkinter region picker
    # in a separate process (tkinter needs the main thread). In dev that's
    # python.exe, so the old `-m src.region_selector_cli` approach worked; in
    # the PyInstaller frozen build `sys.executable` is Ghost.exe, which can't
    # honor -m because the bundled binary doesn't route argv through Python's
    # runpy. Handling the flag here runs the selector inside a freshly spawned
    # Ghost.exe and exits without bringing up the whole app.
    if "--region-selector" in sys.argv:
        from src.region_selector_cli import main as _region_main
        _region_main()
        return

    # Startup log marker so we can tell apart sessions in ghost.log
    _slog(f"=== Ghost starting (pid={os.getpid()}, frozen={getattr(sys, 'frozen', False)}) ===")

    # (0) Single-instance guard — MUST run before the preflight cleanup.
    # If a Ghost is already open and the user double-launches the .exe, the
    # preflight below would taskkill every msedgewebview2.exe globally,
    # orphaning the running instance's webview and triggering "Ghost.exe não
    # está respondendo" on the existing window. Checking the mutex first lets
    # the duplicate launch exit cleanly (after signaling the running Ghost to
    # show itself) without touching shared process state. Mac: NSApplication
    # handles double-launch by default.
    _ensure_single_instance_windows()

    # (1) Clean up zombie WebView2 helpers from previous crashes/closes BEFORE
    # we touch webview.* — this is the main fix for "crashes on reopen, need to
    # reinstall" reports. Reinstalling was just buying time for the OS to
    # release locked file handles; now we force that release up front.
    _preflight_cleanup_webview2()

    # (1b) [REMOVED] Windows Job Object with KILL_ON_JOB_CLOSE. Attempted in
    # 1.1.4 to cascade-kill webview2 on any Ghost death, but WebView2's
    # sandbox uses CREATE_BREAKAWAY_FROM_JOB when spawning msedgewebview2
    # helpers, which fails when the parent is in a job without
    # JOB_OBJECT_LIMIT_BREAKAWAY_OK — causing Ghost to crash during
    # webview.start(). Setting BREAKAWAY_OK defeats the whole point because
    # helpers would then escape the job. Force-close cleanup is instead
    # handled by the next-launch preflight sweep (which kills zombie
    # webview2 helpers before creating the new window).
    # _assign_process_to_kill_on_close_job()  # intentionally disabled

    # (2) Validate WebView2 runtime is installed. On modern Win10/11 it's
    # pre-installed, but some LTSC or stripped installs may lack it.
    if not _check_webview2_runtime():
        _slog("WebView2 runtime NOT FOUND — showing error and exiting")
        _show_error_box(
            "Ghost — WebView2 ausente",
            "O Ghost precisa do Microsoft Edge WebView2 Runtime para funcionar.\n\n"
            "Baixe e instale: https://go.microsoft.com/fwlink/p/?LinkId=2124703\n\n"
            "Depois, abra o Ghost novamente.",
        )
        sys.exit(1)

    # Redirect stderr to a log file so crashes are captured even under pythonw.
    # We append now (not truncate) to preserve the startup log we wrote above.
    try:
        sys.stderr = open(LOG_FILE, "a", encoding="utf-8", buffering=1)
        sys.stdout = sys.stderr
    except Exception as e:
        _slog(f"stderr redirect failed (non-fatal): {e}")

    try:
        _slog("creating GhostAPI")
        api = GhostAPI()
        _watch_show_event_windows(lambda: api._hwnd)

        # Pre-size the window to the monitor's work area (taskbar excluded),
        # with a small "breathing" margin all around so the app feels like a
        # framed window rather than hard-edged fullscreen. Two wrinkles handled
        # here:
        #   1. DPI scaling — GetMonitorInfo returns physical pixels. pywebview's
        #      create_window expects LOGICAL pixels (DIPs). On a 125/150/200%
        #      scaled HiDPI screen the two differ, and passing physical values
        #      makes the window overflow the display. We compute the DPI scale
        #      via GetDeviceCaps(LOGPIXELSX) and divide to get logical units.
        #   2. Taskbar — SW_MAXIMIZE on a frameless window (WS_POPUP style)
        #      covers the taskbar because there's no non-client area for
        #      Windows to snap against. Sizing to the WORK area rect explicitly
        #      keeps the taskbar visible, which is what the user expects when
        #      they say "maximized".
        # The margin ("respiros") is applied symmetrically around the work
        # area so the window doesn't press flush against the screen edges.
        EDGE_MARGIN = 16
        # 740x1000 — restored-window fallback. Taller than wide for a
        # rectangular feel. Used by exit_maximized when there's no prior
        # saved rect.
        init_w, init_h = 740, 1000
        init_x, init_y = 100, 100
        try:
            import ctypes
            import win32api
            import win32con as _wc
            _hdc = ctypes.windll.user32.GetDC(None)
            _dpi = ctypes.windll.gdi32.GetDeviceCaps(_hdc, 88)  # LOGPIXELSX
            ctypes.windll.user32.ReleaseDC(None, _hdc)
            _scale = (_dpi / 96.0) if _dpi > 0 else 1.0

            _hmon = win32api.MonitorFromPoint((0, 0), _wc.MONITOR_DEFAULTTOPRIMARY)
            _info = win32api.GetMonitorInfo(_hmon)
            _wl, _wt, _wr, _wb = _info.get("Work", (0, 0, 1920, 1080))
            # Convert physical work-area pixels → logical DIPs for pywebview
            lx = int(_wl / _scale)
            ly = int(_wt / _scale)
            lw = int((_wr - _wl) / _scale)
            lh = int((_wb - _wt) / _scale)
            # Breathing-room margin (respiros), symmetric
            init_x = lx + EDGE_MARGIN
            init_y = ly + EDGE_MARGIN
            init_w = max(720, lw - EDGE_MARGIN * 2)
            init_h = max(600, lh - EDGE_MARGIN * 2)
            _slog(
                f"work area (physical) {_wr - _wl}x{_wb - _wt} @ dpi={_dpi} scale={_scale:.2f} "
                f"→ logical window {init_w}x{init_h} at ({init_x},{init_y}) [margin={EDGE_MARGIN}]"
            )
        except Exception as e:
            _slog(f"work area / DPI query failed ({e}), falling back to 720x720")

        _slog("creating main window (parked off-screen during WebView2 cold init)")
        # PARK OFF-SCREEN: the main window is created at (-20000, -20000)
        # so that during the 3–10s that WebView2 spends in its cold-init
        # blocking code (cold boot, fresh install, or post-auto-update),
        # there is no visible, unresponsive window for Windows to stamp
        # with the (Não Respondendo) overlay. The _apply_window_tweaks
        # thread below applies WDA_EXCLUDEFROMCAPTURE + NOACTIVATE +
        # no-taskbar while we're still off-screen, then SetWindowPos's
        # the window onto its real rect (init_x, init_y, init_w, init_h)
        # via SWP_ASYNCWINDOWPOS so the user's first visible frame is
        # already protected AND the pump is already responsive.
        #
        # The v1.1.19 attempt at this same goal used `hidden=True` +
        # an on_ready callback; that broke on some setups (window
        # never became visible) and was reverted in v1.1.20. Off-screen
        # is immune to that class of bug because the window is
        # `WS_VISIBLE` throughout — only its coordinates are unusual.
        window = webview.create_window(
            "Ghost",
            str(WEB_INDEX),
            js_api=api,
            width=init_w,
            height=init_h,
            x=-20000,
            y=-20000,
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
        _slog("creating response popup window")
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
        # chip options outside the compact bar's bounds.
        _slog("creating dropdown popup window")
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

        # Tweaks thread: finds HWND, applies protection styles, THEN
        # moves window on-screen to (init_x, init_y, init_w, init_h).
        # Critical: pass the REAL init_x/init_y (DPI-corrected work-area
        # coordinates), not placeholders — the thread uses them for the
        # final on-screen SetWindowPos.
        threading.Thread(
            target=_apply_window_tweaks,
            args=(api, init_x, init_y, init_w, init_h),
            daemon=True,
        ).start()

        # Safety fallback: if the tweaks thread fails to find HWND within
        # 5s (e.g., Win32 enum explodes on some locked-down antivirus
        # setup), fall back to pywebview's cross-thread window.move +
        # resize so the user never ends up staring at a blank desktop
        # while Ghost is alive but off-screen. The tweaks thread nearly
        # always wins this race — typical HWND-acquire takes <1s — so
        # this only fires as genuine disaster recovery.
        def _safety_move():
            try:
                time.sleep(5.0)
                if not api._hwnd:
                    print("[safety] tweaks thread never set HWND — using "
                          "pywebview window.move() fallback", flush=True)
                    try:
                        window.move(init_x, init_y)
                    except Exception as e:
                        print(f"[safety] window.move failed: {e}", flush=True)
                    try:
                        window.resize(init_w, init_h)
                    except Exception as e:
                        print(f"[safety] window.resize failed: {e}", flush=True)
            except Exception as e:
                print(f"[safety] move fallback error: {e}", flush=True)

        threading.Thread(target=_safety_move, daemon=True).start()

        debug_mode = "--debug" in sys.argv
        # Pin WebView2's UserDataFolder to a stable location under ~/.ghost
        # instead of letting pywebview default to a fresh tempfile.mkdtemp
        # each launch. Three wins:
        #   1. Cold-start is now actually warm — fonts, GPU shader cache, codec
        #      profiles persist across runs, shaving ~5-10s off startup. This
        #      was the cause of the "app não está respondendo" dialog users
        #      saw right after auto-updates (fresh install → every tmp* cache
        #      was brand-new → WebView2 spent a small eternity initializing
        #      before the message pump started responding).
        #   2. No more orphan `%TEMP%\tmp<random>\EBWebView` folders piling
        #      up across sessions (we were up to 200+ on the dev machine).
        #   3. Installer's ssPostInstall sweep and the preflight sweep stay
        #      in place as defensive cleanup for anything that DID land in
        #      %TEMP% (pre-1.1.x versions, or future bugs).
        _wv_cache = USER_DATA / "webview-cache"
        try:
            _wv_cache.mkdir(parents=True, exist_ok=True)
            _slog(f"webview storage_path = {_wv_cache}")
        except Exception as e:
            _slog(f"webview storage_path mkdir failed ({e}); pywebview will fall back to tempfile")
        _slog("calling webview.start()")
        webview.start(debug=debug_mode, gui="edgechromium",
                      storage_path=str(_wv_cache))
        _slog("webview.start() returned (user closed Ghost)")
    except SystemExit:
        raise
    except Exception as e:
        # Something blew up during init. Log the full traceback and show a
        # MessageBox so the user sees what went wrong instead of a silent
        # crash. Without this the Ghost exe would just vanish with no clue.
        tb = traceback.format_exc()
        _slog(f"FATAL during startup: {type(e).__name__}: {e}\n{tb}")
        _show_error_box(
            "Ghost — erro ao iniciar",
            f"Algo quebrou ao iniciar o Ghost:\n\n{type(e).__name__}: {e}\n\n"
            f"Detalhes foram salvos em:\n{LOG_FILE}\n\n"
            "Se o erro persistir, reinstale o Ghost pela última versão em\n"
            "https://github.com/userJesus/ghost/releases/latest",
        )
        sys.exit(1)


if __name__ == "__main__":
    main()
