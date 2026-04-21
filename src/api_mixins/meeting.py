"""Meeting lifecycle + live Q&A + post-processing + transcript.

These methods were extracted from GhostAPI (src/api.py) to keep api.py
navigable. They remain METHODS of GhostAPI via mixin inheritance — so
`self` is still the GhostAPI instance and every `self._X` state access
continues to work unchanged. No behavioral change vs. the pre-split file.

Do NOT instantiate MeetingMixin directly. It exists only as a mixin base
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



class MeetingMixin:
    """Mixin base — injects the following methods onto GhostAPI:
      * start_meeting
      * stop_meeting
      * get_meeting_status
      * _set_meeting_status
      * get_live_transcript
      * meeting_live_question
      * _live_transcribe_loop
      * _process_meeting_async
      * consume_meeting_result
      * open_meetings_folder
    """

    def start_meeting(self, target_kind: str = "monitor", target_id: int | None = None) -> dict:
        """target_kind: 'monitor' uses target_id as monitor index; 'window' uses it as HWND; 'auto' uses current monitor."""
        try:
            if self._meeting.is_running():
                return {"error": "Reunião já em andamento"}
            if self._meeting_processing:
                return {"error": "Processamento anterior ainda em andamento"}

            monitor = None
            window_hwnd = 0
            title_patterns: list[str] = []

            if target_kind == "window" and target_id:
                window_hwnd = int(target_id)
                # If the chosen window is a browser running a meeting page,
                # pin the capture to frames whose title still looks like a
                # meeting. Prevents tab-switching from sneaking a different
                # tab's content into the recording.
                try:
                    import win32gui
                    initial_title = win32gui.GetWindowText(window_hwnd).lower()
                    matched = [p.lower() for p in self._MEETING_TITLE_PATTERNS
                               if p.lower() in initial_title]
                    if matched:
                        title_patterns = matched
                        print(f"[meeting] tab-lock patterns: {matched}", flush=True)
                except Exception:
                    pass
            elif target_kind == "monitor" and target_id is not None:
                monitor = next((m for m in self._monitors if m["index"] == target_id), None)
            else:
                monitor = self._current_monitor() or (self._monitors[0] if self._monitors else None)

            self._meeting.start(
                monitor=monitor,
                window_hwnd=window_hwnd,
                window_title_patterns=title_patterns,
            )
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

    def get_meeting_status(self) -> dict:
        return {
            "running": self._meeting.is_running(),
            "processing": self._meeting_processing,
            "elapsed": self._meeting.elapsed(),
            "elapsed_formatted": format_time(self._meeting.elapsed()),
            "status_text": self._meeting_last_status,
        }

    def _set_meeting_status(self, text: str):
        self._meeting_last_status = text

    def get_live_transcript(self) -> dict:
        """Return the current live transcript snapshot."""
        return {
            "running": self._meeting.is_running(),
            "segments_count": len(self._live_transcript),
            "transcribed_up_to": format_time(self._last_transcribed_sec),
        }

    def meeting_live_question(self, question: str) -> dict:
        """Responde uma pergunta usando a transcrição live + uma captura
        fresca da tela da reunião. A transcrição cobre o que foi DITO; a
        captura cobre o que está NA TELA agora (slide, código, gráfico) —
        perguntas tipo "o que diz nesse slide?" precisam dos dois."""
        try:
            from src.config import get_openai_key, get_openai_model

            if not self._meeting.is_running():
                return {"error": "Nenhuma reunião em andamento"}

            # Just-in-time: transcribe the untranscribed tail (from the last
            # chunked checkpoint up to ~1s before "now") so the question has
            # access to what was said in the last few seconds — not just
            # whatever the 20-second chunker has already captured.
            try:
                import tempfile as _tf
                from pathlib import Path as _P
                tail_start = float(self._last_transcribed_sec)
                tail_end = max(tail_start, self._meeting.elapsed() - 1.0)
                if tail_end - tail_start >= 3.0:  # only worth it if ≥3s of fresh audio
                    tail_tmp = _P(_tf.gettempdir()) / f"ghost_tail_{int(time.time() * 1000)}.wav"
                    p = self._meeting.export_audio_range(tail_start, tail_end, tail_tmp)
                    if p is not None:
                        try:
                            result = transcribe_audio_verbose(p)
                            for seg in result.get("segments", []):
                                self._live_transcript.append({
                                    "start": seg["start"] + tail_start,
                                    "end": seg["end"] + tail_start,
                                    "text": seg["text"],
                                    "_tail": True,  # marker; regular chunker will overwrite on next pass
                                })
                            self._last_transcribed_sec = tail_end
                        finally:
                            try: tail_tmp.unlink()
                            except Exception: pass
            except Exception as _e:
                print(f"[qa] tail transcribe skipped: {_e}", flush=True)

            segs = list(self._live_transcript or [])
            transcript_text = "\n".join(
                f"[{format_time(s.get('start', 0))}] {s.get('text', '')}" for s in segs
            ) if segs else "(transcrição ainda não disponível — ainda processando os primeiros segundos)"

            # Grab the current meeting screen so the model sees slides/charts/code,
            # not just what was spoken aloud. JPEG (not PNG) because we need
            # this encoded in <1s for responsive Q&A; PNG with optimize=True
            # on a 1600-wide screenshot can block for 20-30s and make the
            # typing indicator look frozen.
            import time as _t
            t0 = _t.time()
            img = self._meeting.capture_now()
            print(f"[qa] capture_now: {_t.time()-t0:.2f}s img={'ok' if img else 'none'}", flush=True)

            image_data_url = None
            if img is not None:
                try:
                    import base64 as _b64
                    import io as _io
                    w, h = img.size
                    max_dim = 1280
                    if max(w, h) > max_dim:
                        ratio = max_dim / max(w, h)
                        img = img.resize((int(w * ratio), int(h * ratio)), Image.LANCZOS)
                    # JPEG quality 80 = tiny + looks fine for a meeting screenshot
                    buf = _io.BytesIO()
                    img.convert("RGB").save(buf, format="JPEG", quality=80)
                    b64 = _b64.b64encode(buf.getvalue()).decode("utf-8")
                    image_data_url = f"data:image/jpeg;base64,{b64}"
                    print(f"[qa] jpeg encode: {_t.time()-t0:.2f}s bytes={len(buf.getvalue())}", flush=True)
                except Exception as _e:
                    print(f"[qa] image encode error: {_e}", flush=True)
                    image_data_url = None

            prompt_text = (
                "Você recebe a transcrição parcial de uma reunião em andamento "
                "e uma captura da tela compartilhada neste momento. Use AMBOS "
                "para responder. A tela pode conter slide, código, planilha "
                "ou documento — leia-a literalmente quando a pergunta exigir. "
                "Se a resposta não puder ser inferida nem do áudio nem da tela, diga isso.\n\n"
                f"TRANSCRIÇÃO ATÉ AGORA:\n{transcript_text}\n\n"
                f"PERGUNTA DO USUÁRIO: {question}"
            )

            user_content: list[dict] | str
            if image_data_url:
                user_content = [
                    {"type": "text", "text": prompt_text},
                    {"type": "image_url", "image_url": {"url": image_data_url, "detail": "high"}},
                ]
            else:
                user_content = prompt_text

            key = get_openai_key()
            if not key:
                return {"error": "OpenAI API key não configurada"}

            from openai import OpenAI

            from src.gpt_client import completion_kwargs
            client = OpenAI(api_key=key, timeout=45.0)
            model = get_openai_model()
            print(f"[qa] calling openai model={model} with_image={bool(image_data_url)}", flush=True)
            t1 = _t.time()
            resp = client.chat.completions.create(
                model=model,
                messages=[
                    {"role": "system", "content": "Você é um assistente que ajuda durante reuniões ao vivo, com acesso à transcrição e à tela compartilhada."},
                    {"role": "user", "content": user_content},
                ],
                **completion_kwargs(model, max_tokens=1500),
            )
            print(f"[qa] openai returned in {_t.time()-t1:.2f}s", flush=True)
            return {"ok": True, "text": resp.choices[0].message.content or ""}
        except Exception as e:
            return {"error": _log_error("meeting_live_question", e)}

    def _live_transcribe_loop(self):
        """Transcribe short chunks of the running meeting for live Q&A.
        Shorter chunks (≈20s) keep the transcript close to real-time so the
        assistant can answer questions about things said moments ago without
        waiting a full minute."""
        import tempfile
        from pathlib import Path as _P
        CHUNK = 20.0
        SAFETY = 2.0
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

