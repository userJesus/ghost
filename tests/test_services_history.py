"""Tests for src/services/history_service.py — OO facade over src.history."""
from __future__ import annotations

from src.services.history_service import HistoryService


class TestHistoryServiceBridgeShape:
    """The bridge method return shape must match what app.js expects."""

    def test_list_empty(self, tmp_ghost_home):
        svc = HistoryService()
        r = svc.list()
        assert r == {"ok": True, "conversations": []}

    def test_new_id_returns_dict_not_bare_string(self, tmp_ghost_home):
        """Pre-refactor bridge method `history_new_id` returned `{"ok": True, "id": "..."}`
        — preserving this shape is load-bearing for app.js."""
        svc = HistoryService()
        r = svc.new_id()
        assert isinstance(r, dict)
        assert r["ok"] is True
        assert r["id"].startswith("conv-")

    def test_save_and_get_roundtrip(self, tmp_ghost_home, sample_messages):
        svc = HistoryService()
        conv_id = svc.new_id()["id"]
        saved = svc.save(conv_id, sample_messages)
        assert saved["ok"] is True
        meta = saved["meta"]
        assert meta["id"] == conv_id
        assert meta["message_count"] == len(sample_messages)

        got = svc.get(conv_id)
        assert got["ok"] is True
        assert got["conversation"]["id"] == conv_id
        assert got["conversation"]["messages"] == sample_messages

    def test_get_not_found_returns_error_shape(self, tmp_ghost_home):
        svc = HistoryService()
        r = svc.get("conv-does-not-exist")
        assert "error" in r
        assert "não encontrada" in r["error"]

    def test_delete_returns_ok_false_on_not_found(self, tmp_ghost_home):
        """Pre-refactor contract: `{"ok": False}` when nothing was deleted.
        The UI distinguishes 'deleted ok' from 'nothing to delete', so this
        shape is load-bearing."""
        svc = HistoryService()
        r = svc.delete("conv-does-not-exist")
        assert r == {"ok": False}

    def test_delete_returns_ok_true_when_deleted(self, tmp_ghost_home, sample_messages):
        svc = HistoryService()
        conv_id = svc.new_id()["id"]
        svc.save(conv_id, sample_messages)
        r = svc.delete(conv_id)
        assert r == {"ok": True}
        # Verify it's actually gone
        assert svc.get(conv_id).get("error")


class TestHistoryIntegrationWithUnderlyingModule:
    """The service is a THIN wrapper — data must round-trip through src.history."""

    def test_list_reflects_saved_conversations(self, tmp_ghost_home, sample_messages):
        svc = HistoryService()
        # `new_id()` uses millisecond-precision timestamps, so three calls in a
        # tight loop on a fast CPU can collide. Pass explicit unique ids
        # instead — mirrors real-world use where the frontend also generates
        # ids from `Date.now()` with natural gaps between conversations.
        ids = [f"conv-test-{i}" for i in range(3)]
        for i, conv_id in enumerate(ids):
            svc.save(conv_id, [{"role": "user", "text": f"conv {i}"}])

        r = svc.list()
        assert r["ok"] is True
        assert len(r["conversations"]) == 3
        returned_ids = {c["id"] for c in r["conversations"]}
        assert returned_ids == set(ids)
