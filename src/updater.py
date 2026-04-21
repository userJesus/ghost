"""Backwards-compatible re-export of the moved update checker.

Real implementation: `src.services.update_service` (check flow + UpdateInfo)
and `src.integrations.github_releases` (HTTP client). `src.domain.version_compare`
holds the version-tuple parser.

Wholesale re-export copies every top-level name of `update_service` here.
A few pre-refactor helpers whose names/locations changed are re-mapped
below so the OLD names still resolve:

    _fetch_latest      -> github_releases.fetch_latest_release
    _parse_version     -> domain.version_compare.parse_version
    _CHECK_TIMEOUT_SEC -> github_releases.DEFAULT_TIMEOUT_SEC
    GITHUB_LATEST_API  -> version.GITHUB_LATEST_API (pre-refactor re-export)
"""
from __future__ import annotations

from .services import update_service as _src

for _name in dir(_src):
    if not _name.startswith("__"):
        globals()[_name] = getattr(_src, _name)

# ---- Pre-refactor compatibility aliases ----
# These names existed at module level in the pre-2026-04 src/updater.py.
# Tests, ad-hoc scripts, and in-process callers may import them by those
# exact names; mapping them to their new homes keeps every such caller working.
from .domain.version_compare import parse_version as _parse_version  # noqa: E402
from .integrations.github_releases import (  # noqa: E402
    DEFAULT_TIMEOUT_SEC as _CHECK_TIMEOUT_SEC,
    fetch_latest_release as _fetch_latest,
)
from .version import GITHUB_LATEST_API  # noqa: E402

del _name, _src
