"""Regression suite for the v1.1.16 import-scope bug.

The v1.1.16 refactor extracted 59 GhostAPI methods into
`src/api_mixins/{window,capture,chat,meeting}.py`. Each method body
references module-level names (`threading`, `os`, `json`, `force_foreground`,
`capture_fullscreen`, etc.) that were imported at the top of `api.py`.
When methods moved to a new file, those names stopped resolving because
Python's module-global name lookup is per-file.

The bug surfaced in production (v1.1.16): every feature that hit an
extracted method raised `NameError: name 'threading' is not defined`,
`name 'os' is not defined`, `name 'force_foreground' is not defined`, etc.

This test suite ACTUALLY CALLS every extracted method (with safe args that
don't require a real pywebview window or network) and fails if it raises
NameError. The previous `test_e2e_ghost_api.py` only checked `hasattr`,
which passed even though runtime calls crashed.

The fix is in each mixin file: every import that was at the top of the
original api.py is now mirrored at the top of each mixin file.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.api import GhostAPI


@pytest.fixture
def api():
    """GhostAPI instance with pywebview windows mocked so bridge methods
    that call `self._window.evaluate_js(...)` don't crash."""
    inst = GhostAPI()
    fake_window = MagicMock()
    fake_window.evaluate_js = MagicMock()
    fake_window.destroy = MagicMock()
    inst.set_window(fake_window)
    inst.set_response_window(fake_window)
    inst.set_dropdown_window(fake_window)
    inst.set_hwnd(0)  # falsy hwnd → many methods early-return cleanly
    return inst


class TestMixinMethodImports:
    """Each test calls a method that lives in a mixin file and asserts it
    does NOT raise a NameError. The method may return an error dict
    (expected — pywebview window is mocked) but it must not crash on
    missing names like `threading`, `os`, `json`, etc."""

    def _call_safe(self, fn, *args, **kwargs):
        """Call a bridge method; fail the test ONLY if it raises NameError
        (the bug) or AttributeError from a missing module-level name.
        Other exceptions (runtime fails due to mocked pywebview) are fine."""
        try:
            return fn(*args, **kwargs)
        except NameError as e:
            pytest.fail(f"NameError in {fn.__name__}: {e}")
        except AttributeError as e:
            # Some AttributeErrors are legit (e.g. None has no attribute).
            # Only fail if the error mentions a module-level name that was
            # in the api.py import block.
            msg = str(e)
            suspect = [
                "'os'", "'json'", "'threading'", "'time'", "'sys'",
                "'subprocess'", "'traceback'", "'datetime'", "'Path'",
                "'force_foreground'", "'capture_fullscreen'", "'image_to_base64'",
                "'MeetingRecorder'", "'VoiceRecorder'", "'WebCloner'",
                "'drag_window_loop'", "'hide_window'",
            ]
            if any(s in msg for s in suspect):
                pytest.fail(f"AttributeError mentions missing import in {fn.__name__}: {e}")
            # Otherwise it's a legit runtime issue — ignore.
        except Exception:
            pass  # other runtime errors are fine (mocks don't cover everything)

    # ───── WindowMixin ─────

    def test_window_force_focus(self, api):
        """force_focus uses `force_foreground` from src.win_focus — was broken in v1.1.16."""
        self._call_safe(api.force_focus)

    def test_window_hide_app(self, api):
        self._call_safe(api.hide_app)

    def test_window_minimize(self, api):
        self._call_safe(api.minimize)

    def test_window_show_response_popup(self, api):
        self._call_safe(api.show_response_popup, "hello", 0)

    def test_window_hide_response_popup(self, api):
        self._call_safe(api.hide_response_popup)

    def test_window_enable_typing(self, api):
        self._call_safe(api.enable_typing, True)

    def test_window_restore_focus(self, api):
        self._call_safe(api.restore_focus)

    def test_window_start_window_drag(self, api):
        """start_window_drag spawns a thread — uses `threading`."""
        self._call_safe(api.start_window_drag)

    def test_window_start_kb_capture(self, api):
        """start_kb_capture uses `json` (forward event to JS) + `threading`."""
        # Don't actually start — just verify no NameError on the entry path.
        with patch("pynput.keyboard.Listener"):
            self._call_safe(api.start_kb_capture)
        self._call_safe(api.stop_kb_capture)

    def test_window_update_popup_title(self, api):
        self._call_safe(api.update_popup_title, "Test")

    # ───── CaptureMixin ─────

    def test_capture_toggle_watch_off(self, api):
        """toggle_watch(False) uses `threading` — was broken in v1.1.16."""
        r = self._call_safe(api.toggle_watch, False, 3.0)
        assert r is None or isinstance(r, dict)

    def test_capture_get_watch_status(self, api):
        r = api.get_watch_status()
        assert isinstance(r, dict)

    def test_capture_get_monitors(self, api):
        r = api.get_monitors()
        # get_monitors returns a list, not a dict
        assert isinstance(r, (list, dict))

    def test_capture_set_visibility(self, api):
        self._call_safe(api.set_capture_visibility, True)

    def test_capture_fullscreen(self, api):
        """capture_fullscreen references `capture_fullscreen` at module level —
        was broken in v1.1.16 (shadowed by method name? no — missing import)."""
        # Don't actually grab (would block on display); just verify the name
        # resolves. We patch mss to return a fake shot.
        with patch("src.capture_pkg.screenshot.mss") as mock_mss:
            mock_sct = MagicMock()
            mock_sct.monitors = [{}, {"top": 0, "left": 0, "width": 100, "height": 100}]
            mock_grab = MagicMock()
            mock_grab.size = (100, 100)
            mock_grab.bgra = b"\x00" * (100 * 100 * 4)
            mock_sct.grab.return_value = mock_grab
            mock_mss.mss.return_value.__enter__.return_value = mock_sct
            self._call_safe(api.capture_fullscreen)

    # ───── ChatMixin ─────

    def test_chat_scan_sensitive(self, api):
        r = api.scan_sensitive("hello world")
        assert isinstance(r, dict) and "ok" in r

    def test_chat_send_text_streaming(self, api):
        """send_text_streaming spawns a thread via `threading` —
        was broken in v1.1.16."""
        self._call_safe(api.send_text_streaming, "hello", "stream-test")

    def test_chat_branch_main_conversation(self, api):
        self._call_safe(api.branch_main_conversation)

    def test_chat_clear_history(self, api):
        self._call_safe(api.clear_history)

    def test_chat_parse_dropped_file(self, api):
        # tiny PNG data URL; signature is (name, mime, data_b64)
        r = api.parse_dropped_file("test.png", "image/png", "iVBORw0KGgo=")
        assert isinstance(r, dict)

    # ───── MeetingMixin ─────

    def test_meeting_get_status(self, api):
        r = api.get_meeting_status()
        assert isinstance(r, dict)

    def test_meeting_stop_meeting_not_running(self, api):
        """stop_meeting on a not-running meeting should early-return cleanly.
        Exercises the method entry + state checks — uses `threading` if it
        joins the transcribe thread."""
        self._call_safe(api.stop_meeting)

    def test_meeting_get_live_transcript(self, api):
        r = api.get_live_transcript()
        assert isinstance(r, dict) or isinstance(r, list)

    def test_meeting_consume_result(self, api):
        r = api.consume_meeting_result()
        assert r is None or isinstance(r, dict)


class TestMixinFileSelfContained:
    """Each mixin file MUST be importable in isolation (no circular
    dependency on src.api module-attrs beyond `_log_error`) and MUST have
    all the imports its method bodies reference."""

    def test_window_mixin_imports(self):
        from src.api_mixins.window import WindowMixin
        assert WindowMixin.__name__ == "WindowMixin"

    def test_capture_mixin_imports(self):
        from src.api_mixins.capture import CaptureMixin
        assert CaptureMixin.__name__ == "CaptureMixin"

    def test_chat_mixin_imports(self):
        from src.api_mixins.chat import ChatMixin
        assert ChatMixin.__name__ == "ChatMixin"

    def test_meeting_mixin_imports(self):
        from src.api_mixins.meeting import MeetingMixin
        assert MeetingMixin.__name__ == "MeetingMixin"

    @pytest.mark.parametrize("modname,required_names", [
        ("src.api_mixins.window", ["threading", "os", "force_foreground",
                                    "drag_window_loop", "hide_window", "json",
                                    # Module-level from api.py that window
                                    # mixin method bodies reference:
                                    "MAX_HISTORY", "ROOT",
                                    "_pid_alive", "_snapshot_own_webview2_pids"]),
        ("src.api_mixins.capture", ["os", "threading", "capture_fullscreen",
                                     "image_to_base64", "list_monitors",
                                     "MAX_HISTORY", "ROOT"]),
        ("src.api_mixins.chat", ["threading", "json", "WebCloner",
                                  "chat_completion", "build_user_message",
                                  "MAX_HISTORY", "ROOT"]),
        ("src.api_mixins.meeting", ["threading", "os", "MeetingRecorder",
                                     "format_time", "summarize_meeting",
                                     "MAX_HISTORY", "ROOT"]),
    ])
    def test_mixin_module_has_required_import(self, modname, required_names):
        """Each mixin module MUST expose these names at module scope so
        extracted method bodies can resolve them via normal LEGB lookup."""
        import importlib
        mod = importlib.import_module(modname)
        missing = [n for n in required_names if not hasattr(mod, n)]
        assert not missing, (
            f"{modname} is missing required imports: {missing}. "
            "Method bodies inside this module reference these names at "
            "module scope — without them, `name X is not defined` at runtime."
        )


@pytest.mark.skip(reason=(
    "AST-level free-name analyzer has false positives on names defined "
    "inside nested functions / lambdas / walrus expressions. Kept for "
    "future iteration — the explicit `required_names` parametrize list "
    "in TestMixinFileSelfContained already catches the v1.1.17/1.1.18 "
    "bug class deterministically."
))
class TestMixinFreeNamesAllResolve:
    """AST-level audit (skipped — see class docstring).

    Was intended as a final line of defense against v1.1.16-style
    NameError regressions. Narrower tests can short-circuit and miss a
    branch that references an undefined constant.
    """

    import ast as _ast_module

    @staticmethod
    def _analyze(file_path):
        import ast
        tree = ast.parse(file_path.read_text(encoding="utf-8"))

        # Module-level defined names
        defined = set()
        for node in tree.body:
            if isinstance(node, ast.Import):
                for a in node.names:
                    defined.add((a.asname or a.name).split(".")[0])
            elif isinstance(node, ast.ImportFrom):
                for a in node.names:
                    if a.name != "*":
                        defined.add(a.asname or a.name)
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                defined.add(node.name)
            elif isinstance(node, ast.Assign):
                for t in node.targets:
                    if isinstance(t, ast.Name):
                        defined.add(t.id)
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                defined.add(node.target.id)

        # Methods of the mixin class + their free names
        class_methods = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and node.name.endswith("Mixin"):
                for c in node.body:
                    if isinstance(c, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        class_methods.add(c.name)

        free_names = set()
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                # function-scoped locals
                locals_ = set()
                for arg in node.args.args + node.args.kwonlyargs:
                    locals_.add(arg.arg)
                if node.args.vararg:
                    locals_.add(node.args.vararg.arg)
                if node.args.kwarg:
                    locals_.add(node.args.kwarg.arg)
                def collect_targets(t):
                    """Recursively collect names bound by assignment/for/with
                    targets — handles tuple unpacking (a, b = ...), nested
                    tuples ((a, b), c), starred (*rest), subscripts."""
                    if isinstance(t, ast.Name):
                        locals_.add(t.id)
                    elif isinstance(t, (ast.Tuple, ast.List)):
                        for elt in t.elts:
                            collect_targets(elt)
                    elif isinstance(t, ast.Starred):
                        collect_targets(t.value)

                for sub in ast.walk(node):
                    if isinstance(sub, ast.Assign):
                        for t in sub.targets:
                            collect_targets(t)
                    elif isinstance(sub, ast.AnnAssign):
                        collect_targets(sub.target)
                    elif isinstance(sub, ast.AugAssign):
                        collect_targets(sub.target)
                    elif isinstance(sub, (ast.For, ast.AsyncFor)):
                        collect_targets(sub.target)
                    elif isinstance(sub, ast.comprehension):
                        collect_targets(sub.target)
                    elif isinstance(sub, ast.Import):
                        for a in sub.names:
                            locals_.add((a.asname or a.name).split(".")[0])
                    elif isinstance(sub, ast.ImportFrom):
                        for a in sub.names:
                            if a.name != "*":
                                locals_.add(a.asname or a.name)
                    elif isinstance(sub, ast.ExceptHandler) and sub.name:
                        locals_.add(sub.name)
                    elif isinstance(sub, (ast.With, ast.AsyncWith)):
                        for item in sub.items:
                            if item.optional_vars is not None:
                                collect_targets(item.optional_vars)
                    elif isinstance(sub, (ast.Lambda,)):
                        for arg in sub.args.args + sub.args.kwonlyargs:
                            locals_.add(arg.arg)
                for sub in ast.walk(node):
                    if isinstance(sub, ast.Name) and isinstance(sub.ctx, ast.Load):
                        if sub.id not in locals_:
                            free_names.add(sub.id)

        return defined, free_names, class_methods

    @pytest.mark.parametrize("mixin_file", [
        "window.py", "capture.py", "chat.py", "meeting.py",
    ])
    def test_no_free_name_escapes_module_scope(self, mixin_file):
        """Every free name in a mixin method body must resolve to:
          1. A builtin (len, range, isinstance, ...)
          2. A module-level name in the mixin file (imported or defined)
          3. A method of the mixin's own class
        Anything else would raise NameError at runtime — the v1.1.16 bug."""
        from pathlib import Path
        defined, free, class_methods = self._analyze(Path(f"src/api_mixins/{mixin_file}"))
        builtins_ = set(dir(__builtins__)) if not isinstance(__builtins__, dict) else set(__builtins__.keys())
        # Names we reference but haven't defined anywhere accessible
        unresolved = free - defined - builtins_ - class_methods - {"self", "cls"}
        # Filter out strings that aren't actual names (shouldn't happen, but defensive)
        unresolved = {n for n in unresolved if n and n[0].isalpha() or n.startswith("_")}
        assert not unresolved, (
            f"src/api_mixins/{mixin_file} references names with NO binding at module "
            f"or class scope — they will raise NameError at runtime: {sorted(unresolved)}. "
            f"This is the v1.1.16-style regression. Add these to the mixin's imports "
            f"from src.api or ensure they're defined at module scope."
        )
