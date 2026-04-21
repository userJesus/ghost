"""Lazy import + on-demand install of Playwright (dev only)."""
from __future__ import annotations

import sys


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
