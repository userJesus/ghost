"""Chat streaming + send_text + branch summarize + sensitive scan + drag-drop parse.

These methods were extracted from GhostAPI (src/api.py) to keep api.py
navigable. They remain METHODS of GhostAPI via mixin inheritance — so
`self` is still the GhostAPI instance and every `self._X` state access
continues to work unchanged. No behavioral change vs. the pre-split file.

Do NOT instantiate ChatMixin directly. It exists only as a mixin base
for `class GhostAPI(WindowMixin, CaptureMixin, ChatMixin, MeetingMixin):`.
"""
from __future__ import annotations

# ── Imports migrated from api.py top-level so extracted method bodies
# ── resolve names like `threading`, `os`, `force_foreground`, etc. exactly
# ── as they did when they lived in api.py. Do not trim without checking
# ── every reference in method bodies below first.
import json
import os
import subprocess
import sys
import threading
import time
import traceback
from datetime import datetime
from pathlib import Path

from PIL import Image

from src import history as _history
from src.capture import (
    capture_fullscreen,
    capture_region,
    image_to_base64,
    image_to_data_url,
)
from src.clone import WebCloner, clones_dir
from src.config import PRESETS
from src.gpt_client import build_user_message, chat_completion
from src.meeting import MeetingRecorder, format_time
from src.meeting_processor import (
    meetings_dir,
    summarize_meeting,
    transcribe_audio_verbose,
    transcribe_chunks_verbose,
    write_markdown_doc,
)
from src.scroll_capture import (
    capture_monitor,
    list_monitors,
    scroll_and_capture,
    stitch_vertical,
)
from src.voice import VoiceRecorder
from src.win_focus import (
    drag_window_loop,
    force_foreground,
    hide_window,
)

# Error-logging helper used by every bridge method. Imported from api.py
# so the log format stays uniform across mixins.
# Module-level names from api.py that extracted method bodies reference:
# _log_error (uniform error log format), MAX_HISTORY (chat buffer cap),
# ROOT (bundled-asset root for subprocess), _pid_alive / _snapshot_own_webview2_pids
# (close_app process cleanup). All defined in api.py BEFORE its mixin imports,
# so this import resolves cleanly during api.py's load.
from src.api import MAX_HISTORY, ROOT, _log_error  # noqa: F401



class ChatMixin:
    """Mixin base — injects the following methods onto GhostAPI:
      * send_text
      * send_text_streaming
      * _stream_worker
      * _stream_emit_chunk
      * _stream_emit_done
      * branch_summarize
      * branch_main_conversation
      * scan_sensitive
      * parse_dropped_file
      * clear_history
      * branch_reset_history
      * history_suggest_title
    """

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

    def send_text_streaming(self, text: str, stream_id: str) -> dict:
        """Inicia uma chamada de chat streaming. Retorna imediatamente;
        tokens chegam via window.ghostStreamChunk(stream_id, chunk_text)
        e window.ghostStreamDone(stream_id, full_text_or_error)."""
        try:
            from src.config import get_openai_key, get_openai_model

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

            from src.gpt_client import (
                BASE_PERSONA,
                SCREEN_CONTEXT_ADDENDUM,
                _has_image,
                completion_kwargs,
            )

            # Adiciona contexto watch/meeting se relevante (mesma lógica do send_text)
            user_content = text
            watched_thumb = None
            try:
                if self._watch_running and self._watch_image is not None:
                    from src.capture import image_to_base64
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
                stream=True,
                **completion_kwargs(model, max_tokens=2000),
            )
            # Batch deltas to reduce cross-thread evaluate_js pressure on
            # WebView2's dispatch queue. The OpenAI stream emits 30–80
            # token-sized deltas per second; turning each into its own
            # evaluate_js call saturates the message pump, which is
            # especially bad during cold-init. Coalescing every 50ms or
            # 16 tokens (whichever comes first) drops that to ~20 calls
            # per second with no visible UI change — the frontend just
            # appends payload.chunk to the message text, so "ab" arriving
            # together is identical to "a" then "b" arriving separately.
            flush_interval_s = 0.05
            flush_buffer_size = 16
            full: list[str] = []
            pending: list[str] = []
            last_flush = time.monotonic()
            for event in stream:
                try:
                    delta = event.choices[0].delta.content if event.choices else None
                    if delta:
                        full.append(delta)
                        pending.append(delta)
                        now = time.monotonic()
                        if (len(pending) >= flush_buffer_size
                                or (now - last_flush) >= flush_interval_s):
                            self._stream_emit_chunk(stream_id, "".join(pending))
                            pending.clear()
                            last_flush = now
                except Exception as e:
                    print(f"[stream] chunk skip: {e}", flush=True)
            # Flush any tail tokens that didn't hit the size/interval threshold.
            if pending:
                self._stream_emit_chunk(stream_id, "".join(pending))
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

    def branch_summarize(self, messages: list) -> dict:
        """Gera um resumo conciso da conversa pra usar como contexto inicial
        de uma nova conversa (branch). Retorna {ok: True, summary: "..."}."""
        try:
            from src.config import get_openai_key, get_openai_model
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

            from src.gpt_client import completion_kwargs
            client = OpenAI(api_key=key, timeout=45.0)
            model = get_openai_model()
            resp = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                **completion_kwargs(model, max_tokens=500, temperature=0.3),
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

    def scan_sensitive(self, text: str) -> dict:
        """Detecta padrões de info sensível em texto (CPF/CNPJ/cartão/email/telefone).
        Retorna lista de tipos encontrados pra avisar usuário antes de enviar."""
        try:
            from src.sensitive import scan
            return {"ok": True, "sensitive": scan(text)}
        except Exception as e:
            return {"error": _log_error("scan_sensitive", e)}

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

    def history_suggest_title(self, conv_id: str) -> dict:
        """Gera título inteligente pra uma conversa via IA e persiste.
        Roda em thread pra não bloquear — retorna imediatamente com ok.
        Frontend pode recarregar a lista depois pra ver o título atualizado."""
        try:
            from src.config import get_openai_key
            if not get_openai_key():
                return {"ok": False, "reason": "no api key"}

            def worker():
                try:
                    from src.gpt_client import generate_conversation_title
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

