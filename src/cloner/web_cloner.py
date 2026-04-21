"""Web page cloning — orchestrator.

The public class `WebCloner` lives here; implementation details are
split across sibling modules (`_config`, `_paths`, `_context`,
`_processors`, `_playwright_ensure`, `_render_webview`, `_render_playwright`).

All those helpers are re-exported at module scope so that the compat
shim `src/clone.py` (which does wholesale re-export) continues to expose
every symbol it did before the split.
"""
from __future__ import annotations

# Stdlib + third-party imports needed inside WebCloner body.
import sys
import threading
import traceback
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import urlparse

# Re-export everything from extracted sub-modules. `import *` gives us
# public names; underscore names are imported explicitly below so the
# shim's `dir()` surface matches what existed in the pre-split module.
from ._config import *  # noqa: F403

# Explicit re-exports of every private (_prefixed) helper that originally
# lived in this file. `import *` doesn't pull underscore names, so we list
# them here. Purpose: `src/clone.py` shim does wholesale re-export via
# `for n in dir(web_cloner)`, and the regression test in
# `tests/test_shim_completeness.py` requires every pre-split symbol to be
# accessible through the shim.
# noqa: F401 — many are "unused" inside this file but re-exported on purpose.
from ._config import (  # noqa: F401
    MAX_WORKERS,
)
from ._context import _Ctx, _fetch, _is_spa_shell  # noqa: F401
from ._paths import (  # noqa: F401
    _asset_rel_path,
    _folder_name,
    _relative_between,
    _slug,
    clones_dir,
)
from ._playwright_ensure import _ensure_playwright, _try_import_playwright  # noqa: F401
from ._processors import (  # noqa: F401
    TAG_URL_ATTRS,
    _process_asset,
    _process_html,
    _process_srcset,
    _rewrite_css_text,
)
from ._render_playwright import (  # noqa: F401
    _clone_profile_dir,
    _render_with_playwright,
    _render_with_playwright_headed,
)
from ._render_webview import (  # noqa: F401
    _STEALTH_INIT_SCRIPT,
    _STEALTH_PATCHED,
    _patch_pywebview_stealth,
    _render_with_ghost_webview,
)


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
