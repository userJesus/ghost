"""History service — conversation persistence.

Stateless wrapper around `src.history` (the pre-refactor module at
`~/.ghost/history.json`). The module-level functions stay where tests
expect them; this service provides an object-oriented facade.
"""
from __future__ import annotations

from src import history as _history


class HistoryService:
    """Thin OO facade over the pre-existing functional history module."""

    def list(self) -> dict:
        return {"ok": True, "conversations": _history.list_conversations()}

    def get(self, conv_id: str) -> dict:
        c = _history.get_conversation(conv_id)
        if not c:
            return {"error": "Conversa não encontrada"}
        return {"ok": True, "conversation": c}

    def save(self, conv_id: str, messages: list) -> dict:
        meta = _history.save_conversation(conv_id, messages)
        return {"ok": True, "meta": meta}

    def delete(self, conv_id: str) -> dict:
        ok = _history.delete_conversation(conv_id)
        # Preserve the pre-refactor contract: {"ok": False} on not-found
        # (the frontend distinguishes "deleted ok" from "nothing to delete").
        return {"ok": ok}

    def clear_all(self) -> dict:
        _history.clear_all()
        return {"ok": True}

    def new_id(self) -> dict:
        """Return the same shape the pre-refactor GhostAPI returned:
        `{"ok": True, "id": "conv-..."}` — not a bare string."""
        return {"ok": True, "id": _history.new_id()}
