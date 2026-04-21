"""Screen capture + watch mode + monitor/window enumeration + analyze-last methods.

These methods were extracted from GhostAPI (src/api.py) to keep api.py
navigable. They remain METHODS of GhostAPI via mixin inheritance — so
`self` is still the GhostAPI instance and every `self._X` state access
continues to work unchanged. No behavioral change vs. the pre-split file.

Do NOT instantiate CaptureMixin directly. It exists only as a mixin base
for `class GhostAPI(WindowMixin, CaptureMixin, ChatMixin, MeetingMixin):`.
"""
from __future__ import annotations

# ── Imports migrated from api.py top-level so extracted method bodies
# ── resolve names like `threading`, `os`, `force_foreground`, etc. exactly
# ── as they did when they lived in api.py. Do not trim without checking
# ── every reference in method bodies below first.
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from PIL import Image

from src import history as _history
from src.capture import (
    capture_fullscreen,
    capture_region,
    image_to_base64,
    image_to_data_url,
)
from src.clone import WebCloner, clones_dir
from src.config import PRESETS
from src.gpt_client import build_user_message, chat_completion
from src.meeting import MeetingRecorder, format_time
from src.meeting_processor import (
    meetings_dir,
    summarize_meeting,
    transcribe_audio_verbose,
    transcribe_chunks_verbose,
    write_markdown_doc,
)
from src.scroll_capture import (
    capture_monitor,
    list_monitors,
    scroll_and_capture,
    stitch_vertical,
)
from src.voice import VoiceRecorder
from src.win_focus import (
    drag_window_loop,
    force_foreground,
    hide_window,
)

# Error-logging helper used by every bridge method. Imported from api.py
# so the log format stays uniform across mixins.
from src.api import _log_error  # noqa: F401 — used inside method bodies below



class CaptureMixin:
    """Mixin base — injects the following methods onto GhostAPI:
      * capture_fullscreen
      * capture_area
      * capture_with_scroll
      * list_windows
      * list_meeting_windows
      * get_monitors
      * toggle_watch
      * get_watch_status
      * get_watch_thumbnail
      * _watch_loop
      * _current_monitor
      * set_capture_visibility
      * analyze_last_capture
    """

    def capture_fullscreen(self) -> dict:
        """Ghost is invisible to captures (WDA_EXCLUDEFROMCAPTURE), so no hide/show needed.
        Avoids window.show() which internally activates the window on WinForms.
        """
        try:
            img = capture_fullscreen()
            self._last_image = img
            thumb = image_to_data_url(img, max_dim=480)
            return {"thumbnail": thumb, "width": img.width, "height": img.height}
        except Exception as e:
            return {"error": _log_error("capture_fullscreen", e)}

    def capture_area(self) -> dict:
        """Launch region selector in subprocess (tkinter needs main thread).
        Doesn't hide Ghost — it's already invisible to captures via WDA flag.

        In dev `sys.executable` is python.exe and `-m src.region_selector_cli`
        works. In the frozen PyInstaller build `sys.executable` is Ghost.exe,
        which ignores `-m` (the bundled binary doesn't route argv through
        runpy), so we pass a `--region-selector` flag that main.py intercepts
        at the top of main() and routes straight to the selector CLI.
        """
        try:
            if getattr(sys, "frozen", False):
                cmd = [sys.executable, "--region-selector"]
            else:
                cmd = [sys.executable, "-m", "src.region_selector_cli"]
            # Suppress the PyInstaller "unhandled exception" error dialog on
            # the child so a broken selector doesn't block the main UI thread
            # with a modal that only the user can dismiss. If the subprocess
            # fails, we want it to exit quickly so busy=true clears on the JS
            # side and the user can keep chatting.
            CREATE_NO_WINDOW = 0x08000000
            result = subprocess.run(
                cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=120,
                creationflags=CREATE_NO_WINDOW,
            )
            # returncode 1 = user cancelled (ESC in the selector). Anything
            # else non-zero is a genuine error — surface it to the UI so
            # bugs don't silently look like cancellation (that's how the
            # 1.0.29 tkinter-missing crash went undiagnosed for a release).
            if result.returncode == 1 and not result.stdout.strip():
                return {"cancelled": True}
            if result.returncode != 0:
                err = (result.stderr or "").strip()
                _log_error("capture_area", Exception(f"subprocess rc={result.returncode}: {err[:300]}"))
                return {"error": f"Seletor de região falhou: {err[:200] or 'erro desconhecido'}"}
            if not result.stdout.strip():
                return {"cancelled": True}

            region = json.loads(result.stdout.strip())
            img = capture_region(region["x"], region["y"], region["w"], region["h"])
            self._last_image = img
            thumb = image_to_data_url(img, max_dim=480)
            return {"thumbnail": thumb, "width": img.width, "height": img.height}
        except Exception as e:
            return {"error": _log_error("capture_area", e)}

    def capture_with_scroll(self, monitor_index: int, max_scrolls: int) -> dict:
        """Scroll capture minimizes Ghost via Win32 (doesn't activate on restore)."""
        monitor = next((m for m in self._monitors if m["index"] == monitor_index), None)
        if not monitor:
            return {"error": "Monitor não encontrado"}

        import win32con
        import win32gui as _w
        try:
            # Minimize via Win32 without activation
            if self._hwnd:
                _w.ShowWindow(self._hwnd, win32con.SW_MINIMIZE)
            time.sleep(0.5)

            shots = scroll_and_capture(monitor, max_scrolls=max_scrolls)
            stitched = stitch_vertical(shots)
            if stitched is None:
                return {"error": "Nenhuma captura feita"}
            self._last_image = stitched
            thumb = image_to_data_url(stitched, max_dim=480)
            return {"thumbnail": thumb, "width": stitched.width, "height": stitched.height, "pages": len(shots)}
        except Exception as e:
            return {"error": _log_error("capture_with_scroll", e)}
        finally:
            try:
                # Restore without activating: SW_SHOWNOACTIVATE
                if self._hwnd:
                    _w.ShowWindow(self._hwnd, win32con.SW_SHOWNOACTIVATE)
            except Exception:
                pass

    def list_windows(self) -> list[dict]:
        """Enumerate visible top-level windows (excluding Ghost itself)."""
        try:
            import win32gui
            import win32process
        except Exception:
            return []

        my_pid = os.getpid()
        ghost_hwnd = self._hwnd
        windows: list[dict] = []

        def callback(hwnd, _):
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                if hwnd == ghost_hwnd:
                    return True
                title = win32gui.GetWindowText(hwnd)
                if not title or len(title) < 2:
                    return True
                rect = win32gui.GetWindowRect(hwnd)
                w = rect[2] - rect[0]
                h = rect[3] - rect[1]
                if w < 100 or h < 100:
                    return True
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid == my_pid:
                    return True
                windows.append({
                    "hwnd": hwnd,
                    "title": title,
                    "width": w,
                    "height": h,
                })
            except Exception:
                pass
            return True

        win32gui.EnumWindows(callback, None)
        windows.sort(key=lambda w: w["title"].lower())
        return windows

    def list_meeting_windows(self) -> list[dict]:
        """Enumerate visible top-level windows eligible as a meeting capture
        target, annotated with the monitor they live on and whether they
        look like a meeting app/page.

        The UI groups these under the monitor the user picks, so we return
        ALL reasonably-sized visible windows (not just meeting-matching
        ones): the user's pre-join browser tab, a Chrome window that
        doesn't yet show "Meet - ...", or any other window they want to
        capture should appear. The `is_meeting` flag lets the frontend
        surface detected meetings first without hiding the rest."""
        try:
            import ctypes
            import win32gui
            import win32process
        except Exception:
            return []

        my_pid = os.getpid()
        ghost_hwnd = self._hwnd

        # Resolve PID → lowercased image basename. Cache to avoid repeated
        # OpenProcess calls for the same pid (common when a browser has many
        # windows owned by the main process).
        image_cache: dict[int, str] = {}

        def _image_name_for(pid: int) -> str:
            if pid in image_cache:
                return image_cache[pid]
            try:
                PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
                h = ctypes.windll.kernel32.OpenProcess(
                    PROCESS_QUERY_LIMITED_INFORMATION, False, pid
                )
                if not h:
                    image_cache[pid] = ""
                    return ""
                try:
                    buf = ctypes.create_unicode_buffer(520)
                    size = ctypes.c_ulong(len(buf))
                    ok = ctypes.windll.kernel32.QueryFullProcessImageNameW(
                        h, 0, buf, ctypes.byref(size)
                    )
                    path = buf.value if ok else ""
                finally:
                    ctypes.windll.kernel32.CloseHandle(h)
                name = os.path.basename(path).lower() if path else ""
                image_cache[pid] = name
                return name
            except Exception:
                image_cache[pid] = ""
                return ""

        patterns_lower = tuple(p.lower() for p in self._MEETING_TITLE_PATTERNS)
        windows: list[dict] = []

        def callback(hwnd, _):
            try:
                if not win32gui.IsWindowVisible(hwnd) or hwnd == ghost_hwnd:
                    return True
                title = win32gui.GetWindowText(hwnd)
                if not title or len(title) < 2:
                    return True
                rect = win32gui.GetWindowRect(hwnd)
                left, top = rect[0], rect[1]
                w, h_ = rect[2] - left, rect[3] - top
                if w < 100 or h_ < 100:
                    return True
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid == my_pid:
                    return True

                title_l = title.lower()
                image = _image_name_for(pid)
                is_meeting = (
                    image in self._MEETING_APP_PROCESSES
                    or any(pat in title_l for pat in patterns_lower)
                )

                windows.append({
                    "hwnd": hwnd,
                    "title": title,
                    "left": left,
                    "top": top,
                    "width": w,
                    "height": h_,
                    "process": image,
                    "is_meeting": is_meeting,
                    "monitor": self._monitor_index_for_rect(left, top, w, h_),
                })
            except Exception:
                pass
            return True

        win32gui.EnumWindows(callback, None)
        # Meetings first (so the obvious choice is up top), then the rest by title.
        windows.sort(key=lambda x: (0 if x["is_meeting"] else 1, x["title"].lower()))
        return windows

    def get_monitors(self) -> list[dict]:
        return [{
            "index": m["index"],
            "label": f"Monitor {m['index']} ({m['width']}×{m['height']})",
            "width": m["width"],
            "height": m["height"],
        } for m in self._monitors]

    def toggle_watch(self, enabled: bool, interval: float = 3.0) -> dict:
        try:
            self._watch_interval = max(1.0, float(interval))
            if enabled and not self._watch_running:
                self._watch_running = True
                self._watch_thread = threading.Thread(target=self._watch_loop, daemon=True)
                self._watch_thread.start()
            elif not enabled and self._watch_running:
                self._watch_running = False
                with self._watch_lock:
                    self._watch_image = None
            return self.get_watch_status()
        except Exception as e:
            return {"error": _log_error("toggle_watch", e)}

    def get_watch_status(self) -> dict:
        return {
            "enabled": self._watch_running,
            "interval": self._watch_interval,
            "has_image": self._watch_image is not None,
        }

    def get_watch_thumbnail(self) -> dict:
        with self._watch_lock:
            img = self._watch_image
        if img is None:
            return {"thumbnail": None}
        return {"thumbnail": image_to_data_url(img, max_dim=480)}

    def _watch_loop(self):
        while self._watch_running:
            try:
                monitor = self._current_monitor()
                if monitor:
                    img = capture_monitor(monitor)
                else:
                    img = capture_fullscreen()
                with self._watch_lock:
                    self._watch_image = img
            except Exception as e:
                _log_error("watch_loop", e)
            slept = 0.0
            while slept < self._watch_interval and self._watch_running:
                time.sleep(0.2)
                slept += 0.2

    def _current_monitor(self) -> dict | None:
        """Return the monitor dict containing the Ghost window's center.
        Includes a 'work' tuple (left, top, right, bottom) that excludes the taskbar.
        """
        if not self._hwnd:
            return None
        try:
            import win32api
            import win32con
            import win32gui
            rect = win32gui.GetWindowRect(self._hwnd)
            cx = (rect[0] + rect[2]) // 2
            cy = (rect[1] + rect[3]) // 2
            for m in self._monitors:
                if (m["left"] <= cx < m["left"] + m["width"] and
                        m["top"] <= cy < m["top"] + m["height"]):
                    # Augment with work area (excludes taskbar)
                    try:
                        hmon = win32api.MonitorFromPoint(
                            (cx, cy), win32con.MONITOR_DEFAULTTONEAREST
                        )
                        info = win32api.GetMonitorInfo(hmon)
                        work = info.get("Work")  # (left, top, right, bottom)
                        if work:
                            return {**m, "work": work}
                    except Exception:
                        pass
                    return m
        except Exception:
            pass
        return None

    def set_capture_visibility(self, visible: bool) -> dict:
        """Toggle whether Ghost is visible in screen captures / screen sharing.
        Does NOT affect NOACTIVATE (focus behavior stays the same).
        """
        try:
            from src.win_focus import hide_from_capture
            hide_enabled = not visible
            ok = False
            if self._hwnd:
                ok = hide_from_capture(self._hwnd, hide_enabled, force_redraw=True)
            if self._response_hwnd:
                hide_from_capture(self._response_hwnd, hide_enabled, force_redraw=True)
            return {"ok": bool(ok), "visible": visible}
        except Exception as e:
            return {"error": _log_error("set_capture_visibility", e)}

    def analyze_last_capture(self, preset_name: str, extra_text: str = "") -> dict:
        try:
            if self._last_image is None:
                return {"error": "Nenhuma captura disponível"}
            base = PRESETS.get(preset_name)
            if not base:
                return {"error": f"Preset '{preset_name}' não encontrado"}
            prompt = base
            if extra_text:
                prompt += f"\n\nMensagem do usuário: {extra_text}"

            image_b64 = image_to_base64(self._last_image)
            msg = build_user_message(prompt, image_b64)
            self._history.append(msg)
            messages = self._history[-MAX_HISTORY:]
            response = chat_completion(messages)
            self._history.append({"role": "assistant", "content": response})
            return {"text": response}
        except Exception as e:
            if self._history and self._history[-1].get("role") == "user":
                self._history.pop()
            return {"error": _log_error("analyze_last_capture", e)}

