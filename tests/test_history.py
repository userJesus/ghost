"""Tests for src/history.py — conversation persistence in ~/.ghost/history.json."""
from __future__ import annotations

import json
import re
import time

from src import history


def test_new_id_format():
    """new_id() must return 'conv-<timestamp_ms>' with a plausible current ms value."""
    before_ms = int(time.time() * 1000)
    cid = history.new_id()
    after_ms = int(time.time() * 1000)

    assert isinstance(cid, str)
    m = re.fullmatch(r"conv-(\d+)", cid)
    assert m is not None, f"unexpected format: {cid!r}"
    ts = int(m.group(1))
    # The embedded timestamp must fall within the window in which we called it.
    assert before_ms - 5 <= ts <= after_ms + 5


def test_save_and_get_conversation(tmp_ghost_home, sample_messages):
    """Save a conversation, then get it back by id — messages must round-trip."""
    cid = history.new_id()
    meta = history.save_conversation(cid, sample_messages)

    assert meta["id"] == cid
    assert meta["message_count"] == len(sample_messages)

    loaded = history.get_conversation(cid)
    assert loaded is not None
    assert loaded["id"] == cid
    assert loaded["messages"] == sample_messages
    # Non-existent id returns None, not a placeholder
    assert history.get_conversation("conv-does-not-exist") is None


def test_save_creates_valid_json_file(tmp_ghost_home, sample_messages):
    """history.json must be created, parseable JSON, and contain the saved conversation."""
    assert not tmp_ghost_home.exists(), "precondition: fresh temp dir"

    cid = history.new_id()
    history.save_conversation(cid, sample_messages)

    hist_file = tmp_ghost_home / "history.json"
    assert hist_file.is_file()

    raw = hist_file.read_text(encoding="utf-8")
    data = json.loads(raw)  # raises if invalid JSON
    assert "conversations" in data
    assert len(data["conversations"]) == 1
    assert data["conversations"][0]["id"] == cid


def test_list_conversations_sorted_desc(tmp_ghost_home):
    """Three conversations saved with spaced timestamps -> listing is updated_at DESC."""
    ids = []
    for i in range(3):
        cid = f"conv-{1000 + i}"  # stable ids independent of wallclock
        history.save_conversation(cid, [{"role": "user", "text": f"msg {i}"}])
        ids.append(cid)
        # _now_iso() has second granularity; sleep >1s to guarantee ordering
        time.sleep(1.1)

    listed = history.list_conversations()
    listed_ids = [c["id"] for c in listed]
    # Most recently saved (ids[2]) should come first.
    assert listed_ids == list(reversed(ids))

    # updated_at values must be monotonically non-increasing.
    ups = [c["updated_at"] for c in listed]
    assert ups == sorted(ups, reverse=True)


def test_derive_title_from_user_message():
    """Title must come from the FIRST user message, collapsing newlines,
    truncating at 60 chars (57 + '...')."""
    # Short message: used verbatim.
    short = history._derive_title(
        [
            {"role": "assistant", "text": "ignore me"},  # skipped — not user
            {"role": "user", "text": "Olá mundo"},
        ]
    )
    assert short == "Olá mundo"

    # Long message: trimmed at 57 chars then '...'.
    long_text = "a" * 120
    long_title = history._derive_title([{"role": "user", "text": long_text}])
    assert long_title == "a" * 57 + "..."
    assert len(long_title) == 60

    # Newlines collapsed to spaces.
    multi = history._derive_title(
        [{"role": "user", "text": "linha 1\nlinha 2"}]
    )
    assert "\n" not in multi
    assert multi == "linha 1 linha 2"

    # No user message -> fallback string.
    assert history._derive_title([{"role": "assistant", "text": "oi"}]) == (
        "Conversa sem título"
    )


def test_derive_title_strips_audio_transcript_prefix():
    """Messages starting with '[Trecho de áudio ...]\\n\\n<real>' should title from <real>."""
    text = "[Trecho de áudio transcrito]\n\nQual é a capital da França?"
    title = history._derive_title([{"role": "user", "text": text}])
    assert title == "Qual é a capital da França?"
    assert not title.startswith("[Trecho")


def test_delete_conversation_removes_from_list(tmp_ghost_home, sample_messages):
    """delete_conversation returns True + removes it; deleting again returns False."""
    cid_a = "conv-a"
    cid_b = "conv-b"
    history.save_conversation(cid_a, sample_messages)
    history.save_conversation(cid_b, sample_messages)

    assert {c["id"] for c in history.list_conversations()} == {cid_a, cid_b}

    assert history.delete_conversation(cid_a) is True
    remaining_ids = {c["id"] for c in history.list_conversations()}
    assert remaining_ids == {cid_b}
    assert history.get_conversation(cid_a) is None

    # Deleting an already-deleted id is a no-op returning False.
    assert history.delete_conversation(cid_a) is False


def test_max_conversations_limit(tmp_ghost_home, monkeypatch):
    """Save MAX+1 conversations; only the newest MAX must survive the prune.

    `_now_iso()` has second-granularity, so rapid-fire saves would tie on
    updated_at and make the prune non-deterministic (stable sort keeps
    insertion order, and insertion order disagrees with 'newest first').
    We inject a monotonically-increasing timestamp to nail ordering down.
    """
    counter = {"n": 0}

    def fake_now_iso():
        counter["n"] += 1
        # ISO-ish lexicographic-sortable string, strictly increasing.
        return f"2026-01-01T00:00:{counter['n']:06d}"

    monkeypatch.setattr(history, "_now_iso", fake_now_iso)

    max_n = history.MAX_CONVERSATIONS
    total = max_n + 1

    for i in range(total):
        history.save_conversation(
            f"conv-{i:04d}", [{"role": "user", "text": f"msg {i}"}]
        )

    listed = history.list_conversations()
    assert len(listed) == max_n

    listed_ids = {c["id"] for c in listed}
    # The very first conversation has the oldest updated_at -> must be pruned.
    assert "conv-0000" not in listed_ids
    # The most recent one must still be there.
    assert f"conv-{total - 1:04d}" in listed_ids
