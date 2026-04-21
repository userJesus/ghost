"""End-to-end tests that simulate the real user flow via the GhostAPI facade.

These tests mimic exactly what `web/app.js` does — call `pywebview.api.<method>`
methods (here: call them directly on a GhostAPI instance) and assert on the
returned dict. Every bridge method in the UI's call path has at least one
assertion here, so renames / signature changes fail fast.

Pywebview windows are NOT created (this is import-level testing of the
facade), but every service is exercised. No network traffic — we mock
`fetch_latest_release` so these tests are offline-safe.
"""
from __future__ import annotations

from unittest.mock import patch

from src.api import GhostAPI


class TestGhostAPIConstruction:
    def test_services_are_wired_in(self):
        api = GhostAPI()
        # The 3 services extracted during the 2026-04 refactor.
        assert api._update_svc is not None
        assert api._settings_svc is not None
        assert api._history_svc is not None
        # The UpdateService must get a lazy window reference, not a value.
        assert callable(api._update_svc._window_getter)


class TestBridgeMethodSurface:
    """Every method the webview calls MUST exist. Renaming any of these
    breaks `pywebview.api.<name>(...)` calls in app.js (string-dispatched)."""

    REQUIRED_METHODS = [
        # Core chat
        "send_text", "send_text_streaming",
        "analyze_last_capture", "scan_sensitive", "read_clipboard",
        # Voice
        "voice_start", "voice_status", "voice_stop_and_transcribe", "voice_cancel",
        "openai_tts",
        # Capture
        "capture_fullscreen", "capture_area", "capture_with_scroll",
        "list_windows",
        # Watch
        "toggle_watch", "get_watch_status", "get_watch_thumbnail",
        # Meeting
        "start_meeting", "stop_meeting", "get_meeting_status",
        "get_live_transcript", "meeting_live_question",
        "consume_meeting_result", "open_meetings_folder",
        # Clone
        "start_clone", "get_clone_status", "cancel_clone",
        "consume_clone_result", "open_clones_folder", "open_cloned_page",
        # Window management
        "enter_maximized", "exit_maximized", "restore_from_edge",
        "enter_compact_bar", "exit_compact_bar",
        "show_response_popup", "update_response_popup", "hide_response_popup",
        "minimize", "hide_app", "close_app", "minimize_to_edge",
        "force_focus", "restore_focus", "start_window_drag",
        # Dropdown
        "show_dropdown_popup", "hide_dropdown_popup", "dropdown_pick",
        # Updates (DO NOT BREAK)
        "check_for_updates", "download_and_install_update",
        # Keyboard
        "start_kb_capture", "stop_kb_capture",
        # History
        "history_list", "history_get", "history_save", "history_delete",
        "history_new_id", "history_suggest_title", "update_popup_title",
        # Settings
        "get_settings", "set_openai_model", "save_openai_key", "clear_openai_key",
        # Meta
        "get_presets", "get_monitors", "get_app_info",
        "set_capture_visibility", "parse_dropped_file", "open_url",
        # Branch
        "branch_summarize", "branch_main_conversation",
    ]

    def test_all_bridge_methods_present(self):
        api = GhostAPI()
        missing = [m for m in self.REQUIRED_METHODS if not hasattr(api, m)]
        assert not missing, f"GhostAPI is missing bridge methods: {missing}"

    def test_all_bridge_methods_callable(self):
        api = GhostAPI()
        for m in self.REQUIRED_METHODS:
            assert callable(getattr(api, m)), f"{m} exists but is not callable"


class TestRealUserFlowPureFunctions:
    """Flows that don't require a window / subprocess / network."""

    def test_settings_round_trip(self, tmp_ghost_home):
        """Open settings → change model → save → re-open shows new model.
        Mirrors the user flow: open Config modal, select GPT-5, save."""
        api = GhostAPI()
        before = api.get_settings()
        assert "openai_model" in before

        r = api.set_openai_model("gpt-5-mini")
        assert r.get("ok") is True

        after = api.get_settings()
        assert after["openai_model"] == "gpt-5-mini"

    def test_settings_rejects_unknown_model(self, tmp_ghost_home):
        api = GhostAPI()
        r = api.set_openai_model("gpt-imaginary-999")
        assert "error" in r

    def test_history_crud_flow(self, tmp_ghost_home):
        """New chat → save → list → open → delete."""
        api = GhostAPI()
        # List starts empty
        assert api.history_list() == {"ok": True, "conversations": []}

        # User starts a new chat
        conv_id = api.history_new_id()["id"]
        assert conv_id.startswith("conv-")

        # Types messages, saves
        msgs = [
            {"role": "user", "text": "pergunta"},
            {"role": "assistant", "text": "resposta"},
        ]
        saved = api.history_save(conv_id, msgs)
        assert saved["ok"] is True

        # Reopens from the history modal
        loaded = api.history_get(conv_id)
        assert loaded["ok"] is True
        assert loaded["conversation"]["messages"] == msgs

        # Delete flow
        r = api.history_delete(conv_id)
        assert r == {"ok": True}

        # Confirm gone
        assert "error" in api.history_get(conv_id)

    def test_sensitive_scan_round_trip(self):
        """Before sending a prompt, app.js calls scan_sensitive to warn the user.
        Verify detection on a synthetic email."""
        api = GhostAPI()
        r = api.scan_sensitive("Contact me at test@example.com")
        assert r.get("ok") is True
        # Note: bridge returns `sensitive`, not `matches` — keep this contract
        # stable, app.js accesses r.sensitive.
        types = {m["type"] for m in r["sensitive"]}
        assert "Email" in types

    def test_sensitive_scan_empty_text(self):
        api = GhostAPI()
        r = api.scan_sensitive("")
        assert r.get("ok") is True
        assert r["sensitive"] == []

    def test_app_info_shape(self):
        """app.js reads version + author from this to show the settings sidebar."""
        api = GhostAPI()
        r = api.get_app_info()
        # Keys the frontend depends on (post-refactor shape unchanged).
        for key in ("version", "author", "authorEmail", "repoUrl", "releasesUrl"):
            assert key in r, f"get_app_info missing key: {key}"
        assert r["version"]  # non-empty
        assert r["repoUrl"].startswith("https://github.com/")

    def test_check_for_updates_offline_returns_graceful_error(self):
        """When GitHub is unreachable, update banner must stay hidden —
        NOT crash the UI."""
        api = GhostAPI()
        with patch("src.services.update_service.fetch_latest_release", return_value=None):
            # Force=True bypasses the module-level cache.
            r = api.check_for_updates(force=True)
        assert r["hasUpdate"] is False
        assert r["error"] == "offline"

    def test_check_for_updates_newer_version_advertised(self):
        """Simulates a brand-new release being published."""
        api = GhostAPI()
        fake = {
            "tag_name": "v99.0.0",
            "html_url": "https://github.com/userJesus/ghost/releases/tag/v99.0.0",
            "body": "huge release",
        }
        # Reset cache to force re-check
        from src.services import update_service as us
        us._CACHED = None
        with patch("src.services.update_service.fetch_latest_release", return_value=fake):
            r = api.check_for_updates(force=True)
        assert r["hasUpdate"] is True
        assert r["latest"] == "99.0.0"
        assert r["releaseUrl"].endswith("v99.0.0")

    def test_get_presets_returns_preset_list(self):
        """app.js expects a bare list of preset names, not a wrapped dict."""
        api = GhostAPI()
        r = api.get_presets()
        assert isinstance(r, list)
        assert len(r) >= 5
        # Each entry is a preset name (string)
        assert all(isinstance(name, str) for name in r)


class TestUpdateFlowMechanicsSmoke:
    """Smoke tests for the install-launch path WITHOUT actually exiting."""

    def test_download_and_install_unsupported_platform(self):
        """Guard: non-win/mac hosts get a graceful error, not a crash."""
        api = GhostAPI()
        import sys
        with patch.object(sys, "platform", "freebsd"):
            r = api.download_and_install_update()
        assert "error" in r
        assert "freebsd" in r["error"]


class TestOpenUrlValidation:
    """open_url is called from the update banner → must not accept random input."""

    def test_open_url_exists_and_returns_dict(self):
        api = GhostAPI()
        # Do NOT actually open a browser — the method should just call webbrowser.open
        # which we patch.
        with patch("webbrowser.open", return_value=True):
            r = api.open_url("https://github.com/userJesus/ghost/releases")
        assert r.get("ok") is True
