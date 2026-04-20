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
    """Poll for our window's HWND until found (up to 10s).

    The win32 calls here (hide_from_capture / hide_from_taskbar /
    make_non_activating) all touch the window's style, and we run in a
    background thread. Since 1.1.8 `hide_from_taskbar` uses SWP_ASYNCWINDOWPOS
    so its SetWindowPos doesn't block waiting for WebView2's UI thread to
    drain — earlier versions could freeze the entire init chain at
    `hide_from_capture=True` when WebView2 was cold-initializing on a
    resource-contended system, because the next SetWindowPos was sent
    synchronously and never returned until the UI thread was free. The
    ASYNC flag makes that post-and-return."""
    for attempt in range(50):
        time.sleep(0.2)
        hwnd = _find_own_top_window()
        if hwnd:
            api.set_hwnd(hwnd)
            print(f"[init] HWND={hwnd} (after {attempt + 1} attempts)", flush=True)
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

def _slog(msg: str) -> None:
    """Append a timestamped line to ghost.log so we can diagnose startup crashes
    from a user's log even when stderr redirect failed."""
    try:
        USER_DATA.mkdir(parents=True, exist_ok=True)
        log = USER_DATA / "ghost.log"
        with open(log, "a", encoding="utf-8") as f:
            f.write(f"[{datetime.now().isoformat()}] [startup] {msg}\n")
    except Exception:
        pass
    # Also mirror to stderr (may be redirected to log file later)
    print(f"[startup] {msg}", flush=True)


def _show_error_box(title: str, message: str) -> None:
    """Native Win32 MessageBox to surface startup errors to the user. Without
    this, a crash in webview.create_window/start is silent and the user just
    sees the app "not open" with no clue what happened."""
    try:
        import ctypes
        MB_OK = 0x00000000
        MB_ICONERROR = 0x00000010
        MB_TOPMOST = 0x00040000
        ctypes.windll.user32.MessageBoxW(
            None, message, title, MB_OK | MB_ICONERROR | MB_TOPMOST
        )
    except Exception:
        pass


def _preflight_cleanup_webview2() -> None:
    """Before creating any webview window, forcibly clean up state from
    previous Ghost sessions. Without this, rapid close→reopen cycles leave
    zombie helpers holding locks on the WebView2 UserData folder and DLLs,
    causing the new instance's UI thread to hang ("Ghost não está respondendo").

    Approach: fire taskkill for each helper image, then sleep a fixed 600ms
    to let the OS finish tearing down. A previous version of this function
    polled `tasklist` until helpers were gone — but each tasklist call takes
    ~200ms and we were calling it 30+ times, blocking startup for 6-8 seconds
    and triggering the very "not responding" state we were trying to prevent.
    A fixed wait is both faster AND more reliable."""
    CREATE_NO_WINDOW = 0x08000000

    # Kill webview2 + CefSharp helpers by name. Safe — Ghost is the primary
    # webview2 user here; any other app using webview2 will simply relaunch
    # its own helper processes.
    for image in ("msedgewebview2.exe", "WebView2Host.exe",
                  "CefSharp.BrowserSubprocess.exe"):
        try:
            r = subprocess.run(
                ["taskkill", "/F", "/IM", image],
                capture_output=True, timeout=3,
                creationflags=CREATE_NO_WINDOW,
            )
            if r.returncode == 0:
                _slog(f"preflight: killed stale {image}")
        except Exception as e:
            _slog(f"preflight: taskkill {image} error: {e}")

    # Fixed 600ms settle so the OS can finish releasing file handles from the
    # processes we just killed. Empirically this is plenty — processes tear
    # down in ~100-300ms on modern Windows. No polling needed.
    time.sleep(0.6)

    # Orphan-cache sweep: pywebview creates a fresh `%TEMP%\tmp<random>\EBWebView`
    # folder per session via tempfile.mkdtemp and never removes it. Over time
    # (crashes, force-kills, dev restarts) these accumulate — we've seen 200+
    # folders pile up, each ~50-80MB, which besides wasting disk actually starts
    # slowing WebView2 initialization (the runtime apparently scans/walks temp).
    # Safe to nuke now: we just killed every msedgewebview2 process above, so
    # nothing holds locks on these dirs. We're conservative and only delete
    # folders that (a) match the tempfile mkdtemp pattern and (b) contain an
    # EBWebView subdir — anyone else's tmp stays untouched.
    try:
        import glob
        import shutil
        temp_root = os.environ.get("TEMP") or os.environ.get("TMP")
        if temp_root and os.path.isdir(temp_root):
            # Two-pass sweep: first pass deletes what it can; second pass
            # retries dirs that failed (usually because a webview2 child was
            # still tearing down). Between passes we wait an extra beat so
            # the OS can finish releasing handles from the taskkills above.
            # We always log — both success counts and leftovers — so post-
            # update crash reports carry enough signal to diagnose a "my new
            # Ghost didn't start" issue without forcing the user to send
            # stderr dumps.
            remaining = []
            swept_total = 0
            for pass_idx in range(2):
                swept_pass = 0
                leftovers = []
                for candidate in glob.glob(os.path.join(temp_root, "tmp*")):
                    # Only act on dirs that look like pywebview's WebView2
                    # UserData temps — presence of EBWebView child proves it.
                    if not os.path.isdir(os.path.join(candidate, "EBWebView")):
                        continue
                    shutil.rmtree(candidate, ignore_errors=True)
                    if os.path.isdir(candidate):
                        leftovers.append(candidate)
                    else:
                        swept_pass += 1
                swept_total += swept_pass
                remaining = leftovers
                if not leftovers:
                    break
                # Second chance: give the OS 400ms to release any stragglers.
                time.sleep(0.4)
            if swept_total:
                _slog(f"preflight: swept {swept_total} orphan WebView2 cache dir(s)")
            if remaining:
                # Non-fatal but important signal for diagnosing post-update
                # crashes — a locked cache dir means a webview2 handle leaked
                # somewhere and the new session might race against it.
                _slog(f"preflight: WARNING {len(remaining)} cache dir(s) could not be deleted (locked?); first: {remaining[0]}")
    except Exception as e:
        _slog(f"preflight: cache sweep error (non-fatal): {e}")


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
    """Return True if the WebView2 runtime is installed. If not, show a
    MessageBox pointing the user to the evergreen installer URL.

    Without the runtime Edge-Chromium backend won't load and pywebview will
    fail deep inside its C++ bindings with a confusing error."""
    try:
        import winreg
        # Evergreen WebView2 registers under HKLM\SOFTWARE\WOW6432Node\...
        paths = [
            r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
            r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
        ]
        for p in paths:
            try:
                k = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, p)
                winreg.CloseKey(k)
                return True
            except OSError:
                continue
        # Per-user install
        for p in paths:
            try:
                k = winreg.OpenKey(winreg.HKEY_CURRENT_USER, p)
                winreg.CloseKey(k)
                return True
            except OSError:
                continue
        return False
    except Exception:
        # If we can't check, assume it's there to avoid false alarms
        return True


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

    # Single-instance guard — exits if another Ghost is already running and
    # signals it to show itself. Mac: NSApplication handles double-launch by
    # default (brings existing instance to front).
    _ensure_single_instance_windows()

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

        _slog("creating main window (pre-sized to logical work area)")
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

        threading.Thread(target=_apply_window_tweaks,
                         args=(api, 100, 100, init_w, init_h),
                         daemon=True).start()

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
