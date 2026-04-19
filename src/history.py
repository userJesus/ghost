"""Histórico de conversas — salva/carrega em ~/.ghost/history.json.

Estrutura:
{
    "conversations": [
        {
            "id": "conv-1719234567123",
            "title": "Como funciona o Alpine.js",
            "created_at": "2026-04-18T20:30:00",
            "updated_at": "2026-04-18T20:35:12",
            "messages": [ {role, text, image?, transcript?}, ... ]
        },
        ...
    ]
}
"""
import json
import time
from datetime import datetime
from pathlib import Path

HISTORY_DIR = Path.home() / ".ghost"
HISTORY_FILE = HISTORY_DIR / "history.json"
MAX_CONVERSATIONS = 100  # Mantém últimas 100 pra não crescer infinito


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _load() -> dict:
    try:
        if HISTORY_FILE.exists():
            data = json.loads(HISTORY_FILE.read_text(encoding="utf-8"))
            if isinstance(data, dict) and "conversations" in data:
                return data
    except Exception as e:
        print(f"[history] load error: {e}", flush=True)
    return {"conversations": []}


def _save(data: dict) -> None:
    try:
        HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        HISTORY_FILE.write_text(
            json.dumps(data, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    except Exception as e:
        print(f"[history] save error: {e}", flush=True)


def _derive_title(messages: list[dict]) -> str:
    """Gera título baseado na primeira pergunta do usuário."""
    for m in messages:
        if m.get("role") == "user":
            t = (m.get("text") or "").strip()
            # Remove prefixo [Trecho de áudio transcrito]...
            if t.startswith("[Trecho de áudio"):
                idx = t.find("\n\n")
                if idx > 0:
                    t = t[idx + 2:].strip()
            # Tira linhas e corta
            t = t.replace("\n", " ")
            if len(t) > 60:
                t = t[:57] + "..."
            return t or "Conversa sem título"
    return "Conversa sem título"


def list_conversations() -> list[dict]:
    """Retorna lista resumida (sem messages) ordenada por updated_at desc."""
    data = _load()
    items = []
    for c in data["conversations"]:
        items.append({
            "id": c.get("id"),
            "title": c.get("title", "Sem título"),
            "created_at": c.get("created_at", ""),
            "updated_at": c.get("updated_at", ""),
            "message_count": len(c.get("messages", [])),
        })
    items.sort(key=lambda x: x.get("updated_at", ""), reverse=True)
    return items


def get_conversation(conv_id: str) -> dict | None:
    data = _load()
    for c in data["conversations"]:
        if c.get("id") == conv_id:
            return c
    return None


def save_conversation(conv_id: str, messages: list[dict]) -> dict:
    """Cria ou atualiza uma conversa. Retorna a metadata salva."""
    data = _load()
    # Procura se já existe
    existing = None
    for c in data["conversations"]:
        if c.get("id") == conv_id:
            existing = c
            break

    title = _derive_title(messages)
    now = _now_iso()

    if existing:
        existing["messages"] = messages
        existing["updated_at"] = now
        if existing.get("title", "").startswith("Conversa sem título"):
            existing["title"] = title
        saved = existing
    else:
        saved = {
            "id": conv_id,
            "title": title,
            "created_at": now,
            "updated_at": now,
            "messages": messages,
        }
        data["conversations"].append(saved)

    # Poda lista
    if len(data["conversations"]) > MAX_CONVERSATIONS:
        data["conversations"].sort(key=lambda x: x.get("updated_at", ""), reverse=True)
        data["conversations"] = data["conversations"][:MAX_CONVERSATIONS]

    _save(data)
    return {
        "id": saved["id"],
        "title": saved["title"],
        "created_at": saved["created_at"],
        "updated_at": saved["updated_at"],
        "message_count": len(saved["messages"]),
    }


def delete_conversation(conv_id: str) -> bool:
    data = _load()
    before = len(data["conversations"])
    data["conversations"] = [c for c in data["conversations"] if c.get("id") != conv_id]
    if len(data["conversations"]) < before:
        _save(data)
        return True
    return False


def clear_all() -> None:
    _save({"conversations": []})


def new_id() -> str:
    return f"conv-{int(time.time() * 1000)}"
