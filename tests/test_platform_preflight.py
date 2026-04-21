"""Tests for src/platform/windows/preflight.py.

The robust retry-until-verified kill pattern introduced in 1.1.11 must:
  * Return True when taskkill reports "not found" (128) — clean state.
  * Retry up to MAX rounds when processes are still matching.
  * Never tree-kill Ghost.exe (would self-terminate the preflight).
  * Handle the /T flag correctly for webview2/WebView2Host.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

import pytest


pytestmark = pytest.mark.skipif(
    sys.platform != "win32",
    reason="preflight is Windows-only; tests patch subprocess.run so safe elsewhere, "
           "but the module-level imports (winreg, ctypes on Windows) gate on platform",
)


class TestKillImageUntilGone:
    def test_returns_true_immediately_when_nothing_to_kill(self):
        """Exit code 128 = 'no matching process' — the clean state we want."""
        from src.platform.windows.preflight import _kill_image_until_gone

        fake_run = MagicMock(return_value=MagicMock(returncode=128))
        with patch("src.platform.windows.preflight.subprocess.run", fake_run):
            ok = _kill_image_until_gone("msedgewebview2.exe")
        assert ok is True
        # First call short-circuits the loop
        assert fake_run.call_count == 1

    def test_retries_then_succeeds_when_killed(self):
        """First round kills (rc=0), second round verifies gone (rc=128)."""
        from src.platform.windows.preflight import _kill_image_until_gone

        results = [MagicMock(returncode=0), MagicMock(returncode=128)]
        fake_run = MagicMock(side_effect=results)
        with patch("src.platform.windows.preflight.subprocess.run", fake_run), \
             patch("src.platform.windows.preflight.time.sleep"):
            ok = _kill_image_until_gone("msedgewebview2.exe")
        assert ok is True
        assert fake_run.call_count == 2

    def test_returns_false_when_budget_exhausted(self):
        """If every round returns rc=0 (keeps killing) we give up after MAX."""
        from src.platform.windows.preflight import (
            _KILL_ROUNDS_PER_IMAGE,
            _kill_image_until_gone,
        )

        fake_run = MagicMock(return_value=MagicMock(returncode=0))
        with patch("src.platform.windows.preflight.subprocess.run", fake_run), \
             patch("src.platform.windows.preflight.time.sleep"):
            ok = _kill_image_until_gone("msedgewebview2.exe")
        assert ok is False
        assert fake_run.call_count == _KILL_ROUNDS_PER_IMAGE

    def test_ghost_exe_omits_tree_kill_flag(self):
        """Preflight must NOT /T-kill Ghost.exe — that would kill our own
        webview2 children if we're the parent. The installer uses /T for
        Ghost.exe because it's a separate process from Ghost."""
        from src.platform.windows.preflight import _kill_image_until_gone

        fake_run = MagicMock(return_value=MagicMock(returncode=128))
        with patch("src.platform.windows.preflight.subprocess.run", fake_run):
            _kill_image_until_gone("Ghost.exe", include_self=False)

        args_passed = fake_run.call_args[0][0]
        assert "/T" not in args_passed, \
            f"Ghost.exe was tree-killed — would self-terminate preflight. args={args_passed}"

    def test_webview2_gets_tree_kill_flag(self):
        """Everything NOT-Ghost gets /T so its descendants die too."""
        from src.platform.windows.preflight import _kill_image_until_gone

        fake_run = MagicMock(return_value=MagicMock(returncode=128))
        with patch("src.platform.windows.preflight.subprocess.run", fake_run):
            _kill_image_until_gone("msedgewebview2.exe")
        args_passed = fake_run.call_args[0][0]
        assert "/T" in args_passed


class TestKillWebView2Helpers:
    def test_all_gone_returns_true(self):
        """Happy path — every image reports rc=128."""
        from src.platform.windows.preflight import _kill_webview2_helpers

        fake_run = MagicMock(return_value=MagicMock(returncode=128))
        with patch("src.platform.windows.preflight.subprocess.run", fake_run), \
             patch("src.platform.windows.preflight.time.sleep"):
            ok = _kill_webview2_helpers()
        assert ok is True

    def test_skips_ghost_exe_in_preflight(self):
        """Preflight runs INSIDE Ghost.exe — killing Ghost.exe would kill
        the preflight itself. The installer kills Ghost.exe; we don't."""
        from src.platform.windows.preflight import _kill_webview2_helpers

        killed_images: list[str] = []

        def fake_run(args, **kw):
            # args is like ['taskkill', '/F', '/T', '/IM', 'name.exe']
            image = args[-1]
            killed_images.append(image)
            return MagicMock(returncode=128)

        with patch("src.platform.windows.preflight.subprocess.run", side_effect=fake_run), \
             patch("src.platform.windows.preflight.time.sleep"):
            _kill_webview2_helpers()

        assert "Ghost.exe" not in killed_images, \
            "preflight must NEVER taskkill Ghost.exe (would kill itself)"
        assert "msedgewebview2.exe" in killed_images


class TestCleanupReturnsStatus:
    def test_returns_true_when_all_gone(self):
        """Public entry point reports success of the kill phase."""
        from src.platform.windows.preflight import cleanup_webview2_state

        fake_run = MagicMock(return_value=MagicMock(returncode=128))
        # Also patch the cache sweep — we don't want to touch %TEMP%.
        with patch("src.platform.windows.preflight.subprocess.run", fake_run), \
             patch("src.platform.windows.preflight.time.sleep"), \
             patch("src.platform.windows.preflight._sweep_orphan_cache_dirs"):
            ok = cleanup_webview2_state()
        assert ok is True

    def test_returns_false_when_budget_exhausted_but_still_sweeps(self):
        """If helpers won't die, we still sweep the cache so orphan dirs
        don't block the next cold start."""
        from src.platform.windows.preflight import cleanup_webview2_state

        fake_run = MagicMock(return_value=MagicMock(returncode=0))
        sweep_called: list[bool] = []

        def fake_sweep():
            sweep_called.append(True)

        with patch("src.platform.windows.preflight.subprocess.run", fake_run), \
             patch("src.platform.windows.preflight.time.sleep"), \
             patch("src.platform.windows.preflight._sweep_orphan_cache_dirs",
                   side_effect=fake_sweep):
            ok = cleanup_webview2_state()
        assert ok is False
        assert sweep_called  # sweep ran despite kill failure
