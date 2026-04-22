"""WebView2 preflight cleanup extracted from main.py.

Kills zombie `msedgewebview2.exe` / `WebView2Host.exe` / `CefSharp` helpers
and sweeps orphan pywebview `%TEMP%\\tmp*\\EBWebView` cache dirs before a
fresh Ghost session starts.

Historical context kept verbatim from the original implementation: previous
close→reopen cycles left zombies holding locks on the WebView2 UserData
folder, causing "Ghost não está respondendo" hangs on the new instance's UI
thread. An earlier polling-based version was slower AND more fragile than
this fixed-wait approach, so the timing here is load-bearing — do not
"optimize" it without reproducing the zombie scenario.
"""
from __future__ import annotations

import glob
import os
import shutil
import subprocess
import time
from pathlib import Path

from src.infra.logging_setup import get_logger

log = get_logger(__name__)

_CREATE_NO_WINDOW = 0x08000000

# taskkill exit codes we act on:
#   0   = at least one process was terminated (success)
#   128 = no matching processes (also success — the end state we want)
# Anything else = transient failure; retry.
_TASKKILL_OK = 0
_TASKKILL_NOT_FOUND = 128

# Image names Ghost can be associated with. Ghost.exe is the app itself;
# msedgewebview2.exe + WebView2Host.exe are pywebview/WebView2 helpers;
# CefSharp.BrowserSubprocess.exe is a legacy pywebview helper name.
_GHOST_IMAGES: tuple[str, ...] = (
    "Ghost.exe",
    "msedgewebview2.exe",
    "WebView2Host.exe",
    "CefSharp.BrowserSubprocess.exe",
)

def _kill_webview2_helpers() -> bool:
    """One-shot kill of stale WebView2/CefSharp helpers from prior sessions.

    Single-pass by design: previous aggressive-retry versions (6 rounds ×
    3 sweeps) added up to 9 seconds to cold-boot startup, because on a
    freshly booted system Teams/Outlook/etc. keep spawning NEW webview2
    helpers faster than we can kill them. We'd loop, kill a batch,
    another batch spawns, kill again — wasting time while the user
    waited.

    Reality check: `Ghost.exe` doesn't spawn children until its OWN
    `webview.start()` is called, and that hasn't happened yet at
    preflight time. So any msedgewebview2 alive now is:
      (a) our own orphan from a previous crash/close → killing it is right
      (b) another app's webview → killing it is collateral damage
    Either way, ONE kill pass + a 600ms settle is enough. The installer
    runs its own kill sequence during updates, and `close_app` explicitly
    kills our own children on clean exit.
    """
    killed_any = False
    for image in _GHOST_IMAGES:
        # Never kill Ghost.exe here — the single-instance mutex guard in
        # main handles "another Ghost already running". This function is
        # about webview2 helpers orphaned from PRIOR sessions.
        if image == "Ghost.exe":
            continue
        try:
            r = subprocess.run(
                ["taskkill", "/F", "/T", "/IM", image],
                capture_output=True, timeout=3,
                creationflags=_CREATE_NO_WINDOW,
            )
            if r.returncode == _TASKKILL_OK:
                log.info("preflight: killed stale %s", image)
                killed_any = True
        except subprocess.TimeoutExpired:
            log.warning("preflight: taskkill %s timed out (skipping)", image)
        except Exception as e:
            log.warning("preflight: taskkill %s error: %s", image, e)
    # Return True unconditionally — the downstream caller only logs the
    # result, never gates startup on it. A failure here is never fatal.
    return True


def _sweep_orphan_cache_dirs() -> None:
    """Remove orphan `%TEMP%\\tmp<random>\\EBWebView` folders.

    pywebview creates a fresh UserDataFolder per session via tempfile.mkdtemp
    and never removes it. Over crashes / force-kills / dev restarts these
    accumulate; we've seen 200+ folders @ 50-80 MB each. Besides wasting
    disk, the WebView2 runtime apparently scans/walks %TEMP% during init,
    which slows down startup as the folder count grows.

    Conservative deletion: only remove dirs that (a) match the mkdtemp `tmp*`
    pattern AND (b) contain an `EBWebView` subdir (pywebview's signature).
    Anything else in %TEMP% is untouched.
    """
    temp_root = os.environ.get("TEMP") or os.environ.get("TMP")
    if not temp_root or not os.path.isdir(temp_root):
        return

    remaining: list[str] = []
    swept_total = 0
    for pass_idx in range(2):
        swept_pass = 0
        leftovers: list[str] = []
        for candidate in glob.glob(os.path.join(temp_root, "tmp*")):
            if not os.path.isdir(os.path.join(candidate, "EBWebView")):
                continue
            shutil.rmtree(candidate, ignore_errors=True)
            if os.path.isdir(candidate):
                leftovers.append(candidate)
            else:
                swept_pass += 1
        swept_total += swept_pass
        remaining = leftovers
        if not leftovers:
            break
        # Second chance: 400ms for OS to release stragglers.
        time.sleep(0.4)

    if swept_total:
        log.info("preflight: swept %d orphan WebView2 cache dir(s)", swept_total)
    if remaining:
        log.warning(
            "preflight: %d cache dir(s) could not be deleted (locked?); first: %s",
            len(remaining), remaining[0],
        )


def _system_uptime_seconds() -> float:
    """Return how many seconds since this Windows session booted.

    Used to detect "cold boot" state — when the OS has been up only a few
    seconds, disk + CPU are saturated by Windows's own startup storm
    (Teams, Outlook, Defender scans, Edge preloads, etc.) and WebView2
    cold-init competes with all that. Returns 0.0 if we can't query.
    """
    try:
        import ctypes
        # GetTickCount64 returns ms since boot; wraps at ~584 million years.
        ms = ctypes.windll.kernel32.GetTickCount64()
        return ms / 1000.0
    except Exception:
        return 0.0


def _warm_webview_cache() -> None:
    """Read all files in ~/.ghost/webview-cache into the OS page cache.

    On cold boot, WebView2's UserDataFolder is NOT in memory — the runtime
    pays a full disk-read tax during init. By `stat`+`open` the files up
    front (before pywebview starts), we pull them into Windows's page
    cache so WebView2's subsequent reads hit RAM instead of the disk.

    This shaves several seconds off cold-boot startup empirically.

    Non-fatal: if the cache dir doesn't exist (fresh install) or we can't
    read it, we just skip. The webview works fine without pre-warming.
    """
    from ...infra.paths import WEBVIEW_CACHE
    if not WEBVIEW_CACHE.exists():
        return
    try:
        count = 0
        total_bytes = 0
        # Walk only the first 2 levels of the cache — deeper levels tend
        # to be binary shader caches that webview2 reads lazily on demand.
        # Limit to 200 files / 50MB total so we don't waste time/RAM on
        # a cache that has grown beyond reasonable bounds.
        for root, dirs, files in os.walk(WEBVIEW_CACHE):
            depth = len(Path(root).relative_to(WEBVIEW_CACHE).parts)
            if depth > 2:
                dirs[:] = []
                continue
            for f in files:
                fp = os.path.join(root, f)
                try:
                    sz = os.path.getsize(fp)
                    if sz > 10 * 1024 * 1024:  # skip huge individual files
                        continue
                    with open(fp, "rb") as fh:
                        # Read in 64KB chunks — this pulls pages into the
                        # Windows file-cache without holding them in our
                        # process heap after the function returns.
                        while fh.read(65536):
                            pass
                    count += 1
                    total_bytes += sz
                    if count >= 200 or total_bytes >= 50 * 1024 * 1024:
                        return
                except OSError:
                    continue
        if count:
            log.info("preflight: warmed %d cache files (%d KB)",
                     count, total_bytes // 1024)
    except Exception as e:
        log.warning("preflight: cache warm failed (non-fatal): %s", e)


def cleanup_webview2_state() -> bool:
    """Run full preflight: kill zombies, wait for OS, sweep orphan caches,
    warm the webview-cache page-cache on cold boot.

    Total cost budget: ~2s typical, ~3s cold boot (cache warming + extra
    settle). Previous versions (v1.1.17) could hit 9+ seconds due to
    aggressive retry loops during the OS startup storm.

    Returns True if the kill pass completed (always True in current impl;
    callers log the result but don't gate on it).
    """
    uptime = _system_uptime_seconds()
    cold_boot = uptime > 0 and uptime < 120  # booted <2min ago

    if cold_boot:
        log.info("preflight: cold boot detected (uptime=%.1fs) — warming cache first", uptime)
        # Warm BEFORE kill — the cache is what Ghost will need after
        # webview.start(), regardless of what other apps are doing.
        _warm_webview_cache()

    all_gone = _kill_webview2_helpers()

    # Settle: 600ms normally so the OS releases file handles from killed
    # helpers. On cold boot we want a little more to let the boot storm
    # subside — but not much more, since excessive delay degrades UX.
    time.sleep(0.8 if cold_boot else 0.6)

    try:
        _sweep_orphan_cache_dirs()
    except Exception as e:
        log.warning("preflight: cache sweep error (non-fatal): %s", e)
    return all_gone


def check_webview2_runtime() -> bool:
    """Return True if the WebView2 Evergreen runtime is installed.

    Without the runtime, pywebview fails deep inside its C++ bindings with
    a confusing error. We check via the registry (both HKLM/HKCU, both
    WOW6432Node and native) and surface a MessageBox up the call stack.

    On modern Win10/11 WebView2 is pre-installed, but LTSC images and
    stripped corporate builds sometimes lack it.
    """
    try:
        import winreg
    except Exception:
        return True  # non-Windows / can't check → assume OK, let main error later

    paths = (
        r"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
        r"SOFTWARE\Microsoft\EdgeUpdate\Clients\{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}",
    )
    for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
        for p in paths:
            try:
                k = winreg.OpenKey(root, p)
                winreg.CloseKey(k)
                return True
            except OSError:
                continue
    return False


__all__ = [
    "check_webview2_runtime",
    "cleanup_webview2_state",
    # helpers exported for completeness; main users call cleanup_webview2_state.
    "_kill_webview2_helpers",
    "_sweep_orphan_cache_dirs",
]


# Unused import guard to avoid lint complaint about Path — keeps the import
# available for anyone extending this module with path helpers later.
_ = Path
