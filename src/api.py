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
from .capture import capture_fullscreen, capture_region, image_to_base64, image_to_data_url
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
if sys.platform == "win32":
    from .win_focus import (
        drag_window_loop,
        force_foreground,
        hide_window,
    )
else:
    # Stubs on non-Windows so the module imports cleanly; these features are
    # currently Windows-only and the calls happen only from Win-specific paths.
    def drag_window_loop(*_args, **_kwargs): return False
    def force_foreground(*_args, **_kwargs): return False
    def hide_window(*_args, **_kwargs): return False

MAX_HISTORY = 10
ROOT = Path(__file__).resolve().parent.parent


def _log_error(ctx: str, e: Exception) -> str:
    tb = traceback.format_exc()
    print(f"[API ERROR] {ctx}: {e}\n{tb}", file=sys.stderr, flush=True)
    return f"{type(e).__name__}: {e}"


class GhostAPI:
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

    def check_for_updates(self, force: bool = False) -> dict:
        """Query GitHub Releases and compare with the current version.
        Returns {hasUpdate, current, latest, releaseUrl, releaseNotes} or {error}.
        Safe to call multiple times — result is cached in-process.
        """
        try:
            from .updater import check
            info = check(force=bool(force))
            if info is None:
                from .version import __version__
                return {
                    "hasUpdate": False,
                    "current": __version__,
                    "latest": __version__,
                    "error": "offline",
                }
            return info.to_dict()
        except Exception as e:
            return {"error": _log_error("check_for_updates", e)}

    def get_settings(self) -> dict:
        """Return current settings (without exposing the full API key)."""
        try:
            from .config import SUPPORTED_MODELS, get_openai_key, get_openai_model
            key = get_openai_key()
            masked = ""
            if key:
                if len(key) > 10:
                    masked = key[:7] + "..." + key[-4:]
                else:
                    masked = "***"
            return {
                "has_openai_key": bool(key),
                "masked_key": masked,
                "openai_model": get_openai_model(),
                "available_models": SUPPORTED_MODELS,
            }
        except Exception as e:
            return {"error": _log_error("get_settings", e)}

    def set_openai_model(self, model_id: str) -> dict:
        """Save user's model choice. Must be in SUPPORTED_MODEL_IDS."""
        try:
            from .config import SUPPORTED_MODEL_IDS, load_user_config, save_user_config
            mid = (model_id or "").strip()
            if mid not in SUPPORTED_MODEL_IDS:
                return {"error": f"Modelo não suportado: {mid}"}
            cfg = load_user_config()
            cfg["openai_model"] = mid
            save_user_config(cfg)
            return {"ok": True, "openai_model": mid}
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
            key = (key or "").strip()
            if not key:
                return {"error": "Chave vazia"}
            if not key.startswith("sk-"):
                return {"error": "Formato inválido — deve começar com 'sk-'"}

            # Block overwriting a configured key unless explicit
            from .config import get_openai_key, load_user_config, save_user_config
            current = get_openai_key()
            if current and not replace_existing and current != key:
                return {
                    "error": "Já existe uma chave configurada. Remova a atual antes de adicionar outra.",
                    "replace_required": True,
                }

            from openai import OpenAI
            client = OpenAI(api_key=key, timeout=15.0)

            # Test 1: basic access
            try:
                models = client.models.list()
                _ = next(iter(models), None)
            except Exception as e:
                err_str = str(e)
                if "401" in err_str or "Incorrect API key" in err_str:
                    return {"error": "Chave rejeitada pela OpenAI (401 - inválida)"}
                return {"error": f"Falha ao validar chave: {err_str[:200]}"}

            # Test 2: chat permission (costs ~$0.0000003)
            chat_ok = False
            chat_err = None
            try:
                client.chat.completions.create(
                    model="gpt-4.1-mini",
                    messages=[{"role": "user", "content": "ok"}],
                    max_tokens=1,
                )
                chat_ok = True
            except Exception as e:
                chat_err = str(e)

            if not chat_ok:
                if chat_err and "insufficient_quota" in chat_err:
                    return {
                        "error": "Chave válida mas SEM créditos. Adicione saldo em platform.openai.com/billing",
                    }
                if chat_err and ("403" in chat_err or "permission" in chat_err.lower()
                                  or "insufficient permissions" in chat_err.lower()):
                    return {
                        "error": "Chave com RESTRIÇÕES: permissão de chat.completions desabilitada. "
                                 "Crie uma chave com 'All' permissions ou habilite 'Model capabilities: Write'.",
                        "permissions": {"models": True, "chat": False, "audio": "unknown"},
                    }
                return {"error": f"Chat falhou: {(chat_err or 'erro desconhecido')[:200]}"}

            # Test 3: audio permission — can't pre-test without real audio, but
            # we can heuristically verify the key's capabilities via /v1/models
            # (whisper-1 appears in the list if audio is enabled for the key).
            audio_ok = True
            try:
                model_ids = []
                for m in client.models.list():
                    mid = getattr(m, "id", "") or ""
                    model_ids.append(mid)
                    if len(model_ids) > 200:
                        break
                if "whisper-1" not in model_ids:
                    audio_ok = False
            except Exception:
                audio_ok = True  # don't block save on this heuristic

            # Save
            cfg = load_user_config()
            cfg["openai_api_key"] = key
            save_user_config(cfg)

            warnings = []
            if not audio_ok:
                warnings.append(
                    "Permissão de Whisper não detectada — gravação de reuniões pode falhar."
                )

            return {
                "ok": True,
                "permissions": {
                    "models": True,
                    "chat": True,
                    "audio": audio_ok,
                },
                "warnings": warnings,
            }
        except Exception as e:
            return {"error": _log_error("save_openai_key", e)}

    def clear_openai_key(self) -> dict:
        """Remove the stored API key."""
        try:
            from .config import load_user_config, save_user_config
            cfg = load_user_config()
            cfg.pop("openai_api_key", None)
            save_user_config(cfg)
            return {"ok": True}
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
            return {"ok": True, "conversations": _history.list_conversations()}
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
            meta = _history.save_conversation(conv_id, messages)
            return {"ok": True, "meta": meta}
        except Exception as e:
            return {"error": _log_error("history_save", e)}

    def history_delete(self, conv_id: str) -> dict:
        try:
            ok = _history.delete_conversation(conv_id)
            return {"ok": ok}
        except Exception as e:
            return {"error": _log_error("history_delete", e)}

    def history_new_id(self) -> dict:
        return {"ok": True, "id": _history.new_id()}

    def update_popup_title(self, title: str) -> dict:
        """Atualiza o header do popup de resposta com o título da conversa."""
        try:
            if self._response_window is None:
                return {"ok": True}
            safe = json.dumps(title or "")
            try:
                self._response_window.evaluate_js(
                    f"window.setPopupTitle && window.setPopupTitle({safe})"
                )
            except Exception:
                pass
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("update_popup_title", e)}

    def history_suggest_title(self, conv_id: str) -> dict:
        """Gera título inteligente pra uma conversa via IA e persiste.
        Roda em thread pra não bloquear — retorna imediatamente com ok.
        Frontend pode recarregar a lista depois pra ver o título atualizado."""
        try:
            from .config import get_openai_key
            if not get_openai_key():
                return {"ok": False, "reason": "no api key"}

            def worker():
                try:
                    from .gpt_client import generate_conversation_title
                    conv = _history.get_conversation(conv_id)
                    if not conv:
                        return
                    msgs = conv.get("messages", [])
                    if len(msgs) < 2:
                        return  # pouco contexto pra titular
                    new_title = generate_conversation_title(msgs)
                    if not new_title:
                        return
                    # Atualiza em disco sem tocar nas mensagens
                    all_data = _history._load()
                    for c in all_data.get("conversations", []):
                        if c.get("id") == conv_id:
                            c["title"] = new_title
                            _history._save(all_data)
                            print(f"[title] {conv_id} -> {new_title}", flush=True)
                            break
                except Exception as e:
                    print(f"[title] worker error: {e}", flush=True)

            threading.Thread(target=worker, daemon=True).start()
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("history_suggest_title", e)}

    def branch_summarize(self, messages: list) -> dict:
        """Gera um resumo conciso da conversa pra usar como contexto inicial
        de uma nova conversa (branch). Retorna {ok: True, summary: "..."}."""
        try:
            from .config import get_openai_key, get_openai_model
            key = get_openai_key()
            if not key:
                return {"error": "Sem API key — configure nas Configurações"}
            if not messages:
                return {"error": "Nenhuma mensagem pra resumir"}

            # Formata conversa como texto pra enviar ao resumidor
            lines = []
            for m in messages:
                role = m.get("role", "user")
                text = (m.get("text") or "").strip()
                if not text:
                    continue
                tag = "Usuário" if role == "user" else "Assistente"
                lines.append(f"{tag}: {text}")
            convo = "\n\n".join(lines)
            if not convo:
                return {"error": "Conversa vazia"}

            prompt = (
                "Resuma a conversa abaixo em formato markdown conciso (3-6 bullet "
                "points). Destaque o tópico principal, decisões tomadas, problemas "
                "em aberto e qualquer dado específico relevante (nomes, números, "
                "trechos de código curtos). Escreva em português. Não adicione "
                "saudação nem despedida — só o resumo puro.\n\n"
                "---\n\n"
                f"{convo}\n\n"
                "---\n\n"
                "Resumo:"
            )

            from openai import OpenAI
            client = OpenAI(api_key=key, timeout=45.0)
            resp = client.chat.completions.create(
                model=get_openai_model(),
                messages=[{"role": "user", "content": prompt}],
                max_tokens=500,
                temperature=0.3,
            )
            summary = (resp.choices[0].message.content or "").strip()
            return {"ok": True, "summary": summary}
        except Exception as e:
            return {"error": _log_error("branch_summarize", e)}

    def branch_main_conversation(self, idx: int) -> dict:
        """Chamado pelo popup pra iniciar um branch no main window.
        Aciona o método branchFromMessage(idx) do Alpine no main e sai do modo
        compact pra usuário ver a nova conversa."""
        try:
            if self._window is None:
                return {"error": "Main window not set"}
            idx_int = int(idx)
            code = (
                f"(async () => {{ "
                f"  const a = Alpine.$data(document.body); "
                f"  if (a && typeof a.branchFromMessage === 'function') {{ "
                f"    await a.branchFromMessage({idx_int}); "
                f"    if (a.compactMode && typeof a.exitCompact === 'function') {{ "
                f"      await a.exitCompact(); "
                f"    }} "
                f"  }} "
                f"}})();"
            )
            def run():
                try:
                    self._window.evaluate_js(code)
                except Exception as e:
                    _log_error("branch_main_conversation_eval", e)
            threading.Thread(target=run, daemon=True).start()
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("branch_main_conversation", e)}

    # ============ Streaming chat (token-by-token via evaluate_js) ============

    def send_text_streaming(self, text: str, stream_id: str) -> dict:
        """Inicia uma chamada de chat streaming. Retorna imediatamente;
        tokens chegam via window.ghostStreamChunk(stream_id, chunk_text)
        e window.ghostStreamDone(stream_id, full_text_or_error)."""
        try:
            from .config import get_openai_key, get_openai_model

            key = get_openai_key()
            if not key:
                self._stream_emit_done(stream_id, error="OpenAI API key não configurada")
                return {"ok": True, "started": False}

            model = get_openai_model()
            threading.Thread(
                target=self._stream_worker,
                args=(stream_id, key, model, text),
                daemon=True,
            ).start()
            return {"ok": True, "started": True}
        except Exception as e:
            self._stream_emit_done(stream_id, error=str(e))
            return {"error": _log_error("send_text_streaming", e)}

    def _stream_worker(self, stream_id: str, key: str, model: str, text: str):
        try:
            from openai import OpenAI

            from .gpt_client import BASE_PERSONA, SCREEN_CONTEXT_ADDENDUM, _has_image

            # Adiciona contexto watch/meeting se relevante (mesma lógica do send_text)
            user_content = text
            watched_thumb = None
            try:
                if self._watch_running and self._watch_image is not None:
                    from .capture import image_to_base64
                    with self._watch_lock:
                        img = self._watch_image
                    if img is not None:
                        b64 = image_to_base64(img)
                        user_content = [
                            {"type": "text", "text": text},
                            {"type": "image_url",
                             "image_url": {"url": f"data:image/png;base64,{b64}", "detail": "high"}},
                        ]
                        watched_thumb = f"data:image/png;base64,{image_to_base64(img, max_dim=320)}"
            except Exception as e:
                print(f"[stream] watch capture skip: {e}", flush=True)

            # Adiciona à history e usa o contexto acumulado (fix: sem isso,
            # cada request era uma conversa nova sem memória)
            user_msg = {"role": "user", "content": user_content}
            self._history.append(user_msg)
            history_msgs = self._history[-MAX_HISTORY:]

            # System prompt condicional: só inclui addendum de screen se houver
            # imagem em alguma msg do contexto
            has_img = _has_image(history_msgs)
            system_content = BASE_PERSONA + (SCREEN_CONTEXT_ADDENDUM if has_img else "")

            client = OpenAI(api_key=key, timeout=60.0)
            stream = client.chat.completions.create(
                model=model,
                messages=[{"role": "system", "content": system_content}] + history_msgs,
                max_tokens=2000,
                stream=True,
            )
            full = []
            for event in stream:
                try:
                    delta = event.choices[0].delta.content if event.choices else None
                    if delta:
                        full.append(delta)
                        self._stream_emit_chunk(stream_id, delta)
                except Exception as e:
                    print(f"[stream] chunk skip: {e}", flush=True)
            text_full = "".join(full)
            # Persiste a resposta no history pra próxima chamada ter contexto
            self._history.append({"role": "assistant", "content": text_full})
            self._stream_emit_done(stream_id, text=text_full, watched_thumb=watched_thumb)
        except Exception as e:
            err = str(e)
            print(f"[stream] worker error: {err}", flush=True)
            # Remove user msg da history se falhou antes da resposta
            if self._history and self._history[-1].get("role") == "user":
                self._history.pop()
            self._stream_emit_done(stream_id, error=err)

    def _stream_emit_chunk(self, stream_id: str, chunk: str):
        try:
            if self._window:
                payload = json.dumps({"id": stream_id, "chunk": chunk})
                self._window.evaluate_js(f"window.ghostStreamChunk && window.ghostStreamChunk({payload})")
        except Exception:
            pass

    def _stream_emit_done(self, stream_id: str, text: str = "", error: str = "",
                          watched_thumb: str | None = None):
        try:
            if self._window:
                payload = json.dumps({
                    "id": stream_id,
                    "text": text,
                    "error": error,
                    "watched_thumb": watched_thumb,
                })
                self._window.evaluate_js(f"window.ghostStreamDone && window.ghostStreamDone({payload})")
        except Exception:
            pass

    # ============ Live Q&A durante reunião ============

    def meeting_live_question(self, question: str) -> dict:
        """Responde uma pergunta usando o transcript live capturado até agora."""
        try:
            from .config import get_openai_key, get_openai_model

            if not self._meeting.is_running():
                return {"error": "Nenhuma reunião em andamento"}

            segs = list(self._live_transcript or [])
            if not segs:
                return {"error": "Ainda não há transcrição disponível. Aguarde alguns segundos."}

            # Monta texto do transcript
            transcript_text = "\n".join(
                f"[{format_time(s.get('start', 0))}] {s.get('text', '')}" for s in segs
            )
            prompt = (
                "Você recebe a transcrição parcial de uma reunião que ainda está em andamento.\n"
                "Responda a pergunta do usuário baseando-se APENAS no que foi dito até aqui.\n"
                "Se a resposta não pode ser inferida da transcrição, diga isso.\n\n"
                f"TRANSCRIÇÃO ATÉ AGORA:\n{transcript_text}\n\n"
                f"PERGUNTA DO USUÁRIO: {question}"
            )

            key = get_openai_key()
            if not key:
                return {"error": "OpenAI API key não configurada"}

            from openai import OpenAI
            client = OpenAI(api_key=key, timeout=60.0)
            resp = client.chat.completions.create(
                model=get_openai_model(),
                messages=[
                    {"role": "system", "content": "Você é um assistente que ajuda durante reuniões ao vivo."},
                    {"role": "user", "content": prompt},
                ],
                max_tokens=1500,
            )
            return {"ok": True, "text": resp.choices[0].message.content or ""}
        except Exception as e:
            return {"error": _log_error("meeting_live_question", e)}

    # ============ Detecção de informação sensível ============

    def scan_sensitive(self, text: str) -> dict:
        """Detecta padrões de info sensível em texto (CPF/CNPJ/cartão/email/telefone).
        Retorna lista de tipos encontrados pra avisar usuário antes de enviar."""
        try:
            from .sensitive import scan
            return {"ok": True, "sensitive": scan(text)}
        except Exception as e:
            return {"error": _log_error("scan_sensitive", e)}

    # ============ Drag-and-drop: parse file content ============

    def parse_dropped_file(self, filename: str, mime: str, data_b64: str) -> dict:
        """Recebe arquivo arrastado. Para texto/markdown retorna conteúdo,
        pra imagem retorna data URL pra GPT analisar, pra PDF tenta extrair texto."""
        try:
            import base64
            raw = base64.b64decode(data_b64)
            lower = (filename or "").lower()

            # Imagem → retorna base64 pra GPT Vision
            if mime.startswith("image/") or any(lower.endswith(ext) for ext in [".png", ".jpg", ".jpeg", ".gif", ".webp"]):
                return {
                    "ok": True,
                    "kind": "image",
                    "filename": filename,
                    "data_url": f"data:{mime or 'image/png'};base64,{data_b64}",
                }

            # Texto puro
            if mime.startswith("text/") or any(lower.endswith(ext) for ext in [".txt", ".md", ".py", ".js", ".ts", ".html", ".css", ".json", ".log", ".csv"]):
                try:
                    content = raw.decode("utf-8")
                except UnicodeDecodeError:
                    content = raw.decode("latin-1", errors="replace")
                return {
                    "ok": True,
                    "kind": "text",
                    "filename": filename,
                    "content": content[:20000],  # cap em 20k chars
                }

            # PDF → tenta extrair texto com pypdf se disponível
            if lower.endswith(".pdf") or mime == "application/pdf":
                try:
                    import io

                    import pypdf
                    reader = pypdf.PdfReader(io.BytesIO(raw))
                    pages = []
                    for i, page in enumerate(reader.pages[:30]):  # max 30 páginas
                        pages.append(page.extract_text() or "")
                    content = "\n\n".join(pages).strip()
                    return {
                        "ok": True,
                        "kind": "text",
                        "filename": filename,
                        "content": content[:30000],
                        "note": f"PDF com {len(reader.pages)} páginas (texto extraído).",
                    }
                except ImportError:
                    return {"error": "PDF requer pypdf — pip install pypdf"}
                except Exception as e:
                    return {"error": f"Falha extraindo PDF: {e}"}

            return {"error": f"Formato não suportado: {mime or filename}. Tente imagem, texto ou PDF."}
        except Exception as e:
            return {"error": _log_error("parse_dropped_file", e)}

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

    def set_capture_visibility(self, visible: bool) -> dict:
        """Toggle whether Ghost is visible in screen captures / screen sharing.
        Does NOT affect NOACTIVATE (focus behavior stays the same).
        """
        try:
            from .win_focus import hide_from_capture
            hide_enabled = not visible
            ok = False
            if self._hwnd:
                ok = hide_from_capture(self._hwnd, hide_enabled, force_redraw=True)
            if self._response_hwnd:
                hide_from_capture(self._response_hwnd, hide_enabled, force_redraw=True)
            return {"ok": bool(ok), "visible": visible}
        except Exception as e:
            return {"error": _log_error("set_capture_visibility", e)}

    # ---------- Config ----------

    def get_presets(self) -> list[str]:
        return list(PRESETS.keys())

    def get_monitors(self) -> list[dict]:
        return [{
            "index": m["index"],
            "label": f"Monitor {m['index']} ({m['width']}×{m['height']})",
            "width": m["width"],
            "height": m["height"],
        } for m in self._monitors]

    def list_windows(self) -> list[dict]:
        """Enumerate visible top-level windows (excluding Ghost itself)."""
        try:
            import win32gui
            import win32process
        except Exception:
            return []

        my_pid = os.getpid()
        ghost_hwnd = self._hwnd
        windows: list[dict] = []

        def callback(hwnd, _):
            try:
                if not win32gui.IsWindowVisible(hwnd):
                    return True
                if hwnd == ghost_hwnd:
                    return True
                title = win32gui.GetWindowText(hwnd)
                if not title or len(title) < 2:
                    return True
                rect = win32gui.GetWindowRect(hwnd)
                w = rect[2] - rect[0]
                h = rect[3] - rect[1]
                if w < 100 or h < 100:
                    return True
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                if pid == my_pid:
                    return True
                windows.append({
                    "hwnd": hwnd,
                    "title": title,
                    "width": w,
                    "height": h,
                })
            except Exception:
                pass
            return True

        win32gui.EnumWindows(callback, None)
        windows.sort(key=lambda w: w["title"].lower())
        return windows

    def clear_history(self):
        self._history.clear()
        self._last_image = None
        return {"ok": True}

    def branch_reset_history(self, summary: str) -> dict:
        """Chamado após um branch: limpa o histórico do servidor e injeta o
        resumo como mensagem de sistema, pra que as próximas chamadas de chat
        tenham o contexto condensado."""
        try:
            self._history.clear()
            self._last_image = None
            summary = (summary or "").strip()
            if summary:
                self._history.append({
                    "role": "system",
                    "content": (
                        "Contexto de uma conversa anterior (resumo). "
                        "Use isto como referência ao responder:\n\n"
                        f"{summary}"
                    ),
                })
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("branch_reset_history", e)}

    # ---------- Watch mode ----------

    def get_watch_status(self) -> dict:
        return {
            "enabled": self._watch_running,
            "interval": self._watch_interval,
            "has_image": self._watch_image is not None,
        }

    def toggle_watch(self, enabled: bool, interval: float = 3.0) -> dict:
        try:
            self._watch_interval = max(1.0, float(interval))
            if enabled and not self._watch_running:
                self._watch_running = True
                self._watch_thread = threading.Thread(target=self._watch_loop, daemon=True)
                self._watch_thread.start()
            elif not enabled and self._watch_running:
                self._watch_running = False
                with self._watch_lock:
                    self._watch_image = None
            return self.get_watch_status()
        except Exception as e:
            return {"error": _log_error("toggle_watch", e)}

    def _current_monitor(self) -> dict | None:
        """Return the monitor dict containing the Ghost window's center.
        Includes a 'work' tuple (left, top, right, bottom) that excludes the taskbar.
        """
        if not self._hwnd:
            return None
        try:
            import win32api
            import win32con
            import win32gui
            rect = win32gui.GetWindowRect(self._hwnd)
            cx = (rect[0] + rect[2]) // 2
            cy = (rect[1] + rect[3]) // 2
            for m in self._monitors:
                if (m["left"] <= cx < m["left"] + m["width"] and
                        m["top"] <= cy < m["top"] + m["height"]):
                    # Augment with work area (excludes taskbar)
                    try:
                        hmon = win32api.MonitorFromPoint(
                            (cx, cy), win32con.MONITOR_DEFAULTTONEAREST
                        )
                        info = win32api.GetMonitorInfo(hmon)
                        work = info.get("Work")  # (left, top, right, bottom)
                        if work:
                            return {**m, "work": work}
                    except Exception:
                        pass
                    return m
        except Exception:
            pass
        return None

    def _watch_loop(self):
        while self._watch_running:
            try:
                monitor = self._current_monitor()
                if monitor:
                    img = capture_monitor(monitor)
                else:
                    img = capture_fullscreen()
                with self._watch_lock:
                    self._watch_image = img
            except Exception as e:
                _log_error("watch_loop", e)
            slept = 0.0
            while slept < self._watch_interval and self._watch_running:
                time.sleep(0.2)
                slept += 0.2

    def get_watch_thumbnail(self) -> dict:
        with self._watch_lock:
            img = self._watch_image
        if img is None:
            return {"thumbnail": None}
        return {"thumbnail": image_to_data_url(img, max_dim=480)}

    # ---------- Meeting mode ----------

    def get_meeting_status(self) -> dict:
        return {
            "running": self._meeting.is_running(),
            "processing": self._meeting_processing,
            "elapsed": self._meeting.elapsed(),
            "elapsed_formatted": format_time(self._meeting.elapsed()),
            "status_text": self._meeting_last_status,
        }

    def start_meeting(self, target_kind: str = "monitor", target_id: int | None = None) -> dict:
        """target_kind: 'monitor' uses target_id as monitor index; 'window' uses it as HWND; 'auto' uses current monitor."""
        try:
            if self._meeting.is_running():
                return {"error": "Reunião já em andamento"}
            if self._meeting_processing:
                return {"error": "Processamento anterior ainda em andamento"}

            monitor = None
            window_hwnd = 0

            if target_kind == "window" and target_id:
                window_hwnd = int(target_id)
            elif target_kind == "monitor" and target_id is not None:
                monitor = next((m for m in self._monitors if m["index"] == target_id), None)
            else:
                monitor = self._current_monitor() or (self._monitors[0] if self._monitors else None)

            self._meeting.start(monitor=monitor, window_hwnd=window_hwnd)
            self._meeting_started_at = datetime.now()
            self._meeting_last_status = "Gravando..."

            # Kick off live transcription for Q&A during the meeting
            self._live_transcript.clear()
            self._last_transcribed_sec = 0.0
            self._live_transcribe_thread = threading.Thread(
                target=self._live_transcribe_loop, daemon=True
            )
            self._live_transcribe_thread.start()

            return self.get_meeting_status()
        except Exception as e:
            return {"error": _log_error("start_meeting", e)}

    def stop_meeting(self) -> dict:
        try:
            if not self._meeting.is_running():
                return {"error": "Nenhuma reunião em andamento"}

            self._meeting.stop()
            self._meeting_processing = True
            self._meeting_last_status = "Processando gravação..."

            t = threading.Thread(target=self._process_meeting_async, daemon=True)
            t.start()
            return {"processing": True}
        except Exception as e:
            self._meeting_processing = False
            return {"error": _log_error("stop_meeting", e)}

    def _set_meeting_status(self, text: str):
        self._meeting_last_status = text

    def _live_transcribe_loop(self):
        """Transcribe ~60s chunks of the running meeting for live Q&A."""
        import tempfile
        from pathlib import Path as _P
        CHUNK = 60.0
        SAFETY = 3.0
        while self._meeting.is_running():
            try:
                elapsed = self._meeting.elapsed()
                available_end = elapsed - SAFETY
                if available_end - self._last_transcribed_sec < CHUNK:
                    time.sleep(3.0)
                    continue

                start_s = self._last_transcribed_sec
                end_s = self._last_transcribed_sec + CHUNK
                tmp = _P(tempfile.gettempdir()) / f"ghost_live_{int(time.time() * 1000)}.wav"
                path = self._meeting.export_audio_range(start_s, end_s, tmp)
                if path is None:
                    time.sleep(3.0)
                    continue

                try:
                    result = transcribe_audio_verbose(path)
                    for seg in result["segments"]:
                        self._live_transcript.append({
                            "start": seg["start"] + start_s,
                            "end": seg["end"] + start_s,
                            "text": seg["text"],
                        })
                    self._last_transcribed_sec = end_s
                except Exception as e:
                    _log_error("live_transcribe_chunk", e)
                finally:
                    try:
                        tmp.unlink()
                    except Exception:
                        pass
            except Exception as e:
                _log_error("live_transcribe_loop", e)
                time.sleep(5.0)

    def get_live_transcript(self) -> dict:
        """Return the current live transcript snapshot."""
        return {
            "running": self._meeting.is_running(),
            "segments_count": len(self._live_transcript),
            "transcribed_up_to": format_time(self._last_transcribed_sec),
        }

    def _process_meeting_async(self):
        result = {}
        try:
            self._set_meeting_status("Salvando áudio + vídeo...")
            started = self._meeting_started_at or datetime.now()
            duration = self._meeting.elapsed()
            stamp = started.strftime("%Y-%m-%d-%H%M")
            # Per-meeting subfolder
            meeting_dir = meetings_dir() / f"reuniao-{stamp}"
            meeting_dir.mkdir(parents=True, exist_ok=True)

            audio_path = meeting_dir / "reuniao.wav"
            self._meeting.export_audio(audio_path)

            # Mux video + audio into MP4
            final_video_path = meeting_dir / "reuniao.mp4"
            video_src = self._meeting.video_tmp_path
            muxed = self._meeting.export_video_with_audio(
                video_src, audio_path, final_video_path
            ) if video_src else None

            self._set_meeting_status("Dividindo em chunks...")
            chunks = self._meeting.split_audio_chunks(audio_path, chunk_minutes=10)

            self._set_meeting_status("Transcrevendo via Whisper...")
            trans = transcribe_chunks_verbose(chunks, chunk_seconds=600,
                                              status_cb=self._set_meeting_status)

            self._set_meeting_status("Gerando resumo...")
            screenshots = self._meeting.get_screenshots()
            summary = summarize_meeting(
                trans["segments"], screenshots, status_cb=self._set_meeting_status
            )

            self._set_meeting_status("Escrevendo documento...")
            doc_path = write_markdown_doc(
                title=f"Reunião — {started.strftime('%d/%m/%Y %H:%M')}",
                started_at=started,
                duration_sec=duration,
                segments=trans["segments"],
                raw_transcript=trans["full_text"],
                summary=summary,
                audio_path=audio_path if not muxed else None,
                video_path=muxed,
                out_dir=meeting_dir,
            )

            # Cleanup: remove WAV and temp silent video when mux succeeded
            try:
                if video_src and video_src.exists() and muxed and muxed.exists():
                    video_src.unlink()
                if muxed and audio_path.exists():
                    audio_path.unlink()
            except Exception:
                pass

            self._set_meeting_status(f"Concluído: {meeting_dir.name}/")
            result = {
                "ok": True,
                "doc_path": str(doc_path),
                "folder_path": str(meeting_dir),
                "audio_path": str(audio_path) if audio_path.exists() else None,
                "video_path": str(muxed) if muxed else None,
                "duration": format_time(duration),
                "summary_bullets": summary.get("executivo", []) or [],
            }
        except Exception as e:
            err = _log_error("process_meeting", e)
            self._set_meeting_status(f"Erro: {err}")
            result = {"error": err}
        finally:
            self._meeting_processing = False
            self._meeting_result = result

    def consume_meeting_result(self) -> dict:
        r = getattr(self, "_meeting_result", None)
        if r is None:
            return {"pending": True}
        self._meeting_result = None
        return r

    def open_meetings_folder(self) -> dict:
        try:
            d = meetings_dir()
            os.startfile(str(d))
            return {"ok": True, "path": str(d)}
        except Exception as e:
            return {"error": _log_error("open_meetings_folder", e)}

    # ---------- Window ----------

    def minimize(self):
        """With WS_EX_TOOLWINDOW applied, there's no taskbar icon to minimize to.
        Instead, hide the window. User restores via Ctrl+Shift+G hotkey.
        """
        try:
            if self._hwnd:
                hide_window(self._hwnd)
        except Exception as e:
            _log_error("minimize", e)

    def hide_app(self) -> dict:
        """Hide main window (+ popup) like Ctrl+Shift+G.
        Usuário pode restaurar com o atalho global."""
        try:
            if self._hwnd:
                hide_window(self._hwnd)
            if self._response_hwnd:
                try:
                    hide_window(self._response_hwnd)
                except Exception:
                    pass
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("hide_app", e)}

    def close_app(self) -> dict:
        """Encerra o app completamente: destrói todas as janelas +
        força saída do processo (garante que msedgewebview2.exe também morra)."""
        try:
            # 1. Destrói janelas pywebview (main + response popup)
            import webview
            for w in list(webview.windows):
                try: w.destroy()
                except Exception: pass

            # 2. Hard-kill processo principal — isso mata threads de áudio,
            # watch loop, hotkey listener, e o OS encerra child processes
            # (WebView2 helper, etc). Usa Timer pra dar 200ms pro webview
            # fechar graciosamente antes.
            def _kill():
                import os
                os._exit(0)
            import threading
            threading.Timer(0.2, _kill).start()
            return {"ok": True}
        except Exception as e:
            _log_error("close_app", e)
            # Fallback brutal
            import os
            os._exit(1)

    def minimize_to_edge(self) -> dict:
        """Shrink window to a 56×56 icon docked on the right edge of the current monitor."""
        try:
            import win32gui
            if not self._hwnd:
                return {"error": "No HWND"}
            rect = win32gui.GetWindowRect(self._hwnd)
            x, y, r, b = rect
            self._saved_rect = (x, y, r - x, b - y)

            icon_size = 56
            monitor = self._current_monitor()
            if monitor is None and self._monitors:
                monitor = self._monitors[0]
            if monitor is None:
                monitor = {"left": 0, "top": 0, "width": 1920, "height": 1080}

            edge_x = monitor["left"] + monitor["width"] - icon_size
            edge_y = monitor["top"] + (monitor["height"] // 2) - (icon_size // 2)

            SWP_NOZORDER_LOCAL = 0x0004
            SWP_NOACTIVATE_LOCAL = 0x0010
            win32gui.SetWindowPos(
                self._hwnd, 0, edge_x, edge_y, icon_size, icon_size,
                SWP_NOZORDER_LOCAL | SWP_NOACTIVATE_LOCAL
            )
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("minimize_to_edge", e)}

    def start_kb_capture(self) -> dict:
        """Begin global keyboard capture. NOACTIVATE is already permanent."""
        try:
            print("[kb] start_kb_capture called", flush=True)
            if self._kb_listener is not None:
                print("[kb] already running, skipping", flush=True)
                return {"ok": True, "already_running": True}
            from pynput import keyboard as kb

            def forward(payload: dict):
                try:
                    if self._window is not None:
                        code = f"window.ghostKey({json.dumps(payload)})"
                        print(f"[kb] forward → {payload}", flush=True)
                        self._window.evaluate_js(code)
                    else:
                        print("[kb] self._window is None!", flush=True)
                except Exception as e:
                    print(f"[kb] forward error: {e}", flush=True)

            def on_press(key):
                try:
                    if isinstance(key, kb.KeyCode) and key.char:
                        forward({"type": "char", "value": key.char})
                    elif key == kb.Key.space:
                        forward({"type": "char", "value": " "})
                    elif key == kb.Key.enter:
                        forward({"type": "enter"})
                    elif key == kb.Key.backspace:
                        forward({"type": "backspace"})
                    elif key == kb.Key.esc:
                        forward({"type": "esc"})
                    elif key == kb.Key.tab:
                        forward({"type": "char", "value": "\t"})
                except Exception as e:
                    print(f"[kb] on_press error: {e}", flush=True)

            self._kb_listener = kb.Listener(on_press=on_press)
            self._kb_listener.start()
            print("[kb] listener started", flush=True)
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("start_kb_capture", e)}

    def stop_kb_capture(self) -> dict:
        try:
            if self._kb_listener is not None:
                self._kb_listener.stop()
                self._kb_listener = None
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("stop_kb_capture", e)}

    def enter_compact_bar(self) -> dict:
        """Shrink window to a composer-only bar at bottom-right. Responses appear
        in a separate floating popup at the top-right."""
        try:
            import win32gui
            if not self._hwnd:
                return {"error": "No HWND"}
            rect = win32gui.GetWindowRect(self._hwnd)
            self._saved_rect = (rect[0], rect[1], rect[2] - rect[0], rect[3] - rect[1])

            bar_w = 820
            bar_h = 200
            monitor = self._current_monitor()
            if monitor is None and self._monitors:
                monitor = self._monitors[0]
            if monitor is None:
                monitor = {"left": 0, "top": 0, "width": 1920, "height": 1080}

            # Use WORK AREA (excludes taskbar). Query the HMONITOR of the center
            # of the current Ghost window, no matter what.
            work_left = monitor["left"]
            work_top = monitor["top"]
            work_right = monitor["left"] + monitor["width"]
            work_bottom = monitor["top"] + monitor["height"]
            try:
                import win32api
                import win32con
                import win32gui
                rect = win32gui.GetWindowRect(self._hwnd) if self._hwnd else None
                if rect:
                    pt = ((rect[0] + rect[2]) // 2, (rect[1] + rect[3]) // 2)
                else:
                    pt = (monitor["left"] + 10, monitor["top"] + 10)
                hmon = win32api.MonitorFromPoint(pt, win32con.MONITOR_DEFAULTTONEAREST)
                info = win32api.GetMonitorInfo(hmon)
                work = info.get("Work")
                if work:
                    work_left, work_top, work_right, work_bottom = work
                print(f"[compact] monitor full={monitor['left']},{monitor['top']},{monitor['width']}x{monitor['height']} "
                      f"work={work_left},{work_top} to {work_right},{work_bottom}", flush=True)
            except Exception as e:
                print(f"[compact] work area fallback: {e}", flush=True)

            # Bottom-right corner of the work area with small margin
            x = work_right - bar_w - 12
            y = work_bottom - bar_h - 12
            print(f"[compact] bar at {x},{y} size {bar_w}x{bar_h}", flush=True)

            SWP_NOZORDER_LOCAL = 0x0004
            SWP_NOACTIVATE_LOCAL = 0x0010
            win32gui.SetWindowPos(
                self._hwnd, 0, x, y, bar_w, bar_h,
                SWP_NOZORDER_LOCAL | SWP_NOACTIVATE_LOCAL
            )
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("enter_compact_bar", e)}

    def exit_compact_bar(self) -> dict:
        """Restore full-size window from compact bar mode. Also closes any response popup."""
        try:
            import win32gui
            self.hide_response_popup()
            if not self._hwnd:
                return {"error": "No HWND"}
            if self._saved_rect:
                x, y, w, h = self._saved_rect
            else:
                monitor = self._current_monitor() or (self._monitors[0] if self._monitors else None)
                if monitor:
                    w, h = 580, 720
                    x = monitor["left"] + (monitor["width"] - w) // 2
                    y = monitor["top"] + (monitor["height"] - h) // 2
                else:
                    x, y, w, h = 100, 100, 580, 720
            SWP_NOZORDER_LOCAL = 0x0004
            SWP_NOACTIVATE_LOCAL = 0x0010
            win32gui.SetWindowPos(
                self._hwnd, 0, x, y, w, h,
                SWP_NOZORDER_LOCAL | SWP_NOACTIVATE_LOCAL
            )
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("exit_compact_bar", e)}

    def show_response_popup(self, messages: list | None = None, text: str = "", loading: bool = False) -> dict:
        """Position the response popup at right side of the monitor (50% height), show it,
        and update with the current conversation messages."""
        try:
            if self._response_window is None:
                return {"error": "Response window not pre-created"}

            # Position at right of current monitor, taking 50% of its height.
            # Width matches the compact bar (820) para alinhar visualmente.
            if self._response_hwnd:
                import win32gui
                monitor = self._current_monitor() or (self._monitors[0] if self._monitors else None)
                if monitor is None:
                    monitor = {"left": 0, "top": 0, "width": 1920, "height": 1080}
                w = 820
                h = monitor["height"] // 2
                x = monitor["left"] + monitor["width"] - w - 12
                y = monitor["top"] + 48
                SWP_NOZORDER_LOCAL = 0x0004
                SWP_NOACTIVATE_LOCAL = 0x0010
                try:
                    win32gui.SetWindowPos(
                        self._response_hwnd, 0, x, y, w, h,
                        SWP_NOZORDER_LOCAL | SWP_NOACTIVATE_LOCAL,
                    )
                    import win32con
                    win32gui.ShowWindow(self._response_hwnd, win32con.SW_SHOWNOACTIVATE)
                except Exception as e:
                    _log_error("popup_position", e)
            else:
                try:
                    self._response_window.show()
                except Exception:
                    pass

            # Update messages
            if messages is None:
                messages = []

            def update():
                import time as _t
                _t.sleep(0.1)
                try:
                    if self._response_window is not None:
                        self._response_window.evaluate_js(
                            f"window.setMessages({json.dumps(messages)})"
                        )
                except Exception as e:
                    _log_error("popup_update", e)

            threading.Thread(target=update, daemon=True).start()
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("show_response_popup", e)}

    def update_response_popup(self, messages: list) -> dict:
        """Just update the messages in the popup without repositioning."""
        try:
            if self._response_window is None:
                return {"ok": False}
            def update():
                try:
                    self._response_window.evaluate_js(
                        f"window.setMessages({json.dumps(messages or [])})"
                    )
                except Exception as e:
                    _log_error("popup_update_inline", e)
            threading.Thread(target=update, daemon=True).start()
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("update_response_popup", e)}

    def hide_response_popup(self) -> dict:
        """Hide the response popup AND move it off-screen so its hidden rect
        doesn't render as a black rectangle under WDA_EXCLUDEFROMCAPTURE."""
        try:
            if self._response_hwnd:
                import win32con
                import win32gui
                win32gui.ShowWindow(self._response_hwnd, win32con.SW_HIDE)
                # Park off-screen to avoid DWM protecting any visible rect
                SWP_NOSIZE = 0x0001
                SWP_NOZORDER = 0x0004
                SWP_NOACTIVATE = 0x0010
                win32gui.SetWindowPos(
                    self._response_hwnd, 0, -10000, -10000, 0, 0,
                    SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE,
                )
            elif self._response_window is not None:
                try:
                    self._response_window.hide()
                except Exception:
                    pass
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("hide_response_popup", e)}

    def restore_from_edge(self) -> dict:
        """Restore window to its saved rect before docking."""
        try:
            import win32gui
            if not self._hwnd:
                return {"error": "No HWND"}
            if self._saved_rect:
                x, y, w, h = self._saved_rect
            else:
                monitor = self._current_monitor() or (self._monitors[0] if self._monitors else None)
                if monitor:
                    w, h = 580, 720
                    x = monitor["left"] + (monitor["width"] - w) // 2
                    y = monitor["top"] + (monitor["height"] - h) // 2
                else:
                    x, y, w, h = 100, 100, 580, 720

            SWP_NOZORDER_LOCAL = 0x0004
            SWP_NOACTIVATE_LOCAL = 0x0010
            win32gui.SetWindowPos(
                self._hwnd, 0, x, y, w, h,
                SWP_NOZORDER_LOCAL | SWP_NOACTIVATE_LOCAL
            )
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("restore_from_edge", e)}

    def force_focus(self) -> dict:
        """Explicitly bring Ghost to foreground (bypassing NOACTIVATE).
        Called when user clicks the text input so they can type directly.
        """
        try:
            if not self._hwnd:
                return {"error": "No HWND"}
            ok = force_foreground(self._hwnd)
            return {"ok": bool(ok)}
        except Exception as e:
            return {"error": _log_error("force_focus", e)}

    def start_window_drag(self):
        try:
            if not self._hwnd:
                # Try on-demand enumeration as a fallback
                try:
                    import win32gui
                    import win32process
                    pid = os.getpid()
                    found = [0]

                    def cb(hwnd, _):
                        try:
                            if not win32gui.IsWindowVisible(hwnd):
                                return True
                            _, wpid = win32process.GetWindowThreadProcessId(hwnd)
                            if wpid == pid:
                                title = win32gui.GetWindowText(hwnd)
                                if title and found[0] == 0:
                                    found[0] = hwnd
                        except Exception:
                            pass
                        return True

                    win32gui.EnumWindows(cb, None)
                    if found[0]:
                        self._hwnd = found[0]
                except Exception:
                    pass

            if not self._hwnd:
                return {"error": "No HWND"}
            t = threading.Thread(target=drag_window_loop, args=(self._hwnd,), daemon=True)
            t.start()
            return {"ok": True}
        except Exception as e:
            return {"error": _log_error("start_window_drag", e)}

    # ---------- Focus management ----------

    def enable_typing(self):
        """No-op. NOACTIVATE stays on permanently — no toggling (causes crashes)."""
        return {"ok": True}

    def restore_focus(self):
        """No-op. NOACTIVATE stays on permanently."""
        return {"ok": True}

    # ---------- Capture ----------

    def capture_fullscreen(self) -> dict:
        """Ghost is invisible to captures (WDA_EXCLUDEFROMCAPTURE), so no hide/show needed.
        Avoids window.show() which internally activates the window on WinForms.
        """
        try:
            img = capture_fullscreen()
            self._last_image = img
            thumb = image_to_data_url(img, max_dim=480)
            return {"thumbnail": thumb, "width": img.width, "height": img.height}
        except Exception as e:
            return {"error": _log_error("capture_fullscreen", e)}

    def capture_area(self) -> dict:
        """Launch region selector in subprocess (tkinter needs main thread).
        Doesn't hide Ghost — it's already invisible to captures via WDA flag.
        """
        try:
            cmd = [sys.executable, "-m", "src.region_selector_cli"]
            result = subprocess.run(
                cmd, cwd=str(ROOT), capture_output=True, text=True, timeout=120
            )
            if result.returncode != 0 or not result.stdout.strip():
                return {"cancelled": True}

            region = json.loads(result.stdout.strip())
            img = capture_region(region["x"], region["y"], region["w"], region["h"])
            self._last_image = img
            thumb = image_to_data_url(img, max_dim=480)
            return {"thumbnail": thumb, "width": img.width, "height": img.height}
        except Exception as e:
            return {"error": _log_error("capture_area", e)}

    def capture_with_scroll(self, monitor_index: int, max_scrolls: int) -> dict:
        """Scroll capture minimizes Ghost via Win32 (doesn't activate on restore)."""
        monitor = next((m for m in self._monitors if m["index"] == monitor_index), None)
        if not monitor:
            return {"error": "Monitor não encontrado"}

        import win32con
        import win32gui as _w
        try:
            # Minimize via Win32 without activation
            if self._hwnd:
                _w.ShowWindow(self._hwnd, win32con.SW_MINIMIZE)
            time.sleep(0.5)

            shots = scroll_and_capture(monitor, max_scrolls=max_scrolls)
            stitched = stitch_vertical(shots)
            if stitched is None:
                return {"error": "Nenhuma captura feita"}
            self._last_image = stitched
            thumb = image_to_data_url(stitched, max_dim=480)
            return {"thumbnail": thumb, "width": stitched.width, "height": stitched.height, "pages": len(shots)}
        except Exception as e:
            return {"error": _log_error("capture_with_scroll", e)}
        finally:
            try:
                # Restore without activating: SW_SHOWNOACTIVATE
                if self._hwnd:
                    _w.ShowWindow(self._hwnd, win32con.SW_SHOWNOACTIVATE)
            except Exception:
                pass

    # ---------- GPT ----------

    def analyze_last_capture(self, preset_name: str, extra_text: str = "") -> dict:
        try:
            if self._last_image is None:
                return {"error": "Nenhuma captura disponível"}
            base = PRESETS.get(preset_name)
            if not base:
                return {"error": f"Preset '{preset_name}' não encontrado"}
            prompt = base
            if extra_text:
                prompt += f"\n\nMensagem do usuário: {extra_text}"

            image_b64 = image_to_base64(self._last_image)
            msg = build_user_message(prompt, image_b64)
            self._history.append(msg)
            messages = self._history[-MAX_HISTORY:]
            response = chat_completion(messages)
            self._history.append({"role": "assistant", "content": response})
            return {"text": response}
        except Exception as e:
            if self._history and self._history[-1].get("role") == "user":
                self._history.pop()
            return {"error": _log_error("analyze_last_capture", e)}

    def send_text(self, text: str, image_data_url: str = "") -> dict:
        try:
            text = (text or "").strip()
            if not text:
                return {"error": "Texto vazio"}

            thumbnail = None
            image_b64 = None

            # Imagem anexada via drag-and-drop (prioridade sobre watch/meeting screenshot)
            if image_data_url and isinstance(image_data_url, str) and image_data_url.startswith("data:image"):
                try:
                    # Extrai base64 puro do data URL
                    _, b64part = image_data_url.split(",", 1)
                    image_b64 = b64part
                    thumbnail = image_data_url  # já é data URL pronta
                except Exception as e:
                    print(f"[send_text] dropped image parse error: {e}", flush=True)
            meeting_context = ""
            meeting_active = self._meeting.is_running()

            # If a meeting is running, prepend the live transcript as context
            if meeting_active and self._live_transcript:
                transcript_lines = [
                    f"[{format_time(s['start'])}] {s['text']}"
                    for s in self._live_transcript
                ]
                transcript_text = "\n".join(transcript_lines)
                meeting_context = (
                    "[REUNIÃO EM ANDAMENTO]\n"
                    "Abaixo está a transcrição ao vivo da reunião atual. "
                    "Use-a como contexto para responder a pergunta do usuário.\n"
                    "Se a informação pedida ainda não apareceu na transcrição, diga isso claramente "
                    f"(última transcrição em {format_time(self._last_transcribed_sec)}).\n\n"
                    f"TRANSCRIÇÃO ATÉ AGORA:\n{transcript_text}\n\n"
                    "---\n\nPERGUNTA DO USUÁRIO:\n"
                )

            # Attach the latest screen image (watch mode OR last meeting screenshot)
            # — só se ainda não temos imagem de drag-drop
            if image_b64 is None:
                img = None
                if self._watch_running:
                    with self._watch_lock:
                        img = self._watch_image
                if img is None and meeting_active:
                    shots = self._meeting.get_screenshots()
                    if shots:
                        img = shots[-1][1]

                if img is not None:
                    image_b64 = image_to_base64(img)
                    thumbnail = image_to_data_url(img, max_dim=480)

            full_text = (meeting_context + text) if meeting_context else text
            msg = build_user_message(full_text, image_b64)
            self._history.append(msg)
            messages = self._history[-MAX_HISTORY:]
            response = chat_completion(messages)
            self._history.append({"role": "assistant", "content": response})
            return {
                "text": response,
                "watched_thumb": thumbnail,
                "meeting_context_used": meeting_active and bool(self._live_transcript),
            }
        except Exception as e:
            if self._history and self._history[-1].get("role") == "user":
                self._history.pop()
            return {"error": _log_error("send_text", e)}
