"""Stealth init script + render via Ghost's own WebView2."""
from __future__ import annotations

import threading

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

