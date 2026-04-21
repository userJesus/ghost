"""`_Ctx` shared state + `_is_spa_shell` + `_fetch` primitive."""
from __future__ import annotations

import threading
from pathlib import Path
from urllib.parse import urldefrag

import httpx
from bs4 import BeautifulSoup  # noqa: F401 — BeautifulSoup not used here, kept for re-export symmetry

from ._config import DEFAULT_HEADERS, FETCH_TIMEOUT, MAX_ASSET_BYTES
from ._paths import _asset_rel_path


def _is_spa_shell(html: str) -> bool:
    """Heuristic: detect an empty single-page-app shell that needs JS to render.

    Focuses on POSITIVE signals of a framework-driven render (empty root div + JS bundle),
    not just "short text", to avoid false positives on small static pages.
    """
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")
    body = soup.find("body")
    if not body:
        return True
    # Strip script/style/noscript before measuring text
    for tag in body.find_all(["script", "style", "noscript", "template"]):
        tag.decompose()
    text = body.get_text(" ", strip=True)
    text_len = len(text)

    # Count scripts on the original soup (body was mutated above)
    try:
        orig_soup = BeautifulSoup(html, "lxml")
    except Exception:
        orig_soup = BeautifulSoup(html, "html.parser")
    scripts = orig_soup.find_all("script") or []
    external_scripts = [s for s in scripts if s.get("src")]
    module_scripts = sum(1 for s in scripts if (s.get("type") or "").lower() == "module")

    # 1) Empty SPA root element — strong signal
    root_ids = ("root", "app", "__next", "__nuxt", "main-app", "svelte")
    for rid in root_ids:
        node = orig_soup.find(id=rid)
        if node is not None:
            inner = node.get_text(" ", strip=True)
            if len(inner) < 30 and external_scripts:
                return True

    # 2) Next.js / Nuxt / Remix hydration payload with empty body
    if "__NEXT_DATA__" in html and text_len < 400 and external_scripts:
        return True
    if "window.__NUXT__" in html and text_len < 400 and external_scripts:
        return True

    # 3) Many module scripts + small text body = modern SPA
    if text_len < 300 and module_scripts >= 2:
        return True

    # 4) Lots of external JS + very little visible text
    return text_len < 200 and len(external_scripts) >= 4


class _Ctx:
    """Per-clone session state: shared httpx client + asset tracking + cancel flag."""

    def __init__(self, output_dir: Path, base_url: str):
        self.output_dir = output_dir
        self.base_url = base_url
        self.client = httpx.Client(
            headers=DEFAULT_HEADERS,
            follow_redirects=True,
            timeout=FETCH_TIMEOUT,
            verify=False,  # some sites have cert oddities; we're not authenticating
            http2=False,
        )
        # url -> local rel path (e.g. "_assets/host/path/file.css")
        self.url_to_local: dict[str, str] = {}
        # rel path -> bytes (queued to write)
        self.to_write: dict[str, bytes] = {}
        # URLs we've already tried to fetch (success OR failure) — prevents
        # infinite re-attempts when CSS references unreachable/404 resources.
        self.attempted: set[str] = set()
        self.seen_css_urls: set[str] = set()
        self.lock = threading.Lock()
        self.cancel = False
        self.errors: list[str] = []

    def close(self):
        import contextlib
        with contextlib.suppress(Exception):
            self.client.close()

    def register(self, absolute_url: str) -> str | None:
        absolute_url = urldefrag(absolute_url)[0]
        if not absolute_url:
            return None
        with self.lock:
            if absolute_url in self.url_to_local:
                return self.url_to_local[absolute_url]
            rel = _asset_rel_path(absolute_url)
            if rel is None:
                return None
            # Avoid collision with index.html
            if rel == "index.html":
                rel = "_assets/index.html"
            self.url_to_local[absolute_url] = rel
            return rel


def _fetch(ctx: _Ctx, url: str) -> tuple[bytes, str] | None:
    """Fetch a URL, return (bytes, content-type). Returns None on failure."""
    try:
        with ctx.client.stream("GET", url) as resp:
            if resp.status_code >= 400:
                ctx.errors.append(f"{resp.status_code} {url[:120]}")
                return None
            ctype = resp.headers.get("content-type", "").split(";", 1)[0].strip().lower()
            chunks: list[bytes] = []
            total = 0
            for chunk in resp.iter_bytes():
                chunks.append(chunk)
                total += len(chunk)
                if total > MAX_ASSET_BYTES:
                    ctx.errors.append(f"too-large {url[:120]}")
                    return None
            return b"".join(chunks), ctype
    except Exception as e:
        ctx.errors.append(f"{type(e).__name__} {url[:120]}")
        return None


