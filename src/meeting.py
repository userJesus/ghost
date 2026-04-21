"""Backwards-compatible re-export of the moved meeting recorder.

Real implementation: `src.recording.meeting_recorder`.
"""
from __future__ import annotations

from .recording.meeting_recorder import (  # noqa: F401
    BLOCK_SIZE,
    CHANNELS,
    SAMPLE_RATE,
    VIDEO_FPS,
    VIDEO_MAX_WIDTH,
    VIDEO_QUALITY,
    MeetingRecorder,
    format_time,
)
