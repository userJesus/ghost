"""Window/popup/focus/drag/keyboard-capture methods of GhostAPI.

These methods were extracted from GhostAPI (src/api.py) to keep api.py
navigable. They remain METHODS of GhostAPI via mixin inheritance — so
`self` is still the GhostAPI instance and every `self._X` state access
continues to work unchanged. No behavioral change vs. the pre-split file.

Do NOT instantiate WindowMixin directly. It exists only as a mixin base
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
# Module-level names from api.py that extracted method bodies reference:
# _log_error (uniform error log format), MAX_HISTORY (chat buffer cap),
# ROOT (bundled-asset root for subprocess), _pid_alive / _snapshot_own_webview2_pids
# (close_app process cleanup). All defined in api.py BEFORE its mixin imports,
# so this import resolves cleanly during api.py's load.
from src.api import MAX_HISTORY, ROOT, _pid_alive, _snapshot_own_webview2_pids, _log_error  # noqa: F401



class WindowMixin:
    """Mixin base — injects the following methods onto GhostAPI:
      * show_dropdown_popup
      * hide_dropdown_popup
      * dropdown_pick
      * minimize
      * hide_app
      * close_app
      * minimize_to_edge
      * enter_maximized
      * exit_maximized
      * enter_compact_bar
      * exit_compact_bar
      * show_response_popup
      * update_response_popup
      * hide_response_popup
      * update_popup_title
      * restore_from_edge
      * force_focus
      * restore_focus
      * start_window_drag
      * enable_typing
      * start_kb_capture
      * stop_kb_capture
      * get_main_window_rect
      * _monitor_index_for_rect
    """

    def show_dropdown_popup(
        self, kind: str, items: list, selected, screen_x: int, screen_y: int,
        width: int = 240, height: int = 300,
    ) -> dict:
        """Position + show the floating dropdown popup at (screen_x, screen_y)."""
        try:
            if self._dropdown_window is None:
                return {"error": "dropdown window not pre-created"}
            payload = json.dumps({"kind": kind, "items": items, "selected": selected})
            try:
                self._dropdown_window.evaluate_js(f"window.setDropdown({payload})")
            except Exception:
                pass
            import win32gui
            if self._dropdown_hwnd:
                # Position first (without activating), then bring to front AND
                # activate so the popup can gain focus — needed so the JS
                # 'blur' handler inside dropdown.html fires on click-outside.
                SWP_NOZORDER_LOCAL = 0x0004
                SWP_NOACTIVATE_LOCAL = 0x0010
                win32gui.SetWindowPos(
                    self._dropdown_hwnd, 0,
                    int(screen_x), int(screen_y), int(width), int(height),
                    SWP_NOZORDER_LOCAL | SWP_NOACTIVATE_LOCAL,
                )
                import win32con
                win32gui.ShowWindow(self._dropdown_hwnd, win32con.SW_SHOWNORMAL)
                try:
                    win32gui.SetForegroundWindow(self._dropdown_hwnd)
                except Exception:
                    pass
            else:
                try:
                    self._dropdown_window.show()
                except Exception:
                    pass
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("show_dropdown_popup", e)}

    def hide_dropdown_popup(self) -> dict:
        """Hide + park off-screen so its hidden rect doesn't show as black."""
        try:
            if self._dropdown_hwnd:
                import win32con
                import win32gui
                win32gui.ShowWindow(self._dropdown_hwnd, win32con.SW_HIDE)
                SWP_NOSIZE_LOCAL = 0x0001
                SWP_NOZORDER_LOCAL = 0x0004
                SWP_NOACTIVATE_LOCAL = 0x0010
                win32gui.SetWindowPos(
                    self._dropdown_hwnd, 0, -10000, -10000, 0, 0,
                    SWP_NOSIZE_LOCAL | SWP_NOZORDER_LOCAL | SWP_NOACTIVATE_LOCAL,
                )
            elif self._dropdown_window is not None:
                try:
                    self._dropdown_window.hide()
                except Exception:
                    pass
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("hide_dropdown_popup", e)}

    def dropdown_pick(self, kind: str, value) -> dict:
        """Called by the popup window when the user picks an option. Routes
        the selection to the main window via evaluate_js, then hides."""
        try:
            if self._window is not None:
                code = f"window.applyDropdownResult({json.dumps(kind)}, {json.dumps(value)})"
                try:
                    self._window.evaluate_js(code)
                except Exception:
                    pass
            self.hide_dropdown_popup()
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("dropdown_pick", e)}

    def minimize(self):
        """With WS_EX_TOOLWINDOW applied, there's no taskbar icon to minimize to.
        Instead, hide the window. User restores via Ctrl+Shift+G hotkey.
        """
        try:
            if self._hwnd:
                hide_window(self._hwnd)
        except Exception as e:
            _log_error("minimize", e)

    def hide_app(self) -> dict:
        """Hide main window (+ popup) like Ctrl+Shift+G.
        Usuário pode restaurar com o atalho global."""
        try:
            if self._hwnd:
                hide_window(self._hwnd)
            if self._response_hwnd:
                try:
                    hide_window(self._response_hwnd)
                except Exception:
                    pass
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("hide_app", e)}

    def close_app(self) -> dict:
        """Close cleanly so the NEXT launch of Ghost starts fast, even if
        the user triggers it immediately (the "clica no X → clica no ícone
        de novo → não abre de primeira" pattern). Three pieces, in order:

        1. Release the single-instance mutex NOW (not at process exit) so
           a new Ghost doesn't have to wait ~1.5s for our cleanup to finish
           before it can even attempt its own init.
        2. Snapshot OUR own webview2 children by walking the process tree.
           Kill only those PIDs — not by image name globally — so a new
           Ghost that's already starting up with its own fresh helpers
           doesn't get its webview2 nuked by our dying session.
        3. Poll for those specific PIDs to actually exit (not a global
           image-name check), so other apps' webview2 helpers (Outlook,
           Teams) don't keep us waiting the full timeout every time.
        """
        try:
            # Snapshot our own webview2 descendants BEFORE destroying the
            # windows — once pywebview tears down, its handles may close
            # and the children can re-parent under winlogon/explorer,
            # making them harder to identify.
            own_pids = _snapshot_own_webview2_pids()

            # Release the mutex early. New Ghost can now acquire it
            # without waiting for the rest of our cleanup.
            try:
                import main as _ghost_main
                _ghost_main._release_instance_mutex()
            except Exception:
                pass

            # Destroy pywebview windows
            import webview
            for w in list(webview.windows):
                try: w.destroy()
                except Exception: pass

            import os
            import subprocess
            import threading
            import time
            CREATE_NO_WINDOW = 0x08000000

            def _nuke():
                time.sleep(0.25)

                # Kill only OUR captured PIDs. If none were captured, fall
                # back to the image-name sweep as a safety net — better to
                # over-kill than leak zombies in an edge case.
                if own_pids:
                    for p in own_pids:
                        try:
                            subprocess.run(
                                ["taskkill", "/F", "/PID", str(p)],
                                capture_output=True, timeout=2,
                                creationflags=CREATE_NO_WINDOW,
                            )
                        except Exception:
                            pass
                else:
                    for image in ("msedgewebview2.exe", "WebView2Host.exe",
                                  "CefSharp.BrowserSubprocess.exe"):
                        try:
                            subprocess.run(
                                ["taskkill", "/F", "/IM", image],
                                capture_output=True, timeout=2,
                                creationflags=CREATE_NO_WINDOW,
                            )
                        except Exception:
                            pass

                # Poll specifically for our captured PIDs to exit. Bail
                # fast if all are gone; otherwise give the OS up to 1.5s
                # total. A new Ghost that spawns its own helpers won't
                # delay us because we only look at OUR original PIDs.
                if own_pids:
                    deadline = time.monotonic() + 1.5
                    while time.monotonic() < deadline:
                        time.sleep(0.15)
                        still_alive = [p for p in own_pids if _pid_alive(p)]
                        if not still_alive:
                            break
                        own_pids[:] = still_alive

                os._exit(0)

            threading.Thread(target=_nuke, daemon=True).start()
            return {"ok": True}
        except Exception as e:
            _log_error("close_app", e)
            import os
            os._exit(1)

    def minimize_to_edge(self) -> dict:
        """Shrink window to a 56×56 icon docked on the right edge of the current monitor."""
        try:
            icon_size = 56
            import win32gui
            if not self._hwnd:
                return {"error": "No HWND"}
            rect = win32gui.GetWindowRect(self._hwnd)
            x, y, r, b = rect
            self._saved_rect = (x, y, r - x, b - y)

            monitor = self._current_monitor()
            if monitor is None and self._monitors:
                monitor = self._monitors[0]
            if monitor is None:
                monitor = {"left": 0, "top": 0, "width": 1920, "height": 1080}

            edge_x = monitor["left"] + monitor["width"] - icon_size
            edge_y = monitor["top"] + (monitor["height"] // 2) - (icon_size // 2)

            SWP_NOZORDER_LOCAL = 0x0004
            SWP_NOACTIVATE_LOCAL = 0x0010
            win32gui.SetWindowPos(
                self._hwnd, 0, edge_x, edge_y, icon_size, icon_size,
                SWP_NOZORDER_LOCAL | SWP_NOACTIVATE_LOCAL
            )
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("minimize_to_edge", e)}

    def enter_maximized(self) -> dict:
        """Fill the monitor's work area (taskbar excluded) with a small
        breathing-room margin around the window. Uses SetWindowPos with the
        physical work-area rect from GetMonitorInfo — SW_MAXIMIZE on a
        frameless (WS_POPUP) window ignores the work area and covers the
        taskbar, which is not what users expect. Physical pixels here (not
        logical) because both GetMonitorInfo and SetWindowPos agree on the
        physical coordinate system for a DPI-aware process; the DPI mismatch
        only shows up when handing sizes to pywebview's create_window."""
        try:
            import win32api
            import win32con
            import win32gui
            if not self._hwnd:
                return {"error": "No HWND"}

            # Save the pre-maximize rect so exit_maximized can restore it.
            rect = win32gui.GetWindowRect(self._hwnd)
            self._saved_rect = (rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])

            pt = ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
            hmon = win32api.MonitorFromPoint(pt, win32con.MONITOR_DEFAULTTONEAREST)
            info = win32api.GetMonitorInfo(hmon)
            work = info.get("Work") or (0, 0, 1920, 1080)
            wl, wt, wr, wb = work

            # Breathing-room margin (physical pixels). We scale the margin by
            # the monitor's DPI so it looks the same on HiDPI screens as on
            # standard ones — a fixed 16px margin is barely visible at 200%.
            import ctypes
            _hdc = ctypes.windll.user32.GetDC(None)
            _dpi = ctypes.windll.gdi32.GetDeviceCaps(_hdc, 88)
            ctypes.windll.user32.ReleaseDC(None, _hdc)
            _scale = (_dpi / 96.0) if _dpi > 0 else 1.0
            margin = int(16 * _scale)

            x = wl + margin
            y = wt + margin
            w = (wr - wl) - margin * 2
            h = (wb - wt) - margin * 2

            SWP_NOZORDER_LOCAL = 0x0004
            SWP_NOACTIVATE_LOCAL = 0x0010
            win32gui.SetWindowPos(
                self._hwnd, 0, x, y, w, h,
                SWP_NOZORDER_LOCAL | SWP_NOACTIVATE_LOCAL,
            )
            return {"ok": True, "x": x, "y": y, "w": w, "h": h}
        except Exception as e:
            return {"error": _log_error("enter_maximized", e)}

    def exit_maximized(self) -> dict:
        """Leave maximized mode and restore to the saved pre-maximize rect,
        or center a 740x1000 default on the current monitor if we have no
        saved rect (e.g., first-boot minimize click before any maximize)."""
        try:
            import win32gui
            if not self._hwnd:
                return {"error": "No HWND"}

            if self._saved_rect:
                x, y, w, h = self._saved_rect
            else:
                monitor = self._current_monitor() or (self._monitors[0] if self._monitors else None)
                if monitor:
                    w, h = 740, 1000
                    x = monitor["left"] + (monitor["width"] - w) // 2
                    y = monitor["top"] + (monitor["height"] - h) // 2
                else:
                    x, y, w, h = 100, 100, 740, 1000

            SWP_NOZORDER_LOCAL = 0x0004
            SWP_NOACTIVATE_LOCAL = 0x0010
            win32gui.SetWindowPos(
                self._hwnd, 0, x, y, w, h,
                SWP_NOZORDER_LOCAL | SWP_NOACTIVATE_LOCAL,
            )
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("exit_maximized", e)}

    def enter_compact_bar(self) -> dict:
        """Shrink window to a composer-only bar at bottom-right. Responses appear
        in a separate floating popup at the top-right."""
        try:
            bar_w = 820
            bar_h = 200

            import win32gui
            if not self._hwnd:
                return {"error": "No HWND"}
            rect = win32gui.GetWindowRect(self._hwnd)
            self._saved_rect = (rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])
            monitor = self._current_monitor()
            if monitor is None and self._monitors:
                monitor = self._monitors[0]
            if monitor is None:
                monitor = {"left": 0, "top": 0, "width": 1920, "height": 1080}

            # Use WORK AREA (excludes taskbar). Query the HMONITOR of the center
            # of the current Ghost window, no matter what.
            work_left = monitor["left"]
            work_top = monitor["top"]
            work_right = monitor["left"] + monitor["width"]
            work_bottom = monitor["top"] + monitor["height"]
            try:
                import win32api
                import win32con
                import win32gui
                rect = win32gui.GetWindowRect(self._hwnd) if self._hwnd else None
                if rect:
                    pt = ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
                else:
                    pt = (monitor["left"] + 10, monitor["top"] + 10)
                hmon = win32api.MonitorFromPoint(pt, win32con.MONITOR_DEFAULTTONEAREST)
                info = win32api.GetMonitorInfo(hmon)
                work = info.get("Work")
                if work:
                    work_left, work_top, work_right, work_bottom = work
                print(f"[compact] monitor full={monitor['left']},{monitor['top']},{monitor['width']}x{monitor['height']} "
                      f"work={work_left},{work_top} to {work_right},{work_bottom}", flush=True)
            except Exception as e:
                print(f"[compact] work area fallback: {e}", flush=True)

            # Bottom-right corner of the work area with small margin
            x = work_right - bar_w - 12
            y = work_bottom - bar_h - 12
            print(f"[compact] bar at {x},{y} size {bar_w}x{bar_h}", flush=True)

            SWP_NOZORDER_LOCAL = 0x0004
            SWP_NOACTIVATE_LOCAL = 0x0010
            win32gui.SetWindowPos(
                self._hwnd, 0, x, y, bar_w, bar_h,
                SWP_NOZORDER_LOCAL | SWP_NOACTIVATE_LOCAL
            )
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("enter_compact_bar", e)}

    def exit_compact_bar(self) -> dict:
        """Restore full-size window from compact bar mode. Also closes any response popup."""
        try:
            self.hide_response_popup()

            import win32gui
            if not self._hwnd:
                return {"error": "No HWND"}
            if self._saved_rect:
                x, y, w, h = self._saved_rect
            else:
                monitor = self._current_monitor() or (self._monitors[0] if self._monitors else None)
                if monitor:
                    w, h = 740, 1000
                    x = monitor["left"] + (monitor["width"] - w) // 2
                    y = monitor["top"] + (monitor["height"] - h) // 2
                else:
                    x, y, w, h = 100, 100, 740, 1000
            SWP_NOZORDER_LOCAL = 0x0004
            SWP_NOACTIVATE_LOCAL = 0x0010
            win32gui.SetWindowPos(
                self._hwnd, 0, x, y, w, h,
                SWP_NOZORDER_LOCAL | SWP_NOACTIVATE_LOCAL
            )
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("exit_compact_bar", e)}

    def show_response_popup(self, messages: list | None = None, text: str = "", loading: bool = False) -> dict:
        """Position the response popup at right side of the monitor (50% height), show it,
        and update with the current conversation messages."""
        try:
            if self._response_window is None:
                return {"error": "Response window not pre-created"}

            # Position at right of current monitor, taking 50% of its height.
            # Width matches the compact bar (820) para alinhar visualmente.
            if self._response_hwnd:
                import win32gui
                monitor = self._current_monitor() or (self._monitors[0] if self._monitors else None)
                if monitor is None:
                    monitor = {"left": 0, "top": 0, "width": 1920, "height": 1080}
                w = 820
                h = monitor["height"] // 2
                x = monitor["left"] + monitor["width"] - w - 12
                y = monitor["top"] + 48
                SWP_NOZORDER_LOCAL = 0x0004
                SWP_NOACTIVATE_LOCAL = 0x0010
                try:
                    win32gui.SetWindowPos(
                        self._response_hwnd, 0, x, y, w, h,
                        SWP_NOZORDER_LOCAL | SWP_NOACTIVATE_LOCAL,
                    )
                    import win32con
                    win32gui.ShowWindow(self._response_hwnd, win32con.SW_SHOWNOACTIVATE)
                except Exception as e:
                    _log_error("popup_position", e)
            else:
                try:
                    self._response_window.show()
                except Exception:
                    pass

            # Update messages
            if messages is None:
                messages = []

            def update():
                import time as _t
                _t.sleep(0.1)
                try:
                    if self._response_window is not None:
                        self._response_window.evaluate_js(
                            f"window.setMessages({json.dumps(messages)})"
                        )
                except Exception as e:
                    _log_error("popup_update", e)

            threading.Thread(target=update, daemon=True).start()
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("show_response_popup", e)}

    def update_response_popup(self, messages: list) -> dict:
        """Just update the messages in the popup without repositioning."""
        try:
            if self._response_window is None:
                return {"ok": False}
            def update():
                try:
                    self._response_window.evaluate_js(
                        f"window.setMessages({json.dumps(messages or [])})"
                    )
                except Exception as e:
                    _log_error("popup_update_inline", e)
            threading.Thread(target=update, daemon=True).start()
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("update_response_popup", e)}

    def hide_response_popup(self) -> dict:
        """Hide the response popup AND move it off-screen so its hidden rect
        doesn't render as a black rectangle under WDA_EXCLUDEFROMCAPTURE."""
        try:
            if self._response_hwnd:
                import win32con
                import win32gui
                win32gui.ShowWindow(self._response_hwnd, win32con.SW_HIDE)
                # Park off-screen to avoid DWM protecting any visible rect
                SWP_NOSIZE = 0x0001
                SWP_NOZORDER = 0x0004
                SWP_NOACTIVATE = 0x0010
                win32gui.SetWindowPos(
                    self._response_hwnd, 0, -10000, -10000, 0, 0,
                    SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE,
                )
            elif self._response_window is not None:
                try:
                    self._response_window.hide()
                except Exception:
                    pass
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("hide_response_popup", e)}

    def update_popup_title(self, title: str) -> dict:
        """Atualiza o header do popup de resposta com o título da conversa."""
        try:
            if self._response_window is None:
                return {"ok": True}
            safe = json.dumps(title or "")
            try:
                self._response_window.evaluate_js(
                    f"window.setPopupTitle && window.setPopupTitle({safe})"
                )
            except Exception:
                pass
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("update_popup_title", e)}

    def restore_from_edge(self) -> dict:
        """Restore window to its saved rect before docking."""
        try:
            import win32gui
            if not self._hwnd:
                return {"error": "No HWND"}
            if self._saved_rect:
                x, y, w, h = self._saved_rect
            else:
                monitor = self._current_monitor() or (self._monitors[0] if self._monitors else None)
                if monitor:
                    w, h = 740, 1000
                    x = monitor["left"] + (monitor["width"] - w) // 2
                    y = monitor["top"] + (monitor["height"] - h) // 2
                else:
                    x, y, w, h = 100, 100, 740, 1000

            SWP_NOZORDER_LOCAL = 0x0004
            SWP_NOACTIVATE_LOCAL = 0x0010
            win32gui.SetWindowPos(
                self._hwnd, 0, x, y, w, h,
                SWP_NOZORDER_LOCAL | SWP_NOACTIVATE_LOCAL
            )
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("restore_from_edge", e)}

    def force_focus(self) -> dict:
        """Explicitly bring Ghost to foreground (bypassing NOACTIVATE).
        Called when user clicks the text input so they can type directly.
        """
        try:
            if not self._hwnd:
                return {"error": "No HWND"}
            ok = force_foreground(self._hwnd)
            return {"ok": bool(ok)}
        except Exception as e:
            return {"error": _log_error("force_focus", e)}

    def restore_focus(self):
        """No-op. NOACTIVATE stays on permanently."""
        return {"ok": True}

    def start_window_drag(self):
        try:
            if not self._hwnd:
                # Try on-demand enumeration as a fallback
                try:
                    import win32gui
                    import win32process
                    pid = os.getpid()
                    found = [0]

                    def cb(hwnd, _):
                        try:
                            if not win32gui.IsWindowVisible(hwnd):
                                return True
                            _, wpid = win32process.GetWindowThreadProcessId(hwnd)
                            if wpid == pid:
                                title = win32gui.GetWindowText(hwnd)
                                if title and found[0] == 0:
                                    found[0] = hwnd
                        except Exception:
                            pass
                        return True

                    win32gui.EnumWindows(cb, None)
                    if found[0]:
                        self._hwnd = found[0]
                except Exception:
                    pass

            if not self._hwnd:
                return {"error": "No HWND"}
            t = threading.Thread(target=drag_window_loop, args=(self._hwnd,), daemon=True)
            t.start()
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("start_window_drag", e)}

    def enable_typing(self):
        """No-op. NOACTIVATE stays on permanently — no toggling (causes crashes)."""
        return {"ok": True}

    def start_kb_capture(self) -> dict:
        """Begin global keyboard capture. NOACTIVATE is already permanent."""
        try:
            print("[kb] start_kb_capture called", flush=True)
            if self._kb_listener is not None:
                print("[kb] already running, skipping", flush=True)
                return {"ok": True, "already_running": True}
            from pynput import keyboard as kb

            def forward(payload: dict):
                try:
                    if self._window is not None:
                        code = f"window.ghostKey({json.dumps(payload)})"
                        print(f"[kb] forward → {payload}", flush=True)
                        self._window.evaluate_js(code)
                    else:
                        print("[kb] self._window is None!", flush=True)
                except Exception as e:
                    print(f"[kb] forward error: {e}", flush=True)

            def on_press(key):
                try:
                    if isinstance(key, kb.KeyCode) and key.char:
                        forward({"type": "char", "value": key.char})
                    elif key == kb.Key.space:
                        forward({"type": "char", "value": " "})
                    elif key == kb.Key.enter:
                        forward({"type": "enter"})
                    elif key == kb.Key.backspace:
                        forward({"type": "backspace"})
                    elif key == kb.Key.esc:
                        forward({"type": "esc"})
                    elif key == kb.Key.tab:
                        forward({"type": "char", "value": "\t"})
                except Exception as e:
                    print(f"[kb] on_press error: {e}", flush=True)

            self._kb_listener = kb.Listener(on_press=on_press)
            self._kb_listener.start()
            print("[kb] listener started", flush=True)
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("start_kb_capture", e)}

    def stop_kb_capture(self) -> dict:
        try:
            if self._kb_listener is not None:
                self._kb_listener.stop()
                self._kb_listener = None
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("stop_kb_capture", e)}

    def get_main_window_rect(self) -> dict:
        """Main Ghost window's screen rect — used by JS to translate a chip's
        viewport position to absolute screen coords for popup placement."""
        try:
            import win32gui
            if not self._hwnd:
                return {"error": "no hwnd"}
            l, t, r, b = win32gui.GetWindowRect(self._hwnd)
            return {"x": l, "y": t, "width": r - l, "height": b - t}
        except Exception as e:
            return {"error": _log_error("get_main_window_rect", e)}

    def _monitor_index_for_rect(self, left: int, top: int, width: int, height: int) -> int | None:
        """Pick the monitor that contains the window's center point. Returns
        None if no monitor matches (very-off-screen window)."""
        cx = left + width // 2
        cy = top + height // 2
        for m in self._monitors:
            if (m["left"] <= cx < m["left"] + m["width"] and
                    m["top"] <= cy < m["top"] + m["height"]):
                return m["index"]
        return None

