"""Backwards-compatible re-export of the moved voice recorder.

Real implementation: `src.recording.voice_recorder`.

This shim deliberately re-exports the `sc` (soundcard) and `sf` (soundfile)
module aliases so existing tests that do `monkeypatch.setattr(voice.sc, ...)`
continue to work without modification.
"""
from __future__ import annotations

from .recording.voice_recorder import (  # noqa: F401
    BLOCK_SIZE,
    CHANNELS,
    SAMPLE_RATE,
    VoiceRecorder,
    sc,  # re-exported so tests can patch voice.sc.* without awareness of the move
    sf,  # re-exported for symmetry; not currently patched by tests but cheap to keep
)
