"""Update-checker: queries the GitHub Releases API and compares tags.

Runs in a background thread so it never blocks startup. The result is surfaced
to the webview via `GhostAPI.check_for_updates` (called from Alpine.js on init).
"""
from __future__ import annotations

import json
import logging
import re
import ssl
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional

from .version import GITHUB_LATEST_API, GITHUB_RELEASES_URL, __version__

log = logging.getLogger(__name__)

_CHECK_TIMEOUT_SEC = 6.0
_CACHE_LOCK = threading.Lock()
_CACHED: "Optional[UpdateInfo]" = None


@dataclass(frozen=True)
class UpdateInfo:
    current: str
    latest: str
    has_update: bool
    release_url: str
    release_notes: str

    def to_dict(self) -> dict:
        return {
            "current": self.current,
            "latest": self.latest,
            "hasUpdate": self.has_update,
            "releaseUrl": self.release_url,
            "releaseNotes": self.release_notes,
        }


def _parse_version(tag: str) -> tuple[int, ...]:
    """Turn 'v1.2.3' / '1.2.3-beta' into a sortable tuple. Non-numeric chunks count as 0."""
    nums = re.findall(r"\d+", tag or "")
    if not nums:
        return (0,)
    return tuple(int(n) for n in nums[:4])


def _fetch_latest() -> Optional[dict]:
    """Query GitHub Releases API. Returns dict on success or None on any error."""
    try:
        ctx = ssl.create_default_context()
        req = urllib.request.Request(
            GITHUB_LATEST_API,
            headers={
                "User-Agent": f"Ghost/{__version__}",
                "Accept": "application/vnd.github+json",
            },
        )
        with urllib.request.urlopen(req, timeout=_CHECK_TIMEOUT_SEC, context=ctx) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        if e.code == 404:
            log.debug("update-check: no releases yet (404)")
        else:
            log.warning("update-check HTTP error: %s", e)
    except Exception as e:
        log.warning("update-check error: %s", e)
    return None


def check(force: bool = False) -> Optional[UpdateInfo]:
    """Synchronous check — cache the result for the lifetime of the process.
    Pass `force=True` to bypass the in-memory cache (e.g., manual "check now" button).
    """
    global _CACHED
    with _CACHE_LOCK:
        if _CACHED is not None and not force:
            return _CACHED
    data = _fetch_latest()
    if not data:
        return None
    latest_tag = (data.get("tag_name") or "").lstrip("v")
    release_url = data.get("html_url") or GITHUB_RELEASES_URL
    release_notes = (data.get("body") or "").strip()
    current = __version__.lstrip("v")
    has_update = _parse_version(latest_tag) > _parse_version(current)
    info = UpdateInfo(
        current=current,
        latest=latest_tag or "?",
        has_update=has_update,
        release_url=release_url,
        release_notes=release_notes,
    )
    with _CACHE_LOCK:
        _CACHED = info
    return info


def check_async(callback) -> threading.Thread:
    """Fire a background check and invoke `callback(info_or_None)` on the worker thread."""
    def _run() -> None:
        info = check()
        try:
            callback(info)
        except Exception as e:
            log.warning("update-check callback error: %s", e)
    t = threading.Thread(target=_run, name="ghost-update-check", daemon=True)
    t.start()
    return t
