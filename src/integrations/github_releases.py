"""Thin client for the GitHub Releases REST API.

Extracted from the pre-refactor `src/updater.py`, which mixed HTTP,
version-compare, and caching concerns. This module does ONLY the HTTP call
and JSON decoding. Version comparison lives in `src.domain.version_compare`;
app-level caching and UI-facing `UpdateInfo` live in `src.services.update_service`.
"""
from __future__ import annotations

import json
import ssl
import urllib.error
import urllib.request

from src.infra.logging_setup import get_logger
from src.version import GITHUB_LATEST_API, __version__

log = get_logger(__name__)

DEFAULT_TIMEOUT_SEC = 6.0


def fetch_latest_release(timeout: float = DEFAULT_TIMEOUT_SEC) -> dict | None:
    """GET /repos/<owner>/<repo>/releases/latest.

    Returns the decoded JSON dict on success, None on any failure (404 for
    repos with no release yet, HTTP errors, timeouts, TLS errors, offline).
    """
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(
            GITHUB_LATEST_API,
            headers={
                "User-Agent": f"Ghost/{__version__}",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=timeout, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            log.debug("update-check: no releases yet (404)")
        else:
            log.warning("update-check HTTP error: %s", e)
    except Exception as e:
        log.warning("update-check error: %s", e)
    return None


def installer_asset_url(asset_name: str) -> str:
    """Stable `/releases/latest/download/<asset>` URL for direct downloads."""
    from src.version import GITHUB_REPO_URL
    return f"{GITHUB_REPO_URL}/releases/latest/download/{asset_name}"
