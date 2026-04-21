import json
import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from . import history as _history
from PIL import Image

from .capture import capture_fullscreen, capture_region, image_to_base64, image_to_data_url
from .clone import WebCloner, clones_dir
from .config import PRESETS
from .gpt_client import build_user_message, chat_completion
from .meeting import MeetingRecorder, format_time
from .meeting_processor import (
    meetings_dir,
    summarize_meeting,
    transcribe_audio_verbose,
    transcribe_chunks_verbose,
    write_markdown_doc,
)
from .scroll_capture import capture_monitor, list_monitors, scroll_and_capture, stitch_vertical
from .voice import VoiceRecorder
from .win_focus import (
    drag_window_loop,
    force_foreground,
    hide_window,
)


MAX_HISTORY = 10
ROOT = Path(__file__).resolve().parent.parent


def _log_error(ctx: str, e: Exception) -> str:
    tb = traceback.format_exc()
    print(f"[API ERROR] {ctx}: {e}\n{tb}", file=sys.stderr, flush=True)
    return f"{type(e).__name__}: {e}"


def _snapshot_own_webview2_pids() -> list[int]:
    """Walk the Windows process tree and return PIDs of msedgewebview2.exe,
    WebView2Host.exe, and CefSharp.BrowserSubprocess.exe that are descendants
    of the current process. Used by close_app to kill only our OWN helpers,
    not webview2 instances owned by Outlook/Teams/VS Code or by a fresh
    Ghost that happens to be starting up at the same moment.

    Uses CreateToolhelp32Snapshot directly via ctypes so we don't take a
    psutil dependency. Returns [] on non-Windows or if anything fails —
    callers should treat empty as "fall back to image-name sweep"."""
    if sys.platform != "win32":
        return []
    try:
        import ctypes
        from ctypes import wintypes
        kernel32 = ctypes.windll.kernel32
        TH32CS_SNAPPROCESS = 0x00000002

        class PROCESSENTRY32W(ctypes.Structure):
            _fields_ = [
                ("dwSize", wintypes.DWORD),
                ("cntUsage", wintypes.DWORD),
                ("th32ProcessID", wintypes.DWORD),
                ("th32DefaultHeapID", ctypes.c_void_p),
                ("th32ModuleID", wintypes.DWORD),
                ("cntThreads", wintypes.DWORD),
                ("th32ParentProcessID", wintypes.DWORD),
                ("pcPriClassBase", wintypes.LONG),
                ("dwFlags", wintypes.DWORD),
                ("szExeFile", wintypes.WCHAR * 260),
            ]

        kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE
        kernel32.Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
        kernel32.CloseHandle.argtypes = [wintypes.HANDLE]

        snap = kernel32.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not snap or snap == wintypes.HANDLE(-1).value:
            return []

        children_of: dict[int, list[tuple[int, str]]] = {}
        try:
            pe = PROCESSENTRY32W()
            pe.dwSize = ctypes.sizeof(pe)
            if kernel32.Process32FirstW(snap, ctypes.byref(pe)):
                while True:
                    children_of.setdefault(pe.th32ParentProcessID, []).append(
                        (pe.th32ProcessID, pe.szExeFile.lower())
                    )
                    if not kernel32.Process32NextW(snap, ctypes.byref(pe)):
                        break
        finally:
            kernel32.CloseHandle(snap)

        target_names = ("msedgewebview2.exe", "webview2host.exe",
                        "cefsharp.browsersubprocess.exe")
        my_pid = os.getpid()
        found: list[int] = []
        queue = [my_pid]
        visited = {my_pid}
        while queue:
            parent = queue.pop()
            for child_pid, child_name in children_of.get(parent, []):
                if child_pid in visited:
                    continue
                visited.add(child_pid)
                queue.append(child_pid)
                if child_name in target_names:
                    found.append(child_pid)
        return found
    except Exception:
        return []


def _pid_alive(pid: int) -> bool:
    """Return True if the given PID still has a process in the system.
    Using OpenProcess with minimum rights so we can check even for processes
    we don't own."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        kernel32 = ctypes.windll.kernel32
        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not h:
            return False
        # Existing handle may still reference a process that already
        # exited; check exit code to be precise.
        exit_code = ctypes.c_ulong()
        STILL_ACTIVE = 259
        ok = kernel32.GetExitCodeProcess(h, ctypes.byref(exit_code))
        kernel32.CloseHandle(h)
        if not ok:
            return False
        return exit_code.value == STILL_ACTIVE
    except Exception:
        return False


# Mixins that split the class body across focused files. `self` is still
# the GhostAPI instance in every mixin method (state lives here).
from src.api_mixins.window import WindowMixin  # noqa: E402
from src.api_mixins.capture import CaptureMixin  # noqa: E402
from src.api_mixins.chat import ChatMixin  # noqa: E402
from src.api_mixins.meeting import MeetingMixin  # noqa: E402


class GhostAPI(WindowMixin, CaptureMixin, ChatMixin, MeetingMixin):
    def __init__(self):
        self._window = None
        self._hwnd = 0
        self._history: list[dict] = []
        self._monitors = list_monitors()
        self._prev_foreground = 0
        self._last_image = None  # PIL Image held in Python

        # Watch mode: periodic screen capture in background
        self._watch_running = False
        self._watch_interval = 3.0
        self._watch_image = None
        self._watch_lock = threading.Lock()
        self._watch_thread: threading.Thread | None = None

        # Meeting mode
        self._meeting = MeetingRecorder()
        self._meeting_started_at: datetime | None = None
        self._meeting_processing = False
        self._meeting_last_status = ""

        # Voice recorder (mic ou system loopback → Whisper transcribe)
        self._voice = VoiceRecorder()

        # Live transcription during meeting (for Q&A while recording)
        self._live_transcript: list[dict] = []
        self._live_transcribe_thread: threading.Thread | None = None
        self._last_transcribed_sec: float = 0.0

        # Dock mode: saved window rect before docking to edge
        self._saved_rect: tuple[int, int, int, int] | None = None

        # Global keyboard capture (type without giving Ghost focus)
        self._kb_listener = None

        # Response popup window (pre-created, hidden until compact mode shows a response)
        self._response_window = None
        self._response_hwnd = 0

        # Web page cloner (URL → local HTML + assets bundle)
        self._cloner = WebCloner()

        # Floating dropdown popup (third window — pre-created hidden off-screen)
        self._dropdown_window = None
        self._dropdown_hwnd = 0

        # ----- Application services (facade composition) ------------------
        # Bridge methods below delegate to these. Each service owns one
        # cohesive feature and can be unit-tested independently of the
        # pywebview bridge. Services receive a `window_getter` callable so
        # they can push progress events / JS callbacks without holding a
        # stale window reference when Ghost recreates it.
        from .services.history_service import HistoryService
        from .services.settings_service import SettingsService
        from .services.update_service import UpdateService
        self._update_svc = UpdateService(lambda: self._window)
        self._settings_svc = SettingsService()
        self._history_svc = HistoryService()

    def set_window(self, window):
        self._window = window

    def set_hwnd(self, hwnd: int):
        self._hwnd = hwnd

    def set_response_window(self, window):
        self._response_window = window

    def set_response_hwnd(self, hwnd: int):
        self._response_hwnd = hwnd

    # ---------- Settings ----------

    def open_url(self, url: str) -> dict:
        """Open a URL in the user's default browser (used by the 'Update' banner)."""
        try:
            import webbrowser
            if not url or not isinstance(url, str):
                return {"error": "invalid url"}
            if not (url.startswith("http://") or url.startswith("https://")):
                return {"error": "only http(s) urls are allowed"}
            webbrowser.open(url, new=2)
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("open_url", e)}

    def get_app_info(self) -> dict:
        """Return app version + author metadata for the 'About' view and footers."""
        try:
            from .version import (
                AUTHOR_EMAIL,
                AUTHOR_GITHUB,
                AUTHOR_LINKEDIN,
                AUTHOR_NAME,
                GITHUB_RELEASES_URL,
                GITHUB_REPO_URL,
                __version__,
            )
            return {
                "version": __version__,
                "author": AUTHOR_NAME,
                "authorEmail": AUTHOR_EMAIL,
                "authorLinkedin": AUTHOR_LINKEDIN,
                "authorGithub": AUTHOR_GITHUB,
                "repoUrl": GITHUB_REPO_URL,
                "releasesUrl": GITHUB_RELEASES_URL,
            }
        except Exception as e:
            return {"error": _log_error("get_app_info", e)}

    # =====================================================================
    # Floating dropdown popup — third pywebview window that renders chip
    # options in compact mode outside the 200px bar's bounds (so the full
    # 7-option list overlays the app the way a native menu does).
    # =====================================================================
    def set_dropdown_window(self, window) -> None:
        self._dropdown_window = window

    def set_dropdown_hwnd(self, hwnd: int) -> None:
        self._dropdown_hwnd = int(hwnd)





    def download_and_install_update(self) -> dict:
        """Fetches the latest installer from GitHub Releases and launches it.

        Windows: runs GhostSetup.exe with Inno Setup's /SILENT flag, which
                 closes Ghost, upgrades, and restarts the app automatically.
        macOS:   runs `open GhostInstaller.pkg` which triggers the standard
                 Installer wizard. The user authenticates once; Gatekeeper is
                 bypassed because the file wasn't downloaded via a browser
                 (no `com.apple.quarantine` attribute when fetched via urllib).

        Download progress is reported to the webview via
        `window.setUpdateProgress(pct)`.
        """
        try:
            return self._update_svc.download_and_install()
        except Exception as e:
            return {"error": _log_error("download_and_install_update", e)}

    def check_for_updates(self, force: bool = False) -> dict:
        """Query GitHub Releases and compare with the current version.
        Returns {hasUpdate, current, latest, releaseUrl, releaseNotes} or {error}.
        Safe to call multiple times — result is cached in-process.
        """
        try:
            return self._update_svc.check(force=bool(force))
        except Exception as e:
            return {"error": _log_error("check_for_updates", e)}

    def get_settings(self) -> dict:
        """Return current settings (without exposing the full API key)."""
        try:
            return self._settings_svc.get_settings()
        except Exception as e:
            return {"error": _log_error("get_settings", e)}

    def set_openai_model(self, model_id: str) -> dict:
        """Save user's model choice. Must be in SUPPORTED_MODEL_IDS."""
        try:
            return self._settings_svc.set_openai_model(model_id)
        except Exception as e:
            return {"error": _log_error("set_openai_model", e)}

    def save_openai_key(self, key: str, replace_existing: bool = False) -> dict:
        """Validate the key (including required permissions) and save it.

        Tests:
          1. Basic access (models.list)
          2. Chat completions (tiny 1-token call — cost ~$0.0000003)
          3. Audio/Whisper endpoint availability (best-effort)

        Returns dict with:
          - ok: bool
          - permissions: {chat, audio, models}
          - error: str (if failed)
        """
        try:
            return self._settings_svc.save_openai_key(key, replace_existing=replace_existing)
        except Exception as e:
            return {"error": _log_error("save_openai_key", e)}

    def clear_openai_key(self) -> dict:
        """Remove the stored API key."""
        try:
            return self._settings_svc.clear_openai_key()
        except Exception as e:
            return {"error": _log_error("clear_openai_key", e)}

    def read_clipboard(self) -> dict:
        """Return current clipboard text (used for pasting API keys etc)."""
        try:
            import pyperclip
            text = pyperclip.paste() or ""
            return {"ok": True, "text": text.strip()}
        except Exception as e:
            return {"error": _log_error("read_clipboard", e)}

    def openai_tts(self, text: str, voice: str = "nova") -> dict:
        """Generate speech via OpenAI TTS. Returns base64 data URL.
        Voices: alloy, echo, fable, onyx, nova, shimmer. 'nova' soa natural em pt-BR.
        Uses 'wav' format for lowest decoding latency + speed 1.08 para ritmo natural.
        """
        try:
            import base64
            import time

            from openai import OpenAI

            from .config import get_openai_key

            key = get_openai_key()
            if not key:
                return {"error": "Sem API key configurada"}

            text = (text or "").strip()
            if not text:
                return {"error": "Texto vazio"}
            if len(text) > 3000:
                text = text[:3000]

            t0 = time.time()
            client = OpenAI(api_key=key, timeout=30.0)
            # wav tem zero latência de decode no browser (PCM bruto)
            result = client.audio.speech.create(
                model="tts-1",
                voice=voice,
                input=text,
                response_format="wav",
                speed=1.08,
            )
            audio_bytes = result.content if hasattr(result, "content") else result.read()
            dt = int((time.time() - t0) * 1000)
            b64 = base64.b64encode(audio_bytes).decode("ascii")
            print(f"[tts] {len(text)} chars -> {len(audio_bytes)} bytes em {dt}ms", flush=True)
            return {"ok": True, "audio_url": f"data:audio/wav;base64,{b64}", "elapsed_ms": dt}
        except Exception as e:
            return {"error": _log_error("openai_tts", e)}

    # ============ History (conversas salvas em ~/.ghost/history.json) ============

    def history_list(self) -> dict:
        try:
            return self._history_svc.list()
        except Exception as e:
            return {"error": _log_error("history_list", e)}

    def history_get(self, conv_id: str) -> dict:
        try:
            c = _history.get_conversation(conv_id)
            if not c:
                return {"error": "Conversa não encontrada"}
            return {"ok": True, "conversation": c}
        except Exception as e:
            return {"error": _log_error("history_get", e)}

    def history_save(self, conv_id: str, messages: list) -> dict:
        try:
            return self._history_svc.save(conv_id, messages)
        except Exception as e:
            return {"error": _log_error("history_save", e)}

    def history_delete(self, conv_id: str) -> dict:
        try:
            return self._history_svc.delete(conv_id)
        except Exception as e:
            return {"error": _log_error("history_delete", e)}

    def history_new_id(self) -> dict:
        return self._history_svc.new_id()





    # ============ Streaming chat (token-by-token via evaluate_js) ============





    # ============ Live Q&A durante reunião ============


    # ============ Detecção de informação sensível ============


    # ============ Drag-and-drop: parse file content ============


    # ============ Voice input (mic OR system audio → Whisper) ============

    def voice_start(self, source: str = "mic") -> dict:
        """Start recording audio for transcription. source='mic' or 'system'."""
        try:
            if self._voice.is_running():
                return {"error": "Já gravando"}
            # Garante que não existe outra captura de áudio conflitando
            if self._meeting.is_running():
                return {"error": "Reunião em andamento — finalize primeiro"}
            self._voice.start(source)
            # Pequeno delay pra threads de áudio iniciarem o stream
            time.sleep(0.05)
            if not self._voice.is_running():
                err = self._voice.last_error() or "falha ao iniciar"
                return {"error": err}
            return {"ok": True, "source": self._voice.source()}
        except Exception as e:
            return {"error": _log_error("voice_start", e)}

    def voice_status(self) -> dict:
        """Poll recording state."""
        try:
            return {
                "running": self._voice.is_running(),
                "source": self._voice.source(),
                "elapsed_sec": self._voice.elapsed(),
                "error": self._voice.last_error(),
            }
        except Exception as e:
            return {"error": _log_error("voice_status", e)}

    def voice_stop_and_transcribe(self) -> dict:
        """Stop recording and transcribe via Whisper. Returns {text: ...}."""
        try:
            wav_path = self._voice.stop()
            if wav_path is None:
                err = self._voice.last_error() or "Nenhum áudio gravado"
                return {"error": err}
            if not wav_path.exists() or wav_path.stat().st_size < 512:
                try:
                    wav_path.unlink()
                except Exception:
                    pass
                return {"error": "Áudio muito curto"}

            # Whisper precisa de chave
            from .config import get_openai_key
            key = get_openai_key()
            if not key:
                return {"error": "Configure a API key OpenAI em ⚙"}

            from openai import OpenAI
            client = OpenAI(api_key=key, timeout=60.0)
            t0 = time.time()
            with open(wav_path, "rb") as f:
                result = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=f,
                    language="pt",
                    response_format="text",
                )
            dt = int((time.time() - t0) * 1000)
            text = (result if isinstance(result, str) else getattr(result, "text", "")).strip()
            try:
                wav_path.unlink()
            except Exception:
                pass
            print(f"[voice] transcrevido em {dt}ms: '{text[:60]}...'", flush=True)
            return {"ok": True, "text": text, "elapsed_ms": dt}
        except Exception as e:
            return {"error": _log_error("voice_stop_and_transcribe", e)}

    def voice_cancel(self) -> dict:
        """Cancel recording without transcribing."""
        try:
            self._voice.cancel()
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("voice_cancel", e)}

    def open_url(self, url: str) -> dict:
        """Open a URL in the user's default browser (Windows: ShellExecute)."""
        print(f"[open_url] called with: {url}", flush=True)
        try:
            if sys.platform == "win32":
                os.startfile(url)
            else:
                import webbrowser
                webbrowser.open(url)
            print("[open_url] launched OK", flush=True)
            return {"ok": True}
        except Exception as e:
            print(f"[open_url] error: {e}", flush=True)
            # Fallback: try webbrowser anyway
            try:
                import webbrowser
                webbrowser.open(url)
                return {"ok": True, "fallback": True}
            except Exception as e2:
                return {"error": _log_error("open_url", e2)}


    # ---------- Config ----------

    def get_presets(self) -> list[str]:
        return list(PRESETS.keys())



    # Process-image names for native meeting apps. Matched case-insensitively.
    _MEETING_APP_PROCESSES = (
        "teams.exe", "ms-teams.exe", "msteams.exe",
        "zoom.exe", "cpthost.exe",   # zoom main + helper
        "webex.exe", "webexmta.exe", "ciscowebex.exe", "ciscowebexstart.exe",
        "skype.exe", "lync.exe",
        "discord.exe",
        "slack.exe",
        "bluejeans.exe",
        "gotomeeting.exe", "gotoopener.exe", "goto.exe", "gotomeet.exe",
        "whereby.exe",
        "meetingroomcontrol.exe",
    )

    # Substrings matched in the window title (case-insensitive). Covers both
    # native apps that prefix their titles and browser tabs pinned to
    # meeting-platform URLs. Generic browser process names aren't enough —
    # we match on the TAB title (which the browser puts in the window title).
    # For Google Meet specifically, Chrome shows "Meet - abc-defg-hij -
    # Google Chrome", so "meet - " is the reliable anchor (domain rarely
    # appears in window titles, only in the URL bar).
    _MEETING_TITLE_PATTERNS = (
        # Google Meet — tab title is "Meet - <room-id> - <Browser>"
        "meet.google.com", "google meet", "meet - ", "meet – ",
        # Microsoft Teams — native app and web app
        "teams.microsoft.com", "microsoft teams", "teams meeting",
        "| microsoft teams", "- microsoft teams",
        # Zoom — native "Zoom Meeting", web "zoom.us", sometimes just "Zoom"
        "zoom.us", "zoom meeting", "zoom.com", "zoom - ", " - zoom",
        # Cisco Webex
        "webex.com", "cisco webex", " | webex", "webex meeting", "webex - ",
        # Jitsi (public + self-hosted)
        "meet.jit.si", "jitsi meet", "jitsi",
        # Whereby
        "whereby.com", "whereby - ", " - whereby",
        # Skype (legacy + business)
        "skype.com", "skype for business", " - skype",
        # Discord (calls, huddles, stage channels)
        "discord",
        # Slack (huddles, calls)
        " - slack", "slack | ", "huddle",
        # BlueJeans
        "bluejeans.com", "bluejeans - ",
        # GoToMeeting
        "gotomeeting.com", "goto meeting", "gotomeeting", "goto.com/meeting",
        # Workplace from Meta
        "workplace.com", "workplace chat",
        # Amazon Chime
        "chime.aws", "amazon chime",
        # WhatsApp Web (audio/video calls via web client)
        "whatsapp web", "web.whatsapp.com",
        # Generic meeting-ish signals — catch-all for obscure apps that put
        # "meeting" / "call" in the title. Low-false-positive because we
        # still require the substring to appear in a visible top-level window.
        "meeting in progress", "in-call",
    )





    # ---------- Watch mode ----------






    # ---------- Meeting mode ----------










    # ---------- Clonagem de página web ----------

    def start_clone(self, url: str) -> dict:
        """Kick off background cloning of a URL → offline HTML bundle."""
        try:
            return self._cloner.start(url)
        except Exception as e:
            return {"error": _log_error("start_clone", e)}

    def get_clone_status(self) -> dict:
        try:
            return self._cloner.get_status()
        except Exception as e:
            return {"error": _log_error("get_clone_status", e)}

    def cancel_clone(self) -> dict:
        try:
            return self._cloner.cancel()
        except Exception as e:
            return {"error": _log_error("cancel_clone", e)}

    def consume_clone_result(self) -> dict:
        try:
            r = self._cloner.consume_result()
            return r if r is not None else {"pending": True}
        except Exception as e:
            return {"error": _log_error("consume_clone_result", e)}

    def open_clones_folder(self) -> dict:
        try:
            d = clones_dir()
            os.startfile(str(d))
            return {"ok": True, "path": str(d)}
        except Exception as e:
            return {"error": _log_error("open_clones_folder", e)}

    def open_cloned_page(self, index_path: str) -> dict:
        """Open a cloned index.html in the user's default browser."""
        try:
            import webbrowser
            p = Path(index_path)
            if not p.exists():
                return {"error": "Arquivo não encontrado"}
            webbrowser.open(p.as_uri(), new=2)
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("open_cloned_page", e)}

    # ---------- Window ----------

















    # ---------- Focus management ----------



    # ---------- Capture ----------




    # ---------- GPT ----------


