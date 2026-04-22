"""Tests for src/platform/windows/preflight.py.

v1.1.19 simplified the preflight from an aggressive 6-rounds × 3-sweeps
retry loop (which added up to 9 seconds to cold-boot startup) to a single
kill pass per image + a 600-800ms settle. These tests exercise the new
contract:

  * `_kill_webview2_helpers()` — never kills Ghost.exe (would self-terminate)
  * `cleanup_webview2_state()` — runs kill + sleep + orphan-cache sweep
  * `_system_uptime_seconds()` — returns a positive number on Windows
  * `_warm_webview_cache()` — noop-safe if the cache dir is missing
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


class TestKillWebView2Helpers:
    """Single-pass kill contract (v1.1.19+)."""

    def test_calls_taskkill_for_each_non_ghost_image(self):
        """Every image except Ghost.exe should get ONE taskkill attempt."""
        from src.platform.windows.preflight import _GHOST_IMAGES, _kill_webview2_helpers

        fake_run = MagicMock(return_value=MagicMock(returncode=128))
        with patch("src.platform.windows.preflight.subprocess.run", fake_run):
            _kill_webview2_helpers()

        # One call per image that isn't Ghost.exe
        expected_calls = [i for i in _GHOST_IMAGES if i != "Ghost.exe"]
        assert fake_run.call_count == len(expected_calls), \
            f"expected {len(expected_calls)} taskkill calls, got {fake_run.call_count}"

    def test_never_kills_ghost_exe(self):
        """Ghost.exe in the preflight would self-terminate — must skip it."""
        from src.platform.windows.preflight import _kill_webview2_helpers

        fake_run = MagicMock(return_value=MagicMock(returncode=128))
        with patch("src.platform.windows.preflight.subprocess.run", fake_run):
            _kill_webview2_helpers()

        for call in fake_run.call_args_list:
            args = call.args[0]  # the command list
            assert "Ghost.exe" not in args, \
                f"preflight must never kill Ghost.exe, got: {args}"

    def test_webview2_gets_tree_kill_flag(self):
        """msedgewebview2 helpers spawn grandchildren — kill the tree."""
        from src.platform.windows.preflight import _kill_webview2_helpers

        fake_run = MagicMock(return_value=MagicMock(returncode=128))
        with patch("src.platform.windows.preflight.subprocess.run", fake_run):
            _kill_webview2_helpers()

        for call in fake_run.call_args_list:
            args = call.args[0]
            # Every call should have /T (tree-kill) + /F (force)
            assert "/T" in args, f"tree-kill flag missing: {args}"
            assert "/F" in args, f"force flag missing: {args}"

    def test_survives_taskkill_timeout(self):
        """A hung taskkill (e.g. OS under load) shouldn't crash preflight."""
        from src.platform.windows.preflight import _kill_webview2_helpers
        import subprocess

        # First call times out, second returns 128 (not found)
        fake_run = MagicMock(side_effect=[
            subprocess.TimeoutExpired("taskkill", 3),
            MagicMock(returncode=128),
            MagicMock(returncode=128),
            MagicMock(returncode=128),
        ])
        with patch("src.platform.windows.preflight.subprocess.run", fake_run):
            # Should complete without raising
            r = _kill_webview2_helpers()
        # Contract: _kill_webview2_helpers always returns True (non-fatal)
        assert r is True


class TestCleanupWebView2State:
    """Orchestrator: kill + settle + sweep."""

    def test_always_returns_true_in_current_impl(self):
        """Current contract: the cleanup doesn't gate startup, always returns True."""
        from src.platform.windows.preflight import cleanup_webview2_state

        fake_run = MagicMock(return_value=MagicMock(returncode=128))
        with patch("src.platform.windows.preflight.subprocess.run", fake_run), \
             patch("src.platform.windows.preflight.time.sleep"), \
             patch("src.platform.windows.preflight._sweep_orphan_cache_dirs"):
            r = cleanup_webview2_state()
        assert r is True

    def test_sweep_runs_even_if_kills_fail(self):
        """If taskkill errors, we still want to sweep orphan temp caches."""
        from src.platform.windows.preflight import cleanup_webview2_state

        sweep_mock = MagicMock()
        fake_run = MagicMock(side_effect=OSError("kaboom"))
        with patch("src.platform.windows.preflight.subprocess.run", fake_run), \
             patch("src.platform.windows.preflight.time.sleep"), \
             patch("src.platform.windows.preflight._sweep_orphan_cache_dirs", sweep_mock):
            cleanup_webview2_state()
        sweep_mock.assert_called_once()


class TestSystemUptime:
    """`_system_uptime_seconds` drives the cold-boot detection branch."""

    def test_returns_positive_on_windows(self):
        from src.platform.windows.preflight import _system_uptime_seconds
        val = _system_uptime_seconds()
        # Must be > 0 on any running Windows box
        assert val > 0

    def test_returns_zero_on_kernel32_failure(self):
        """Defensive path: if GetTickCount64 raises, return 0.0."""
        from src.platform.windows.preflight import _system_uptime_seconds
        with patch("ctypes.windll") as mock_windll:
            mock_windll.kernel32.GetTickCount64.side_effect = OSError("no dll")
            val = _system_uptime_seconds()
        assert val == 0.0


class TestWarmWebViewCache:
    """Cache-warming reads files from ~/.ghost/webview-cache to pull them
    into the OS page cache. Must be safe on a fresh install (no dir)."""

    def test_noop_when_cache_dir_missing(self, tmp_path):
        from src.platform.windows import preflight

        fake_cache = tmp_path / "nonexistent"
        with patch("src.platform.windows.preflight.WEBVIEW_CACHE", fake_cache) \
                if hasattr(preflight, "WEBVIEW_CACHE") else patch.object(
                    preflight, "_warm_webview_cache",  # fallback: just call it
                    wraps=preflight._warm_webview_cache):
            # Should not raise
            preflight._warm_webview_cache()

    def test_reads_files_when_cache_exists(self, tmp_path):
        from src.platform.windows import preflight

        # Populate a fake cache dir with a couple of small files
        cache = tmp_path / "webview-cache"
        cache.mkdir()
        (cache / "a.bin").write_bytes(b"hello" * 100)
        (cache / "b.bin").write_bytes(b"world" * 100)

        # Patch the cache path to our temp dir
        from src.infra import paths as infra_paths
        with patch.object(infra_paths, "WEBVIEW_CACHE", cache), \
             patch("src.infra.paths.WEBVIEW_CACHE", cache):
            # Should read without raising
            preflight._warm_webview_cache()
