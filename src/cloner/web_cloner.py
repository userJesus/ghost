"""Web page cloning: download HTML + all assets (CSS/JS/images/fonts) for offline view.

Auto-detects SPA pages and escalates to headless browser rendering via Playwright
(installed on demand the first time it's needed).

Output: ~/Desktop/Ghost-Clones/<site>-<timestamp>/
    index.html          - rewritten HTML
    _assets/<host>/...  - all linked resources
"""

import hashlib
import re
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path, PurePosixPath
from urllib.parse import urldefrag, urljoin, urlparse

import httpx
from bs4 import BeautifulSoup

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


def clones_dir() -> Path:
    p = Path.home() / "Desktop" / "Ghost-Clones"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _slug(text: str, maxlen: int = 40) -> str:
    text = re.sub(r"[^A-Za-z0-9\-_.]", "-", text)
    text = re.sub(r"-+", "-", text).strip("-.")
    return text[:maxlen] or "page"


def _folder_name(url: str) -> str:
    u = urlparse(url)
    host = _slug(u.netloc or "site", 60)
    path_part = _slug((u.path or "").strip("/").replace("/", "-"), 30)
    ts = datetime.now().strftime("%Y%m%d-%H%M")
    if path_part and path_part != "index":
        return f"{host}-{path_part}-{ts}"
    return f"{host}-{ts}"


def _asset_rel_path(absolute_url: str) -> str | None:
    """Map an absolute URL to a relative filesystem path under _assets/."""
    try:
        parsed = urlparse(absolute_url)
        if parsed.scheme not in ("http", "https"):
            return None
        host = _slug(parsed.netloc.replace(":", "_"), 80)
        path = parsed.path or "/"
        if path.endswith("/") or path == "":
            path = path + "index.html"
        # Query strings differentiate cached variants
        if parsed.query:
            qhash = hashlib.md5(parsed.query.encode("utf-8", "ignore")).hexdigest()[:8]
            p = PurePosixPath(path)
            stem = p.stem or "file"
            ext = p.suffix
            parent = str(p.parent).lstrip("/").strip(".")
            new_name = f"{stem}.{qhash}{ext}" if ext else f"{stem}.{qhash}"
            path = f"/{parent}/{new_name}" if parent else f"/{new_name}"
        # Sanitize each path segment independently (preserve folder structure)
        segments = [_slug(seg, 80) for seg in path.lstrip("/").split("/") if seg]
        if not segments:
            segments = ["index.html"]
        return "_assets/" + host + "/" + "/".join(segments)
    except Exception:
        return None


def _relative_between(from_rel: str, to_rel: str) -> str:
    """Compute a relative URL path from from_rel (a file) to to_rel (a file)."""
    try:
        from_parts = PurePosixPath(from_rel).parent.parts
        to_parts = PurePosixPath(to_rel).parts
        i = 0
        while i < len(from_parts) and i < len(to_parts) - 1 and from_parts[i] == to_parts[i]:
            i += 1
        ups = [".."] * (len(from_parts) - i)
        downs = list(to_parts[i:])
        return "/".join(ups + downs) if (ups + downs) else to_parts[-1]
    except Exception:
        return to_rel


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


def _rewrite_css_text(css_text: str, css_absolute_url: str, css_local_rel: str,
                       ctx: _Ctx) -> str:
    """Rewrite url(...) and @import in a CSS document and recursively fetch referenced assets."""

    def _replace_url(match: re.Match) -> str:
        quote = match.group(1)
        raw = (match.group(2) or "").strip()
        if not raw or raw.startswith("data:") or raw.startswith("#"):
            return match.group(0)
        absolute = urljoin(css_absolute_url, raw)
        local = ctx.register(absolute)
        if not local:
            return match.group(0)
        rel = _relative_between(css_local_rel, local)
        # Queue for download (will be processed by main worker)
        with ctx.lock:
            if absolute not in ctx.seen_css_urls:
                ctx.seen_css_urls.add(absolute)
        return f"url({quote}{rel}{quote})"

    def _replace_import(match: re.Match) -> str:
        quote = match.group(1)
        raw = (match.group(2) or "").strip()
        if not raw or raw.startswith("data:"):
            return match.group(0)
        absolute = urljoin(css_absolute_url, raw)
        local = ctx.register(absolute)
        if not local:
            return match.group(0)
        rel = _relative_between(css_local_rel, local)
        with ctx.lock:
            if absolute not in ctx.seen_css_urls:
                ctx.seen_css_urls.add(absolute)
        return f"@import {quote}{rel}{quote}"

    out = CSS_IMPORT_RE.sub(_replace_import, css_text)
    out = CSS_URL_RE.sub(_replace_url, out)
    return out


def _process_asset(ctx: _Ctx, absolute_url: str, local_rel: str) -> bool:
    """Download an asset. If it's CSS, recursively collect its referenced assets."""
    if ctx.cancel:
        return False
    with ctx.lock:
        ctx.attempted.add(absolute_url)
    result = _fetch(ctx, absolute_url)
    if result is None:
        return False
    data, ctype = result

    is_css = (
        "css" in ctype
        or local_rel.endswith(".css")
        or absolute_url.split("?", 1)[0].lower().endswith(".css")
    )

    if is_css:
        try:
            text = data.decode("utf-8", errors="replace")
            rewritten = _rewrite_css_text(text, absolute_url, local_rel, ctx)
            data = rewritten.encode("utf-8", errors="replace")
        except Exception as e:
            ctx.errors.append(f"css-rewrite {type(e).__name__} {absolute_url[:100]}")

    with ctx.lock:
        ctx.to_write[local_rel] = data
    return True


# ---- HTML asset extraction / rewriting -------------------------------------

# Tag/attr pairs that contain a single URL
TAG_URL_ATTRS: list[tuple[str, str]] = [
    ("link", "href"),
    ("script", "src"),
    ("img", "src"),
    ("img", "data-src"),
    ("source", "src"),
    ("video", "src"),
    ("video", "poster"),
    ("audio", "src"),
    ("iframe", "src"),
    ("embed", "src"),
    ("object", "data"),
    ("use", "href"),
    ("use", "xlink:href"),
    ("image", "href"),
    ("image", "xlink:href"),
]


def _process_srcset(value: str, base_url: str, ctx: _Ctx, html_local: str = "index.html") -> str:
    """Rewrite an HTML `srcset` attribute."""
    parts = [p.strip() for p in value.split(",") if p.strip()]
    out_parts = []
    for part in parts:
        bits = part.split(None, 1)
        url_part = bits[0]
        descriptor = bits[1] if len(bits) > 1 else ""
        if url_part.startswith("data:"):
            out_parts.append(part)
            continue
        absolute = urljoin(base_url, url_part)
        local = ctx.register(absolute)
        if local is None:
            out_parts.append(part)
            continue
        rel = _relative_between(html_local, local)
        out_parts.append(f"{rel} {descriptor}".strip())
    return ", ".join(out_parts)


def _process_html(html: str, base_url: str, ctx: _Ctx) -> str:
    """Parse HTML, collect + register all asset URLs, return rewritten HTML."""
    try:
        soup = BeautifulSoup(html, "lxml")
    except Exception:
        soup = BeautifulSoup(html, "html.parser")

    html_local = "index.html"

    # Strip <base> (we're resolving URLs ourselves); save original href for base_url fallback
    for base_tag in soup.find_all("base"):
        href = base_tag.get("href")
        if href:
            base_url = urljoin(base_url, href)
        base_tag.decompose()

    def rewrite_single(absolute: str) -> str | None:
        local = ctx.register(absolute)
        if not local:
            return None
        return _relative_between(html_local, local)

    # Standard single-URL attributes
    for tag_name, attr in TAG_URL_ATTRS:
        for tag in soup.find_all(tag_name):
            val = tag.get(attr)
            if not val:
                continue
            val = val.strip()
            if val.startswith(("data:", "javascript:", "mailto:", "tel:", "#")):
                continue
            absolute = urljoin(base_url, val)
            if not urlparse(absolute).scheme.startswith("http"):
                continue
            rel = rewrite_single(absolute)
            if rel:
                tag[attr] = rel
                # Drop SRI hashes (the file contents may differ post-rewrite)
                if tag.has_attr("integrity"):
                    del tag["integrity"]
                # Crossorigin is irrelevant for local files
                if tag.has_attr("crossorigin"):
                    del tag["crossorigin"]

    # srcset on <img> and <source>
    for tag in soup.find_all(["img", "source"]):
        for attr in ("srcset", "data-srcset"):
            val = tag.get(attr)
            if val:
                tag[attr] = _process_srcset(val, base_url, ctx, html_local)

    # Inline style attributes
    for tag in soup.find_all(style=True):
        style_val = tag["style"]
        new_val = _rewrite_css_text(style_val, base_url, html_local, ctx)
        tag["style"] = new_val

    # Inline <style> blocks
    for style_tag in soup.find_all("style"):
        if style_tag.string:
            style_tag.string = _rewrite_css_text(style_tag.string, base_url, html_local, ctx)

    # Meta refresh redirects → neutralize (we want to stay on the cloned page)
    for meta in soup.find_all("meta", attrs={"http-equiv": True}):
        if (meta.get("http-equiv") or "").lower() == "refresh":
            meta.decompose()

    # Strip CSP meta (local file:// contexts handle it differently)
    for meta in soup.find_all("meta"):
        he = (meta.get("http-equiv") or "").lower()
        if he in ("content-security-policy", "content-security-policy-report-only"):
            meta.decompose()

    # Add a banner comment + UTF-8 charset meta (ensures offline rendering is predictable)
    if soup.head is not None:
        charset = soup.head.find("meta", charset=True)
        if not charset:
            meta = soup.new_tag("meta", charset="utf-8")
            soup.head.insert(0, meta)

    return str(soup)


# ---- Playwright (optional, on-demand) --------------------------------------

def _try_import_playwright():
    try:
        from playwright.sync_api import sync_playwright  # noqa
        return True
    except Exception:
        return False


def _ensure_playwright(status_cb) -> bool:
    """Import playwright; if missing AND we're in a dev venv, `pip install` it.
    In the frozen PyInstaller build we never try to install because
    `sys.executable` is Ghost.exe and `-m pip install` would spawn a
    recursive Ghost instance (no pip available inside the bundle), giving
    the user the "app não responde" symptom while two Ghosts fight for
    WebView2 and the singleton mutex. Returns True on success."""
    import subprocess
    if _try_import_playwright():
        return True
    # Frozen build: Playwright must have been bundled at build time. If it
    # wasn't, surface that cleanly so the worker can fall back to static
    # HTML instead of triggering a recursive self-spawn.
    if getattr(sys, "frozen", False):
        status_cb("Playwright não disponível nesta build — usando HTML estático.")
        print("[clone] frozen build without playwright; skipping install to avoid "
              "recursive Ghost.exe spawn.", flush=True)
        return False
    status_cb("Instalando Playwright (primeira vez, ~1 min)...")
    try:
        subprocess.run(
            [sys.executable, "-m", "pip", "install", "--quiet", "playwright"],
            check=True,
            timeout=180,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        print(f"[clone] pip install playwright failed: {e}", flush=True)
        return False
    status_cb("Baixando navegador headless (~170 MB)...")
    try:
        subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            check=True,
            timeout=600,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except Exception as e:
        print(f"[clone] playwright install chromium failed: {e}", flush=True)
        return False
    return _try_import_playwright()


# Stealth script injected into every document before page scripts run.
# Targets the signals Akamai Bot Manager / Cloudflare Turnstile / Datadome
# commonly fingerprint. Not bulletproof — but covers the 80% that get
# automated browsers flagged before ever reaching the challenge. Patched
# once at clone time via AddScriptToExecuteOnDocumentCreatedAsync, so it
# runs AHEAD of the page's anti-bot logic.
_STEALTH_INIT_SCRIPT = r"""
(() => {
  try {
    // navigator.webdriver: the #1 tell. Must be undefined on real browsers.
    Object.defineProperty(Navigator.prototype, 'webdriver', {
      get: () => undefined, configurable: true
    });

    // window.chrome: headless Chrome misses this, real Chrome has the runtime.
    if (!window.chrome) {
      window.chrome = { runtime: {}, loadTimes: function() {}, csi: function() {}, app: {} };
    }

    // Plugins array: headless returns empty. Fake a reasonable PDF plugin set.
    const fakePlugins = [
      { name: 'PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'Chrome PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
      { name: 'Chromium PDF Viewer', filename: 'internal-pdf-viewer', description: 'Portable Document Format' },
    ];
    Object.defineProperty(Navigator.prototype, 'plugins', {
      get: () => fakePlugins, configurable: true
    });

    // Languages: real users have multiple, headless often has just one.
    Object.defineProperty(Navigator.prototype, 'languages', {
      get: () => ['pt-BR', 'pt', 'en-US', 'en'], configurable: true
    });

    // Permissions: headless reports 'denied' for notifications while real
    // Chrome reports 'default'/'prompt'. Align with Notification.permission.
    if (navigator.permissions && navigator.permissions.query) {
      const origQuery = navigator.permissions.query.bind(navigator.permissions);
      navigator.permissions.query = function(p) {
        if (p && p.name === 'notifications') {
          return Promise.resolve({ state: Notification.permission });
        }
        return origQuery(p);
      };
    }

    // WebGL vendor/renderer: headless reports "Google Inc." + "SwiftShader"
    // which is a dead giveaway. Spoof to common Intel hardware values.
    try {
      const ctxProto = WebGLRenderingContext.prototype;
      const origGetParam = ctxProto.getParameter;
      ctxProto.getParameter = function(p) {
        if (p === 37445) return 'Intel Inc.';           // UNMASKED_VENDOR_WEBGL
        if (p === 37446) return 'Intel(R) UHD Graphics';  // UNMASKED_RENDERER_WEBGL
        return origGetParam.call(this, p);
      };
    } catch (_) {}

    // Hardware concurrency: default to 8 cores like most modern machines.
    if (!navigator.hardwareConcurrency || navigator.hardwareConcurrency < 2) {
      Object.defineProperty(Navigator.prototype, 'hardwareConcurrency', {
        get: () => 8, configurable: true
      });
    }
  } catch (e) { /* swallow — stealth is best-effort */ }
})();
"""


_STEALTH_PATCHED = False


def _patch_pywebview_stealth() -> bool:
    """Monkey-patch pywebview's EdgeChrome.on_webview_ready to inject the
    stealth script on every new document via CoreWebView2's
    AddScriptToExecuteOnDocumentCreatedAsync API. The Microsoft API runs
    the script BEFORE any page script, which is the only way to beat
    anti-bot checks that probe navigator.webdriver / plugins / etc. in
    their opening statements.

    Applied once per Python process. Idempotent — safe to call repeatedly."""
    global _STEALTH_PATCHED
    if _STEALTH_PATCHED:
        return True
    try:
        from webview.platforms.edgechromium import EdgeChrome
        _original_ready = EdgeChrome.on_webview_ready

        def _patched_ready(self, sender, args):
            _original_ready(self, sender, args)
            try:
                sender.CoreWebView2.AddScriptToExecuteOnDocumentCreatedAsync(_STEALTH_INIT_SCRIPT)
            except Exception as e:
                print(f"[clone] stealth inject failed: {e}", flush=True)

        EdgeChrome.on_webview_ready = _patched_ready
        _STEALTH_PATCHED = True
        return True
    except Exception as e:
        print(f"[clone] stealth patch failed: {e}", flush=True)
        return False


def _render_with_ghost_webview(url: str, status_cb, cancel_flag) -> tuple[str, str] | None:
    """Render a page through the ALREADY-RUNNING pywebview engine (WebView2
    on Windows) and read back `document.documentElement.outerHTML` after JS
    executes. No Playwright, no Chromium download, no 200MB bundle — reuses
    the same engine that's rendering Ghost's own UI.

    Called from the clone worker thread. pywebview allows `create_window`
    from non-main threads as long as `webview.start()` is already running
    (see pywebview/__init__.py line 418), which it always is inside Ghost.

    Returns (html, final_url) on success, None if the window hasn't rendered
    by the timeout or if the worker was cancelled."""
    import time as _t
    try:
        import webview
    except Exception as e:
        print(f"[clone] pywebview import failed: {e}", flush=True)
        return None

    # Skip if we're not inside a running pywebview app (e.g. when the cloner
    # is invoked from a standalone CLI test). Only way to tell is to check
    # if there's at least one live window in the registry.
    if not getattr(webview, "windows", None):
        return None

    # Enable stealth before creating the render window. Best-effort — if
    # the patch fails, we still proceed without it (non-bot-protected sites
    # clone fine either way).
    _patch_pywebview_stealth()

    status_cb("Renderizando com WebView2...")
    rendered = {"html": None, "url": None, "error": None}
    done = threading.Event()
    render_win = {"w": None}

    def _on_loaded():
        # Page-ready strategy:
        #   1. Wait 5s for the initial splash/loading animation to pass.
        #      This covers most React/Vue/Vite SPAs (w2g, landing pages,
        #      portfolios) where content hydrates within a few seconds.
        #   2. Probe the current HTML + URL. If tiny (<2 KB) or we're on an
        #      anti-bot challenge URL (Akamai `bm-verify`, Cloudflare `cf_chl`,
        #      etc.), keep waiting — the page is mid-redirect after solving
        #      a silent JS challenge. Poll every 2s up to 20s total.
        #   3. When the HTML stabilizes (same URL + reasonable size + length
        #      not changing), do a scroll pass to trigger lazy loaders, wait
        #      2 more seconds, and capture.
        # This replaces a single fixed sleep that was failing on sites like
        # iFood where the real page arrives only after a Bot Manager dance.
        w = render_win["w"]
        if w is None:
            done.set(); return
        import contextlib as _ctx

        last_html = ""
        last_url = ""
        stable_for = 0
        MAX_POLL_SECONDS = 20
        poll_deadline = _t.monotonic() + MAX_POLL_SECONDS

        # Initial grace period — don't probe during the first 5s, let the
        # page do its normal spinup without us fighting for the bridge.
        _t.sleep(5.0)

        def _looks_like_challenge(html: str, cur_url: str) -> bool:
            if not html or len(html) < 2000:
                return True
            h = html.lower()
            u = cur_url.lower()
            if "bm-verify" in u or "cf_chl" in u or "/_sec/" in u:
                return True
            # Common challenge page markers
            if ("akamai" in h[:4000] and "powered and protected" in h[:4000]) or \
               "checking your browser" in h[:4000] or \
               "just a moment" in h[:4000]:
                return True
            return False

        while _t.monotonic() < poll_deadline:
            try:
                cur_html = w.evaluate_js("document.documentElement.outerHTML") or ""
                cur_url = w.evaluate_js("window.location.href") or url
            except Exception:
                break

            if _looks_like_challenge(cur_html, cur_url):
                last_html, last_url = cur_html, cur_url
                _t.sleep(2.0)
                continue

            # Stability check — same URL, HTML size change < 5% between
            # polls means content stopped arriving. Bail fast when stable.
            if cur_url == last_url and last_html and \
               abs(len(cur_html) - len(last_html)) < max(500, len(last_html) // 20):
                stable_for += 1
                if stable_for >= 2:
                    last_html, last_url = cur_html, cur_url
                    break
            else:
                stable_for = 0

            last_html, last_url = cur_html, cur_url
            _t.sleep(2.0)

        try:
            # Scroll pass to trigger IntersectionObserver-based loaders, then
            # one more settle before the final capture.
            with _ctx.suppress(Exception):
                w.evaluate_js(
                    "window.scrollTo(0, document.body.scrollHeight); "
                    "setTimeout(() => window.scrollTo(0, 0), 800);"
                )
                _t.sleep(2.0)
            rendered["html"] = w.evaluate_js("document.documentElement.outerHTML") or last_html
            rendered["url"] = w.evaluate_js("window.location.href") or last_url or url
        except Exception as e:
            rendered["error"] = f"evaluate_js failed: {e}"
            rendered["html"] = last_html
            rendered["url"] = last_url or url
        finally:
            try: w.destroy()
            except Exception: pass
            done.set()

    try:
        # Off-screen + hidden so the user never sees the render window. We
        # use create_window's x/y to park it far off-screen too, as a
        # defensive belt against `hidden=True` not taking effect on every
        # Windows build.
        w = webview.create_window(
            f"ghost-clone-render-{int(_t.time())}",
            url,
            hidden=True,
            width=1366, height=900,
            x=-32000, y=-32000,
            frameless=True,
            on_top=False,
            resizable=False,
        )
        render_win["w"] = w
        w.events.loaded += _on_loaded
    except Exception as e:
        print(f"[clone] ghost webview create_window failed: {e}", flush=True)
        return None

    # 60-second hard cap on render. Poll the cancel flag in the meantime so
    # the modal's Cancel button can interrupt a slow page.
    deadline = _t.monotonic() + 60.0
    while not done.is_set():
        if _t.monotonic() > deadline:
            try: w.destroy()
            except Exception: pass
            print("[clone] ghost webview render timed out after 60s", flush=True)
            return None
        if cancel_flag():
            try: w.destroy()
            except Exception: pass
            return None
        _t.sleep(0.2)

    if rendered["error"]:
        print(f"[clone] ghost webview render error: {rendered['error']}", flush=True)
        return None
    html = rendered["html"] or ""
    if not html or len(html) < 500:
        # Too small — likely the page errored or redirected to something
        # blank. Treat as failure so the caller falls through to Playwright
        # or static HTML.
        print(f"[clone] ghost webview returned only {len(html)} bytes; treating as failure", flush=True)
        return None
    return html, rendered["url"] or url


def _clone_profile_dir(url: str) -> Path:
    """Per-domain persistent Chromium profile so re-clones of the same site
    don't force the user to log in again. One folder per netloc under
    ~/.ghost/clone-profile/ — isolates cookies/storage between, say, GitHub
    and Linkedin so we don't cross-contaminate sessions."""
    netloc = urlparse(url).netloc or "default"
    slug = re.sub(r"[^a-zA-Z0-9._-]", "_", netloc)
    p = Path.home() / ".ghost" / "clone-profile" / slug
    p.mkdir(parents=True, exist_ok=True)
    return p


def _render_with_playwright_headed(url: str, status_cb, cancel_flag) -> tuple[str, str] | None:
    """Open a VISIBLE Chromium window with a persistent profile so the user
    can log in manually before we capture the page. An injected "clonar
    agora" button at the top of the page signals completion via an exposed
    Python function.

    cancel_flag: a callable returning True if the worker was cancelled. We
    poll it so the modal's Cancel button can interrupt an in-progress login.
    Returns (html, final_url) on success, None on cancel/error.
    """
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    profile_dir = _clone_profile_dir(url)
    status_cb("Abrindo navegador — faça login se necessário...")
    try:
        with sync_playwright() as p:
            # launch_persistent_context mixes launch + new_context in one
            # call, pinning the user_data_dir so cookies/storage survive
            # restarts. headless=False so the user can actually see and
            # interact with the page.
            try:
                ctx = p.chromium.launch_persistent_context(
                    user_data_dir=str(profile_dir),
                    headless=False,
                    user_agent=USER_AGENT,
                    viewport={"width": 1366, "height": 900},
                    locale="pt-BR",
                    args=["--disable-blink-features=AutomationControlled"],
                )
            except Exception as e:
                msg = str(e)
                if "Executable doesn't exist" in msg:
                    if getattr(sys, "frozen", False):
                        status_cb("Chromium não instalado — instale manualmente via venv de dev.")
                        print("[clone] frozen build missing chromium; cannot auto-install.", flush=True)
                        return None
                    import subprocess
                    status_cb("Instalando navegador Chromium (~170 MB, só na primeira vez)...")
                    subprocess.run(
                        [sys.executable, "-m", "playwright", "install", "chromium"],
                        check=True, timeout=600,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    ctx = p.chromium.launch_persistent_context(
                        user_data_dir=str(profile_dir),
                        headless=False,
                        user_agent=USER_AGENT,
                        viewport={"width": 1366, "height": 900},
                        locale="pt-BR",
                        args=["--disable-blink-features=AutomationControlled"],
                    )
                else:
                    raise

            # persistent_context starts with one default page; reuse it.
            page = ctx.pages[0] if ctx.pages else ctx.new_page()

            # Shared flag the injected button toggles via the exposed fn.
            ready = {"done": False}
            def _ghost_clone_ready():
                ready["done"] = True
                return True
            import contextlib
            with contextlib.suppress(Exception):
                ctx.expose_function("ghostCloneReady", _ghost_clone_ready)

            # Inject a floating "Pronto — clonar esta página" button on EVERY
            # page navigation (init script runs before any page scripts). The
            # button calls back into Python via window.ghostCloneReady().
            ctx.add_init_script("""
                (function() {
                    function mount() {
                        if (document.getElementById('__ghost_clone_btn')) return;
                        if (!document.body) { setTimeout(mount, 100); return; }
                        var b = document.createElement('button');
                        b.id = '__ghost_clone_btn';
                        b.textContent = '👻 Clonar esta página agora';
                        b.style.cssText = 'position:fixed;top:16px;right:16px;z-index:2147483647;' +
                            'padding:10px 16px;font:600 13px/1 system-ui,sans-serif;color:#00281e;' +
                            'background:#61dbb4;border:0;border-radius:8px;cursor:pointer;' +
                            'box-shadow:0 6px 24px rgba(0,0,0,.4);transition:filter .15s';
                        b.onmouseover = function(){ b.style.filter='brightness(1.08)' };
                        b.onmouseout = function(){ b.style.filter='' };
                        b.onclick = async function() {
                            b.disabled = true;
                            b.textContent = '⏳ Capturando...';
                            try { if (window.ghostCloneReady) await window.ghostCloneReady(); } catch(_) {}
                        };
                        document.body.appendChild(b);
                    }
                    if (document.readyState === 'loading') {
                        document.addEventListener('DOMContentLoaded', mount);
                    } else {
                        mount();
                    }
                })();
            """)

            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            status_cb("Navegador aberto. Faça login e clique no botão verde 'Clonar esta página agora'.")

            # Poll for either ready flag or cancel. 10-minute cap so we don't
            # leak a Chromium process if the user forgets about it.
            import time as _t
            deadline = _t.monotonic() + 600
            while _t.monotonic() < deadline:
                if cancel_flag():
                    ctx.close()
                    return None
                if ready["done"]:
                    break
                _t.sleep(0.3)
            else:
                status_cb("Timeout (10 min) — cancelando.")
                ctx.close()
                return None

            # Scroll to warm up lazy-loaded content, same as headless flow.
            with contextlib.suppress(Exception):
                page.evaluate(
                    "async () => { "
                    "  const btn = document.getElementById('__ghost_clone_btn'); "
                    "  if (btn) btn.remove(); "
                    "  window.scrollTo(0, document.body.scrollHeight); "
                    "  await new Promise(r => setTimeout(r, 800)); "
                    "  window.scrollTo(0, 0); "
                    "  await new Promise(r => setTimeout(r, 300)); "
                    "}"
                )
            with contextlib.suppress(Exception):
                page.wait_for_load_state("networkidle", timeout=5000)
            final_url = page.url
            html = page.content()
            ctx.close()
            return html, final_url
    except Exception as e:
        print(f"[clone] playwright headed render failed: {e}", flush=True)
        traceback.print_exc()
        return None


def _render_with_playwright(url: str, status_cb) -> tuple[str, str] | None:
    """Return (final_rendered_html, final_url) after JS execution."""
    try:
        from playwright.sync_api import sync_playwright
    except Exception:
        return None
    try:
        with sync_playwright() as p:
            try:
                browser = p.chromium.launch(headless=True)
            except Exception as e:
                msg = str(e)
                # Browser binary missing. In a dev venv we auto-install;
                # in the frozen build we bail out to avoid recursive
                # Ghost.exe spawn via `sys.executable -m playwright ...`.
                if ("Executable doesn't exist" in msg or "browserType.launch" in msg):
                    if getattr(sys, "frozen", False):
                        status_cb("Chromium headless não disponível nesta build — usando HTML estático.")
                        print("[clone] frozen build missing chromium; falling back to static HTML.", flush=True)
                        return None
                    import subprocess
                    status_cb("Instalando navegador headless (~170 MB)...")
                    subprocess.run(
                        [sys.executable, "-m", "playwright", "install", "chromium"],
                        check=True,
                        timeout=600,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                    )
                    browser = p.chromium.launch(headless=True)
                else:
                    raise
            ctx = browser.new_context(
                user_agent=USER_AGENT,
                viewport={"width": 1366, "height": 900},
                locale="pt-BR",
            )
            import contextlib
            page = ctx.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=30000)
            # networkidle may never be reached on sites with persistent polling — best-effort
            with contextlib.suppress(Exception):
                page.wait_for_load_state("networkidle", timeout=15000)
            # Scroll to trigger lazy-loaded content (IntersectionObserver-based loaders)
            with contextlib.suppress(Exception):
                page.evaluate(
                    "async () => { "
                    "  await new Promise(r => setTimeout(r, 500)); "
                    "  window.scrollTo(0, document.body.scrollHeight); "
                    "  await new Promise(r => setTimeout(r, 800)); "
                    "  window.scrollTo(0, 0); "
                    "  await new Promise(r => setTimeout(r, 300)); "
                    "}"
                )
            with contextlib.suppress(Exception):
                page.wait_for_load_state("networkidle", timeout=5000)
            final_url = page.url
            html = page.content()
            browser.close()
            return html, final_url
    except Exception as e:
        print(f"[clone] playwright render failed: {e}", flush=True)
        traceback.print_exc()
        return None


# ---- Main cloner ------------------------------------------------------------

class WebCloner:
    def __init__(self):
        self._running = False
        self._thread: threading.Thread | None = None
        self._status = ""
        self._progress = {"done": 0, "total": 0}
        self._result: dict | None = None
        self._cancel = False
        self._ctx: _Ctx | None = None

    def is_running(self) -> bool:
        return self._running

    def start(self, url: str) -> dict:
        if self._running:
            return {"error": "Uma clonagem já está em andamento"}
        url = (url or "").strip()
        if not url:
            return {"error": "URL vazia"}
        if not url.startswith(("http://", "https://")):
            url = "https://" + url
        try:
            parsed = urlparse(url)
            if not parsed.netloc:
                return {"error": "URL inválida"}
        except Exception:
            return {"error": "URL inválida"}

        self._running = True
        self._status = "Iniciando..."
        self._progress = {"done": 0, "total": 0}
        self._result = None
        self._cancel = False
        self._thread = threading.Thread(target=self._worker, args=(url,), daemon=True)
        self._thread.start()
        return {"ok": True}

    def cancel(self) -> dict:
        self._cancel = True
        if self._ctx is not None:
            self._ctx.cancel = True
        return {"ok": True}

    def get_status(self) -> dict:
        return {
            "running": self._running,
            "status": self._status,
            "progress": dict(self._progress),
            "has_result": self._result is not None,
        }

    def consume_result(self) -> dict | None:
        r = self._result
        self._result = None
        return r

    def _set_status(self, text: str):
        self._status = text
        print(f"[clone] {text}", flush=True)

    def _worker(self, url: str):
        try:
            folder_name = _folder_name(url)
            out_dir = clones_dir() / folder_name
            out_dir.mkdir(parents=True, exist_ok=True)

            ctx = _Ctx(out_dir, url)
            self._ctx = ctx

            # Phase 1: static fetch
            self._set_status("Baixando HTML...")
            static_result = _fetch(ctx, url)
            if static_result is None:
                raise RuntimeError(f"Não foi possível baixar {url}")
            static_bytes, _ctype = static_result
            html = static_bytes.decode("utf-8", errors="replace")
            final_url = url

            # Phase 2: SPA detection → re-render with JS engine if needed.
            # Preferred path is Ghost's own WebView2 (free, bundled, no extra
            # download). Only fall back to Playwright headless if the
            # WebView2 path fails AND we're in dev (playwright isn't in the
            # frozen build).
            used_js = False
            if _is_spa_shell(html):
                self._set_status("Página SPA detectada — renderizando com WebView2...")
                cancel_fn = lambda: self._cancel
                rendered = _render_with_ghost_webview(url, self._set_status, cancel_fn)
                if rendered is not None:
                    html, final_url = rendered
                    used_js = True
                    self._set_status("Renderização via WebView2 concluída.")
                elif _ensure_playwright(self._set_status):
                    self._set_status("Tentando Playwright como fallback...")
                    rendered = _render_with_playwright(url, self._set_status)
                    if rendered is not None:
                        html, final_url = rendered
                        used_js = True
                    else:
                        self._set_status("Renderização JS falhou — usando HTML estático")
                else:
                    self._set_status("Sem renderizador JS disponível — usando HTML estático")

            if self._cancel:
                raise RuntimeError("Cancelado pelo usuário")

            # Phase 3: parse HTML, register + rewrite asset URLs
            self._set_status("Extraindo recursos (CSS/JS/imagens)...")
            rewritten_html = _process_html(html, final_url, ctx)

            # Phase 4: download assets in parallel. CSS files, once fetched, can register
            # ADDITIONAL nested URLs (@import, url(), fonts) on the fly. We keep draining
            # the `pending` set (URLs in url_to_local that we haven't attempted yet) until
            # it's empty, so the count-total naturally grows as discoveries happen.
            self._set_status("Baixando recursos...")
            done = 0
            max_waves = 6  # safety cap: CSS chains beyond 6 levels are implausible

            for _wave in range(max_waves):
                if self._cancel:
                    break
                with ctx.lock:
                    pending = [
                        (u, rel) for u, rel in ctx.url_to_local.items()
                        if u not in ctx.attempted
                    ]
                if not pending:
                    break
                with ctx.lock:
                    total = len(ctx.url_to_local)
                self._progress = {"done": done, "total": total}
                self._set_status(f"Baixando recursos ({len(pending)} nesta rodada)...")

                with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
                    futures = {
                        pool.submit(_process_asset, ctx, u, rel): u
                        for u, rel in pending
                    }
                    for _fut in as_completed(futures):
                        if self._cancel:
                            break
                        done += 1
                        with ctx.lock:
                            total = len(ctx.url_to_local)
                        self._progress = {"done": done, "total": total}

            # Phase 5: write everything to disk
            self._set_status("Salvando em disco...")
            (out_dir / "index.html").write_bytes(rewritten_html.encode("utf-8", errors="replace"))
            for rel, data in ctx.to_write.items():
                dest = out_dir / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                try:
                    dest.write_bytes(data)
                except Exception as e:
                    ctx.errors.append(f"write-fail {rel}: {e}")

            # Write a small log file with errors (if any)
            if ctx.errors:
                (out_dir / "_clone-errors.log").write_text(
                    "\n".join(ctx.errors[:500]), encoding="utf-8"
                )

            self._set_status(f"Concluído: {folder_name}")
            self._result = {
                "ok": True,
                "folder": str(out_dir),
                "folder_name": folder_name,
                "index_path": str(out_dir / "index.html"),
                "assets_count": len(ctx.to_write),
                "errors_count": len(ctx.errors),
                "used_js": used_js,
            }
        except Exception as e:
            tb = traceback.format_exc()
            print(f"[clone] error: {e}\n{tb}", file=sys.stderr, flush=True)
            self._set_status(f"Erro: {e}")
            self._result = {"error": f"{type(e).__name__}: {e}"}
        finally:
            try:
                if self._ctx is not None:
                    self._ctx.close()
            except Exception:
                pass
            self._ctx = None
            self._running = False
