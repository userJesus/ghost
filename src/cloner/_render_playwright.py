"""Headless + headed Playwright renderers, `_clone_profile_dir`."""
from __future__ import annotations

import re
import sys
import traceback
from pathlib import Path
from urllib.parse import urlparse

from ._config import USER_AGENT
from ._playwright_ensure import _try_import_playwright


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
