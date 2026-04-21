"""Tests for src/services/update_service.py.

Exercises the update flow without hitting real GitHub:
  * UpdateInfo.to_dict shape
  * UpdateService.check() under normal + offline conditions
  * UpdateService.download_and_install() error path on unsupported platform
  * Module-level `check` / `check_async` back-compat surface (used by src.updater)
"""
from __future__ import annotations

import sys
from unittest.mock import patch

import pytest

from src.services import update_service
from src.services.update_service import UpdateInfo, UpdateService


class TestUpdateInfoShape:
    def test_to_dict_uses_camelCase_keys(self):
        """Frontend reads `hasUpdate` / `releaseUrl` / `releaseNotes` — not snake_case."""
        info = UpdateInfo(
            current="1.1.10", latest="1.1.11", has_update=True,
            release_url="https://example/rel", release_notes="notes",
        )
        d = info.to_dict()
        assert set(d.keys()) == {"current", "latest", "hasUpdate", "releaseUrl", "releaseNotes"}
        assert d["hasUpdate"] is True
        assert d["current"] == "1.1.10"


class TestUpdateServiceCheck:
    """GhostAPI.check_for_updates must return the exact shape the UI consumes."""

    def setup_method(self):
        # Reset module-global cache between tests to keep isolation.
        update_service._CACHED = None

    def test_offline_returns_error_shape(self):
        svc = UpdateService(lambda: None)
        with patch("src.services.update_service.fetch_latest_release", return_value=None):
            r = svc.check(force=True)
        assert r["hasUpdate"] is False
        assert r["error"] == "offline"
        assert "current" in r
        assert "latest" in r

    def test_normal_response_has_expected_shape(self):
        svc = UpdateService(lambda: None)
        fake = {
            "tag_name": "v1.1.11",
            "html_url": "https://github.com/userJesus/ghost/releases/tag/v1.1.11",
            "body": "bug fixes",
        }
        with patch("src.services.update_service.fetch_latest_release", return_value=fake), \
             patch("src.services.update_service.__version__", "1.1.10"):
            r = svc.check(force=True)
        assert r["hasUpdate"] is True
        assert r["latest"] == "1.1.11"
        assert r["releaseUrl"].endswith("v1.1.11")
        assert r["releaseNotes"] == "bug fixes"
        assert "error" not in r

    def test_cache_reused_when_not_forced(self):
        svc = UpdateService(lambda: None)
        fake = {"tag_name": "v2.0.0", "html_url": "x", "body": "y"}
        with patch("src.services.update_service.fetch_latest_release", return_value=fake) as m:
            svc.check(force=True)
            svc.check(force=False)
            svc.check(force=False)
        # Only ONE HTTP call despite three check() invocations.
        assert m.call_count == 1


class TestUpdateServiceInstaller:
    """Guards on the download_and_install flow that don't require running an installer."""

    def test_unsupported_platform_returns_error(self):
        svc = UpdateService(lambda: None)
        with patch.object(sys, "platform", "linux"):
            r = svc.download_and_install()
        assert "error" in r
        assert "linux" in r["error"]

    def test_window_getter_is_lazy(self):
        """UpdateService must NOT capture a window ref at construction time."""
        captured = []
        svc = UpdateService(lambda: captured[-1] if captured else None)
        assert svc._window_getter() is None
        captured.append("fake-window-1")
        assert svc._window_getter() == "fake-window-1"
        captured.append("fake-window-2")
        assert svc._window_getter() == "fake-window-2"


class TestBackCompatSurface:
    """The pre-refactor `src.updater` module re-exports these; tests touch them."""

    def test_module_level_check_is_exported(self):
        from src.updater import UpdateInfo as UI, check, check_async
        # Identity: the shim must expose the SAME class object, not a new one.
        assert UI is UpdateInfo
        assert check is update_service._check
        assert check_async is update_service._check_async
