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

# Per-image retry budget: each round = 1 taskkill + 300ms settle.
# 6 rounds * 300ms = ~1.8s worst case per image.
_KILL_ROUNDS_PER_IMAGE = 6


def _kill_image_until_gone(image: str, include_self: bool = False) -> bool:
    """Repeatedly `taskkill /F /T /IM <image>` until it reports "not found".

    Returns True when the image is confirmed absent from the process list.
    Returns False if we exhausted the retry budget with processes still
    matching.

    `include_self` — when False (default), a Ghost.exe kill won't terminate
    THIS process (us). That's accomplished by NOT using /T on Ghost.exe,
    because /T would tree-kill OUR own children (we spawned the webview2
    helpers). Instead we kill webview2 by image name separately.
    """
    for _round in range(_KILL_ROUNDS_PER_IMAGE):
        try:
            # /F = force, /T = tree-kill (skip for Ghost.exe itself to
            # avoid self-termination edge cases during shutdown handlers).
            args = ["taskkill", "/F", "/IM", image]
            if include_self or image != "Ghost.exe":
                args.insert(2, "/T")
            r = subprocess.run(
                args,
                capture_output=True, timeout=3,
                creationflags=_CREATE_NO_WINDOW,
            )
            if r.returncode == _TASKKILL_NOT_FOUND:
                return True  # clean, nothing to kill
            if r.returncode == _TASKKILL_OK:
                log.info("preflight: killed stale %s (round %d)", image, _round + 1)
                # Give OS time to release handles before we re-verify.
                time.sleep(0.3)
                continue
            # Other non-zero: transient Win32 issue, retry after short delay.
            time.sleep(0.3)
        except subprocess.TimeoutExpired:
            log.warning("preflight: taskkill %s timed out (round %d)", image, _round + 1)
        except Exception as e:
            log.warning("preflight: taskkill %s error: %s", image, e)
            return False
    # Exhausted budget with processes still matching.
    return False


def _kill_webview2_helpers() -> bool:
    """Kill every Ghost-related image with retry + verification.

    Returns True when ALL four image names are confirmed absent. This is
    the symmetry counterpart of the installer's `KillAllGhostProcesses`
    Pascal function — same contract, same retry budget.
    """
    all_gone = True
    # Up to 3 full sweeps — msedgewebview2 sometimes respawns if the
    # WebView2 runtime had a pending child-spawn queued on its message
    # pump. Second pass catches the late arrival.
    for sweep in range(3):
        sweep_ok = True
        for image in _GHOST_IMAGES:
            # Skip Ghost.exe in preflight (we'd kill ourselves).
            # The installer handles Ghost.exe termination separately.
            if image == "Ghost.exe":
                continue
            if not _kill_image_until_gone(image):
                sweep_ok = False
        if sweep_ok:
            return True
        # At least one image still had live processes. Wait and retry.
        time.sleep(0.3)
        all_gone = False
    return all_gone


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


def cleanup_webview2_state() -> bool:
    """Run full preflight: kill zombies, wait for OS, sweep orphan caches.

    Returns True if all helper images were confirmed absent by the time
    we returned. Callers can log the result but should NOT gate startup
    on it — if a zombie survives, Ghost's single-instance mutex check
    will still detect and recover.

    Idempotent and safe to call multiple times. Total cost: ~1.5s typical,
    ~4s worst-case when retry-budget is exhausted on stubborn zombies.
    """
    all_gone = _kill_webview2_helpers()
    if not all_gone:
        log.warning(
            "preflight: retry budget exhausted — one or more WebView2 helpers "
            "may still be alive; proceeding with cache sweep anyway"
        )

    # Final 600ms settle — even after taskkill reports "not found", Windows
    # can take an additional ~100-300ms to finalize the ImageTeardown and
    # release file handles from the process's mapped DLLs.
    time.sleep(0.6)

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
