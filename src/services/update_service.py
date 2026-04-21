"""Update service — version check + download + install.

Consolidates the pre-refactor split between `src/updater.py` (check) and
`GhostAPI.download_and_install_update` (download + launch + self-exit). All
the hard-won knowledge from those two files is preserved here verbatim as
comments and code — see the CLAUDE.md shipping log for incident history.

Public surface used by the facade:
    UpdateService(window_getter).check(force: bool) -> dict
    UpdateService(window_getter).download_and_install() -> dict

Behavior is byte-for-byte identical to the prior implementation:
  * `check` returns {hasUpdate, current, latest, releaseUrl, releaseNotes}
  * `download_and_install` downloads the latest installer, reports progress
    via `window.setUpdateProgress(pct)`, and launches Inno Setup with the
    same `/SILENT /CLOSEAPPLICATIONS /FORCECLOSEAPPLICATIONS /RESTARTAPPLICATIONS`
    flags via a PowerShell helper to survive the Python process exit.
"""
from __future__ import annotations

import contextlib
import datetime as _dt
import os
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from src.domain.version_compare import is_newer
from src.infra.logging_setup import get_logger
from src.infra.paths import UPDATER_LOG_FILE, ensure_user_data
from src.integrations.github_releases import fetch_latest_release, installer_asset_url
from src.version import GITHUB_RELEASES_URL, __version__

log = get_logger(__name__)

_CACHE_LOCK = threading.Lock()
_CACHED: UpdateInfo | None = None


@dataclass(frozen=True)
class UpdateInfo:
    current: str
    latest: str
    has_update: bool
    release_url: str
    release_notes: str

    def to_dict(self) -> dict:
        return {
            "current": self.current,
            "latest": self.latest,
            "hasUpdate": self.has_update,
            "releaseUrl": self.release_url,
            "releaseNotes": self.release_notes,
        }


def _check(force: bool = False) -> UpdateInfo | None:
    """Synchronous check — cached for process lifetime unless `force=True`."""
    global _CACHED
    with _CACHE_LOCK:
        if _CACHED is not None and not force:
            return _CACHED
    data = fetch_latest_release()
    if not data:
        return None
    latest_tag = (data.get("tag_name") or "").lstrip("v")
    release_url = data.get("html_url") or GITHUB_RELEASES_URL
    release_notes = (data.get("body") or "").strip()
    current = __version__.lstrip("v")
    info = UpdateInfo(
        current=current,
        latest=latest_tag or "?",
        has_update=is_newer(latest_tag, current),
        release_url=release_url,
        release_notes=release_notes,
    )
    with _CACHE_LOCK:
        _CACHED = info
    return info


def _check_async(callback) -> threading.Thread:
    """Fire a background check and invoke `callback(info_or_None)`."""
    def _run() -> None:
        info = _check()
        try:
            callback(info)
        except Exception as e:
            log.warning("update-check callback error: %s", e)
    t = threading.Thread(target=_run, name="ghost-update-check", daemon=True)
    t.start()
    return t


class UpdateService:
    """Application service — bridges UI update actions to the update flow.

    Constructor parameter:
        window_getter: a zero-arg callable returning the main pywebview
                       Window (or None). Used to push progress events via
                       `.evaluate_js("window.setUpdateProgress(<pct>)")`.
                       Taken as a callable (not a bound reference) so the
                       caller can hand us a lazy accessor.
    """

    def __init__(self, window_getter: Callable[[], object | None]):
        self._window_getter = window_getter

    # ---- check ----

    def check(self, force: bool = False) -> dict:
        """Frontend-facing check. Returns the dict shape the UI expects."""
        info = _check(force=bool(force))
        if info is None:
            return {
                "hasUpdate": False,
                "current": __version__,
                "latest": __version__,
                "error": "offline",
            }
        return info.to_dict()

    # ---- download + install ----

    def download_and_install(self) -> dict:
        """Fetch the latest installer from GitHub and launch it.

        See the comment block inside for the full architectural history —
        three separate failure modes were encountered during the 1.0.x series
        and the current shape (PowerShell helper + self-exit without
        taskkill /T) is the minimum that survives all three.
        """
        if sys.platform == "win32":
            asset_name = "GhostSetup.exe"
        elif sys.platform == "darwin":
            asset_name = "GhostInstaller.pkg"
        else:
            return {"error": f"auto-update not supported on {sys.platform}"}

        url = installer_asset_url(asset_name)
        tmpdir = Path(tempfile.gettempdir()) / "ghost-update"
        tmpdir.mkdir(parents=True, exist_ok=True)
        target = tmpdir / asset_name

        # ---------- download with progress ----------
        last_pct = -1
        req = urllib.request.Request(
            url, headers={"User-Agent": f"Ghost/{__version__}"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            total = int(resp.headers.get("Content-Length", 0))
            with open(target, "wb") as f:
                downloaded = 0
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    downloaded += len(chunk)
                    if total:
                        pct = int(downloaded * 100 / total)
                        # Throttle to every 2% so we don't spam evaluate_js.
                        if pct >= last_pct + 2:
                            last_pct = pct
                            self._report_progress(pct)

        # Report 100% before launching.
        self._report_progress(100)

        # ---------- launch installer + self-exit ----------
        #
        # Architecture lessons learned the hard way:
        #
        # 1. `cmd /c timeout /t 3 && installer` FAILS silently when spawned
        #    with DETACHED_PROCESS: timeout needs a console, it fails,
        #    `&&` short-circuits, installer never runs.
        #
        # 2. `taskkill /F /T /PID <self>` (tree kill) KILLS the PowerShell
        #    helper because PS is a direct descendant of our Python. So the
        #    3s delay never completes — installer never launches.
        #
        # 3. `-WindowStyle Hidden` passed to Start-Process inside the PS
        #    helper HIDES the installer's own UI window. User sees nothing,
        #    and in some cases Inno Setup fails to run at all because
        #    /SILENT still needs a visible progress dialog.
        #
        # Correct approach:
        #   a) Kill WebView2 helper processes BY NAME (not tree), so they
        #      release the UserData folder + libsndfile_x64.dll.
        #   b) Sleep ~1s for OS to release file handles.
        #   c) Spawn PowerShell helper detached — PS window itself stays
        #      hidden, but it launches the installer in NORMAL window mode
        #      so the user sees the Inno Setup progress dialog.
        #   d) Log every step to ~/.ghost/updater.log so failures are
        #      diagnosable next time.
        #   e) `os._exit(0)` WITHOUT `taskkill /T` — our Python dies cleanly
        #      (releasing the mutex + our DLL handles), PS survives as an
        #      orphan, completes its 2s delay, launches installer, installer
        #      /RESTARTAPPLICATIONS relaunches Ghost.
        if sys.platform == "win32":
            if not target.exists() or target.stat().st_size < 1024:
                return {"error": f"installer download failed or truncated: {target}"}
            self._launch_windows_installer(target)
        else:
            subprocess.Popen(["open", str(target)])
            def _exit_soon():
                time.sleep(1.5)
                os._exit(0)
            threading.Thread(target=_exit_soon, daemon=True).start()
        return {"ok": True, "target": str(target)}

    # ---- helpers ----

    def _report_progress(self, pct: int) -> None:
        try:
            win = self._window_getter()
            if win is not None:
                win.evaluate_js(f"window.setUpdateProgress({pct})")
        except Exception:
            pass

    def _launch_windows_installer(self, target: Path) -> None:
        ensure_user_data()
        updater_log = UPDATER_LOG_FILE

        def _ulog(msg: str) -> None:
            try:
                with open(updater_log, "a", encoding="utf-8") as f:
                    f.write(f"[{_dt.datetime.now().isoformat()}] {msg}\n")
            except Exception:
                pass

        _ulog("=" * 60)
        _ulog(f"update start: target={target}, size={target.stat().st_size}")

        # NOTE: do NOT pass DETACHED_PROCESS when spawning PowerShell.
        # Combined with CREATE_NO_WINDOW it causes PowerShell to fail
        # silently (PS starts but immediately exits, never running our
        # script). Tested: with DETACHED_PROCESS | CREATE_NO_WINDOW,
        # logs never get written; without DETACHED_PROCESS they do.
        # PS doesn't need DETACHED_PROCESS to survive our exit — it
        # just needs to not be in our process tree for taskkill, and
        # since we no longer call taskkill /T, plain Popen suffices.
        CREATE_NEW_PROCESS_GROUP = 0x00000200
        CREATE_NO_WINDOW = 0x08000000
        helper_flags = CREATE_NEW_PROCESS_GROUP | CREATE_NO_WINDOW

        # (a) Kill WebView2 helpers globally by name — not tree — so our
        #     PowerShell child survives. Ghost is the only known user of
        #     WebView2 at this install; killing by name is safe.
        for image in ("msedgewebview2.exe", "WebView2Host.exe"):
            try:
                result = subprocess.run(
                    ["taskkill", "/F", "/IM", image],
                    capture_output=True, timeout=4,
                    creationflags=CREATE_NO_WINDOW,
                )
                _ulog(f"taskkill {image}: rc={result.returncode}")
            except Exception as e:
                _ulog(f"taskkill {image} error: {e}")

        # (b) Brief pause for OS to release file handles the webview had
        time.sleep(0.8)

        # (c) PowerShell helper: short delay then launch installer with
        #     visible progress window. NO -WindowStyle Hidden on the
        #     installer — user sees the Inno Setup progress dialog.
        helper_log = str(updater_log).replace("'", "''")
        installer_path = str(target).replace("'", "''")
        ps_script = (
            "$ErrorActionPreference='Continue'; "
            f"'[ps] waking after sleep' | Out-File -FilePath '{helper_log}' -Append -Encoding utf8; "
            "Start-Sleep -Seconds 2; "
            "try { "
            f"  $p = Start-Process -FilePath '{installer_path}' "
            "   -ArgumentList '/SILENT','/CLOSEAPPLICATIONS',"
            "   '/FORCECLOSEAPPLICATIONS','/RESTARTAPPLICATIONS' "
            "   -PassThru -ErrorAction Stop; "
            f"  \"[ps] installer launched pid=$($p.Id)\" | Out-File -FilePath '{helper_log}' -Append -Encoding utf8; "
            "} catch { "
            f"  \"[ps] FAILED: $($_.Exception.Message)\" | Out-File -FilePath '{helper_log}' -Append -Encoding utf8; "
            "}"
        )

        try:
            ps = subprocess.Popen(
                ["powershell.exe", "-NoProfile", "-NonInteractive",
                 "-WindowStyle", "Hidden", "-ExecutionPolicy", "Bypass",
                 "-Command", ps_script],
                creationflags=helper_flags,
                close_fds=True,
            )
            _ulog(f"powershell spawned: pid={ps.pid}")
        except FileNotFoundError as e:
            _ulog(f"powershell not found: {e} — falling back to direct launch")
            # Fallback: spawn installer directly; it'll race us a bit
            # but Inno Setup's /CLOSEAPPLICATIONS handles our process.
            subprocess.Popen(
                [str(target), "/SILENT", "/CLOSEAPPLICATIONS",
                 "/FORCECLOSEAPPLICATIONS", "/RESTARTAPPLICATIONS"],
                creationflags=helper_flags,
                close_fds=True,
            )

        # (d) Destroy webview windows before exit so the main window's
        #     UserData can be flushed cleanly.
        try:
            import webview as _webview
            for w in list(_webview.windows):
                with contextlib.suppress(Exception):
                    w.destroy()
        except Exception:
            pass

        # (e) Exit self WITHOUT /T — PowerShell must survive to launch
        #     the installer. Our exit releases the mutex and all file
        #     handles (including libsndfile). Schedule via Timer so the
        #     IPC response to `download_and_install_update` returns OK
        #     first; otherwise the webview sees the bridge tear down
        #     before the promise resolves.
        _ulog("self-exit scheduled in 300ms")
        def _exit():
            _ulog("os._exit(0)")
            os._exit(0)
        threading.Timer(0.3, _exit).start()


# ---- backwards-compat surface for `src.updater` shim ----
# The pre-refactor updater.py exposed `check` and `check_async` at module
# level. Keep those names resolvable from this module so the shim re-export
# stays symmetric.
check = _check
check_async = _check_async
