"""Lightweight tests for src/voice.py — only the pure/logical bits.

No real microphone or speaker is ever opened: we either avoid start()
entirely, or we stub out `soundcard.default_microphone` with a fake that
yields zero frames until `_running` goes False.
"""
from __future__ import annotations

import time
from contextlib import contextmanager

import numpy as np

from src import voice


def test_voice_recorder_initial_state():
    """Fresh VoiceRecorder: not running, default source is 'mic', elapsed=0, no error."""
    rec = voice.VoiceRecorder()
    assert rec.is_running() is False
    # NOTE: this module defaults to 'mic', not '' — asserting actual behavior.
    assert rec.source() == "mic"
    assert rec.elapsed() == 0.0
    assert rec.last_error() is None

    # stop() with nothing recorded returns None (no audio captured).
    assert rec.stop() is None


def test_voice_recorder_invalid_source_defaults_to_mic(monkeypatch):
    """start('bogus') must normalize source to 'mic' before launching.

    We stub the mic recorder so nothing real is opened.
    """
    calls = {"started": False}

    class _FakeRec:
        def record(self, numframes):
            # Return silence; loop exits as soon as _running flips False.
            time.sleep(0.01)
            return np.zeros((numframes, 1), dtype=np.float32)

    class _FakeMic:
        @contextmanager
        def recorder(self, samplerate, channels, blocksize):
            calls["started"] = True
            yield _FakeRec()

    monkeypatch.setattr(voice.sc, "default_microphone", lambda: _FakeMic())

    rec = voice.VoiceRecorder()
    rec.start(source="not-a-real-source")
    try:
        # Give the daemon thread a moment to enter the recorder context.
        for _ in range(50):
            if calls["started"]:
                break
            time.sleep(0.01)
        assert rec.is_running() is True
        # Invalid input normalized to 'mic'.
        assert rec.source() == "mic"
    finally:
        rec.cancel()  # tears down thread, clears buffer
    assert rec.is_running() is False


def test_voice_recorder_cancel_idempotent_without_start():
    """cancel() before any start() must not raise and must leave recorder idle."""
    rec = voice.VoiceRecorder()
    rec.cancel()  # should be a no-op, no thread to join
    rec.cancel()  # calling again is still safe
    assert rec.is_running() is False
    # No audio was captured, so stop() returns None.
    assert rec.stop() is None


def test_voice_recorder_double_start_is_ignored(monkeypatch):
    """If start() is called while already running, the second call is a no-op
    (source/thread are not replaced)."""

    class _FakeRec:
        def record(self, numframes):
            time.sleep(0.01)
            return np.zeros((numframes, 1), dtype=np.float32)

    class _FakeMic:
        @contextmanager
        def recorder(self, samplerate, channels, blocksize):
            yield _FakeRec()

    monkeypatch.setattr(voice.sc, "default_microphone", lambda: _FakeMic())

    rec = voice.VoiceRecorder()
    rec.start(source="mic")
    try:
        first_thread = rec._thread
        rec.start(source="system")  # should bail because _running is True
        assert rec._thread is first_thread
        assert rec.source() == "mic"
    finally:
        rec.cancel()
