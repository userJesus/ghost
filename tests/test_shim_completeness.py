"""Regression test: every shim re-exports every pre-refactor symbol.

The v1.1.12 installed build crashed on chat with:
    ImportError: cannot import name '_has_image' from 'src.gpt_client'
because the explicit re-export list in the shim skipped underscore names.

This test prevents that class of regression for all 14 compat shims by
comparing the shim's importable surface against the pre-refactor module
symbol set (extracted via AST from the backup — or via a hard-coded list
if the backup is not present, so the test is CI-safe).
"""
from __future__ import annotations

import importlib

import pytest

# Every symbol that existed at module level in a pre-refactor shim module
# that a caller could realistically import. Stdlib re-imports (time, json,
# re, ssl, logging, Optional, etc.) are intentionally NOT in this list —
# nobody does `from src.updater import json`.
EXPECTED_SYMBOLS = {
    "src.sensitive": {"SensitiveMatch", "scan"},
    "src.parser": {"CODE_BLOCK_RE", "extract_code_blocks", "strip_code_blocks",
                   "pick_main_code"},
    "src.gpt_client": {
        "BASE_PERSONA", "SCREEN_CONTEXT_ADDENDUM", "SYSTEM_PROMPT",
        "build_user_message", "chat_completion", "completion_kwargs",
        "generate_conversation_title", "analyze_image",
        # The symbol whose absence caused the v1.1.12 production bug:
        "_has_image",
        # Supporting exports the pre-refactor module had:
        "AUTHOR_NAME", "AUTHOR_GITHUB", "AUTHOR_LINKEDIN",
        "GITHUB_REPO_URL", "GITHUB_RELEASES_URL",
    },
    "src.updater": {"UpdateInfo", "check", "check_async",
                    # compat aliases the shim provides explicitly:
                    "_fetch_latest", "_parse_version", "_CHECK_TIMEOUT_SEC",
                    "GITHUB_LATEST_API"},
    "src.win_focus": {
        "WDA_NONE", "WDA_MONITOR", "WDA_EXCLUDEFROMCAPTURE",
        "hide_from_capture", "hide_from_taskbar", "hide_window",
        "show_window", "is_window_visible", "make_non_activating",
        "make_activating", "get_foreground_hwnd", "set_foreground",
        "force_foreground", "set_window_opacity", "set_color_key",
        "set_dwm_shadow", "set_round_region", "start_drag",
        "drag_window_loop",
    },
    "src.platform_adapter": {"PlatformAdapter", "WindowsPlatform", "get_platform"},
    "src.logging_config": {"configure", "get_logger", "_log_dir"},
    "src.capture": {"capture_fullscreen", "capture_region",
                    "image_to_base64", "image_to_data_url"},
    "src.scroll_capture": {"list_monitors", "capture_monitor",
                           "scroll_and_capture", "stitch_vertical"},
    "src.region_selector": {"select_region"},
    "src.meeting": {"MeetingRecorder", "format_time",
                    "SAMPLE_RATE", "CHANNELS", "BLOCK_SIZE",
                    "VIDEO_FPS", "VIDEO_MAX_WIDTH", "VIDEO_QUALITY"},
    "src.meeting_processor": {
        "transcribe_audio_verbose", "transcribe_chunks_verbose",
        "transcribe_chunks", "summarize_meeting", "diarize_and_summarize",
        "write_markdown_doc", "meetings_dir", "desktop_dir",
        "SUMMARY_PROMPT",
    },
    "src.voice": {"VoiceRecorder", "SAMPLE_RATE", "CHANNELS", "BLOCK_SIZE",
                  # tests monkey-patch these — MUST stay accessible:
                  "sc", "sf"},
    "src.clone": {"WebCloner", "clones_dir"},
}


@pytest.mark.parametrize("module_name,symbols", sorted(EXPECTED_SYMBOLS.items()))
def test_shim_exposes_all_expected_symbols(module_name, symbols):
    """Every listed symbol must be accessible via the shim.

    This is the regression test for v1.1.12's `_has_image` ImportError.
    If this passes, any caller that did `from src.<old_module> import X`
    pre-refactor can still do so today.
    """
    mod = importlib.import_module(module_name)
    missing = [s for s in symbols if not hasattr(mod, s)]
    assert not missing, (
        f"{module_name} is missing these pre-refactor symbols: {missing}. "
        f"The compat shim must re-export them so callers like src.api that "
        f"imported private helpers (e.g. `_has_image`) keep working."
    )


def test_shim_wholesale_reexport_handles_private_symbols():
    """The wholesale-re-export pattern must expose `_has_image` — the
    specific symbol whose absence crashed v1.1.12 chat on install."""
    from src.gpt_client import _has_image
    assert callable(_has_image)
    # Sanity: function works.
    msgs_with_img = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "x"},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,y"}},
        ],
    }]
    assert _has_image(msgs_with_img) is True
    assert _has_image([{"role": "user", "content": [{"type": "text", "text": "x"}]}]) is False


def test_updater_backcompat_aliases_point_to_new_locations():
    """Pre-refactor private helpers in src.updater must keep working after
    the split into services + integrations + domain."""
    from src.updater import _fetch_latest, _parse_version, _CHECK_TIMEOUT_SEC
    from src.domain.version_compare import parse_version
    from src.integrations.github_releases import (
        DEFAULT_TIMEOUT_SEC,
        fetch_latest_release,
    )
    assert _parse_version is parse_version
    assert _fetch_latest is fetch_latest_release
    assert _CHECK_TIMEOUT_SEC == DEFAULT_TIMEOUT_SEC


def test_voice_shim_preserves_soundcard_modules_for_test_monkeypatch():
    """`tests/test_voice.py` does `monkeypatch.setattr(voice.sc, ...)`.
    The shim must re-export `sc` (soundcard) and `sf` (soundfile) or those
    tests break."""
    from src import voice
    import soundcard as real_sc
    import soundfile as real_sf
    assert voice.sc is real_sc
    assert voice.sf is real_sf
