"""Tests for src/integrations/github_releases.py — thin HTTP client."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.integrations.github_releases import fetch_latest_release, installer_asset_url


class TestInstallerAssetUrl:
    """The DOWNLOAD URL is a load-bearing contract with the README + auto-updater."""

    def test_unversioned_alias_url(self):
        url = installer_asset_url("GhostSetup.exe")
        assert url == "https://github.com/userJesus/ghost/releases/latest/download/GhostSetup.exe"

    def test_versioned_url(self):
        url = installer_asset_url("GhostSetup-1.1.11.exe")
        assert url == "https://github.com/userJesus/ghost/releases/latest/download/GhostSetup-1.1.11.exe"

    def test_macos_asset(self):
        url = installer_asset_url("GhostInstaller.pkg")
        assert url == "https://github.com/userJesus/ghost/releases/latest/download/GhostInstaller.pkg"


class TestFetchLatestRelease:
    """The function MUST return None on any error (no raising to callers)."""

    def test_happy_path_returns_dict(self):
        fake_response = MagicMock()
        fake_response.read.return_value = b'{"tag_name":"v1.1.11","html_url":"x","body":"notes"}'
        ctx = MagicMock()
        ctx.__enter__.return_value = fake_response
        ctx.__exit__.return_value = False
        with patch("src.integrations.github_releases.urllib.request.urlopen", return_value=ctx):
            r = fetch_latest_release()
        assert r == {"tag_name": "v1.1.11", "html_url": "x", "body": "notes"}

    def test_network_error_returns_none(self):
        with patch("src.integrations.github_releases.urllib.request.urlopen",
                   side_effect=OSError("network down")):
            r = fetch_latest_release()
        assert r is None

    def test_404_returns_none(self):
        import urllib.error
        exc = urllib.error.HTTPError(
            url="...", code=404, msg="Not Found", hdrs=None, fp=None
        )
        with patch("src.integrations.github_releases.urllib.request.urlopen", side_effect=exc):
            r = fetch_latest_release()
        assert r is None
