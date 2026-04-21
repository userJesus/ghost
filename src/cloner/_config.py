"""Static configuration: UA, headers, regex patterns, limits."""
from __future__ import annotations

import re

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/121.0.0.0 Safari/537.36"
)
DEFAULT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
}

CSS_URL_RE = re.compile(r"""url\(\s*(['"]?)([^'")]+)\1\s*\)""", re.IGNORECASE)
CSS_IMPORT_RE = re.compile(
    r"""@import\s+(?:url\()?\s*(['"])([^'"]+)\1\s*\)?""", re.IGNORECASE
)

MAX_WORKERS = 20
FETCH_TIMEOUT = 15.0
MAX_ASSET_BYTES = 50 * 1024 * 1024  # 50 MB per asset
