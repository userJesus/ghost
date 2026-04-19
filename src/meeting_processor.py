"""Transcribe + summarize + write meeting doc (no speaker diarization)."""
import json
import re
from datetime import datetime
from pathlib import Path

from openai import OpenAI
from PIL import Image

from .capture import image_to_base64
from .config import get_openai_key, get_openai_model
from .gpt_client import completion_kwargs
from .meeting import format_time


def _client() -> OpenAI:
    key = get_openai_key()
    if not key:
        raise RuntimeError("OpenAI API key não configurada. Abra Configurações no Ghost.")
    return OpenAI(api_key=key)


def desktop_dir() -> Path:
    home = Path.home()
    desktop_candidates = [
        home / "Desktop",
        home / "OneDrive" / "Desktop",
        home / "OneDrive" / "Área de Trabalho",
    ]
    for d in desktop_candidates:
        if d.exists():
            return d
    return home


def meetings_dir() -> Path:
    d = desktop_dir() / "Ghost-Reunioes"
    d.mkdir(parents=True, exist_ok=True)
    return d


def transcribe_audio_verbose(audio_path: Path) -> dict:
    """Whisper with verbose_json returns segments + timestamps."""
    with open(audio_path, "rb") as f:
        result = _client().audio.transcriptions.create(
            model="whisper-1",
            file=f,
            response_format="verbose_json",
            language="pt",
        )
    return {
        "text": getattr(result, "text", ""),
        "segments": [
            {
                "start": seg.get("start") if isinstance(seg, dict) else seg.start,
                "end": seg.get("end") if isinstance(seg, dict) else seg.end,
                "text": (seg.get("text") if isinstance(seg, dict) else seg.text).strip(),
            }
            for seg in (result.segments or [])
        ],
    }


def transcribe_chunks_verbose(chunk_paths: list[Path], chunk_seconds: int = 600,
                              status_cb=None) -> dict:
    """Transcribe each chunk and merge segments with proper timestamp offsets."""
    all_segments: list[dict] = []
    full_text_parts: list[str] = []

    for i, path in enumerate(chunk_paths):
        if status_cb:
            status_cb(f"Transcrevendo parte {i + 1}/{len(chunk_paths)}...")
        try:
            result = transcribe_audio_verbose(path)
            offset = i * chunk_seconds
            for seg in result["segments"]:
                all_segments.append({
                    "start": seg["start"] + offset,
                    "end": seg["end"] + offset,
                    "text": seg["text"],
                })
            full_text_parts.append(result["text"].strip())
        except Exception as e:
            full_text_parts.append(f"[erro na parte {i + 1}: {e}]")

    return {
        "segments": all_segments,
        "full_text": "\n\n".join(full_text_parts),
    }


SUMMARY_PROMPT = """Você recebe a transcrição de uma reunião (com timestamps) e algumas screenshots. Gere um resumo estruturado em português brasileiro.

Retorne APENAS um JSON válido (sem markdown), com esta estrutura:

{
  "executivo": ["bullet 1", "bullet 2", ...],
  "decisoes": ["decisão 1", ...],
  "acoes": [
    {"tarefa": "...", "responsavel": "Nome ou 'não definido'", "prazo": "... ou 'não definido'"}
  ],
  "destaques": ["insight ou trecho importante 1", ...],
  "temas": ["tema tratado 1", ...]
}

Seja objetivo. Não invente informação que não está nem na transcrição nem nas screenshots. Se algum campo não tiver informação, retorne lista vazia."""


def summarize_meeting(segments: list[dict], screenshots: list[tuple[float, Image.Image]],
                      status_cb=None) -> dict:
    """Generate a structured summary from transcript segments + screenshots."""
    if status_cb:
        status_cb("Gerando resumo com GPT...")

    seg_lines = [f"[{format_time(seg['start'])}] {seg['text']}" for seg in segments]
    segments_text = "\n".join(seg_lines)

    user_content = [
        {"type": "text", "text": f"TRANSCRIÇÃO DA REUNIÃO:\n\n{segments_text[:80000]}"},
    ]

    if screenshots:
        step = max(1, len(screenshots) // 4)
        selected = screenshots[::step][:4]
        for t_offset, img in selected:
            user_content.append({
                "type": "text",
                "text": f"\n[Screenshot em {format_time(t_offset)}]",
            })
            user_content.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:image/png;base64,{image_to_base64(img, max_dim=1200)}",
                    "detail": "low",
                },
            })

    model = get_openai_model()
    response = _client().chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": SUMMARY_PROMPT},
            {"role": "user", "content": user_content},
        ],
        response_format={"type": "json_object"},
        **completion_kwargs(model, max_tokens=3000),
    )

    content = response.choices[0].message.content or "{}"
    try:
        return json.loads(content)
    except json.JSONDecodeError:
        m = re.search(r"\{.*\}", content, re.DOTALL)
        if m:
            try:
                return json.loads(m.group(0))
            except Exception:
                pass
        return {"executivo": [content], "decisoes": [], "acoes": [], "destaques": [], "temas": []}


def _render_summary_md(summary: dict) -> str:
    lines = []
    lines.append("## 📌 Resumo executivo")
    lines.append("")
    executivo = summary.get("executivo") or []
    if executivo:
        for b in executivo:
            lines.append(f"- {b}")
    else:
        lines.append("_(sem resumo gerado)_")
    lines.append("")

    temas = summary.get("temas") or []
    if temas:
        lines.append("## 🧭 Temas tratados")
        lines.append("")
        for t in temas:
            lines.append(f"- {t}")
        lines.append("")

    decisoes = summary.get("decisoes") or []
    if decisoes:
        lines.append("## ✅ Decisões")
        lines.append("")
        for d in decisoes:
            lines.append(f"- {d}")
        lines.append("")

    acoes = summary.get("acoes") or []
    if acoes:
        lines.append("## 🎯 Ações / Next steps")
        lines.append("")
        for a in acoes:
            if isinstance(a, dict):
                task = a.get("tarefa", "")
                resp = a.get("responsavel", "não definido")
                prazo = a.get("prazo", "não definido")
                lines.append(f"- {task} — **{resp}** *(prazo: {prazo})*")
            else:
                lines.append(f"- {a}")
        lines.append("")

    destaques = summary.get("destaques") or []
    if destaques:
        lines.append("## 💡 Pontos de destaque")
        lines.append("")
        for d in destaques:
            lines.append(f"- {d}")
        lines.append("")

    return "\n".join(lines)


def _render_timestamped_transcript(segments: list[dict]) -> str:
    if not segments:
        return "_(transcrição vazia)_"
    lines = []
    for seg in segments:
        ts = format_time(seg["start"])
        text = seg["text"].strip()
        if not text:
            continue
        lines.append(f"**[{ts}]** {text}")
    return "\n\n".join(lines)


def write_markdown_doc(
    title: str,
    started_at: datetime,
    duration_sec: float,
    segments: list[dict],
    raw_transcript: str,
    summary: dict,
    audio_path: Path | None = None,
    video_path: Path | None = None,
    out_dir: Path | None = None,
) -> Path:
    if out_dir is None:
        out_dir = meetings_dir()
    doc_path = out_dir / "reuniao.md"

    lines = [
        f"# {title}",
        "",
        f"**Data:** {started_at.strftime('%d/%m/%Y %H:%M')}",
        f"**Duração:** {format_time(duration_sec)}",
    ]
    if video_path and video_path.exists():
        try:
            rel_video = video_path.relative_to(out_dir)
            lines.append(f"**Vídeo:** [`{rel_video.name}`]({rel_video})")
        except ValueError:
            lines.append(f"**Vídeo:** `{video_path}`")
    if audio_path and audio_path.exists():
        try:
            rel_audio = audio_path.relative_to(out_dir)
            lines.append(f"**Áudio:** [`{rel_audio.name}`]({rel_audio})")
        except ValueError:
            lines.append(f"**Áudio:** `{audio_path}`")
    lines.append("")
    lines.append("---")
    lines.append("")

    lines.append(_render_summary_md(summary))
    lines.append("---")
    lines.append("")

    lines.append("## 🗣 Transcrição com timestamps")
    lines.append("")
    lines.append(_render_timestamped_transcript(segments))
    lines.append("")

    if raw_transcript.strip() and not segments:
        lines.append("---")
        lines.append("")
        lines.append("## 📝 Transcrição bruta")
        lines.append("")
        lines.append(raw_transcript.strip())
        lines.append("")

    doc_path.write_text("\n".join(lines), encoding="utf-8")
    return doc_path


# Backward-compat aliases
transcribe_chunks = transcribe_chunks_verbose
diarize_and_summarize = summarize_meeting
