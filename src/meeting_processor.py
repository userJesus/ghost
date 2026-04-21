"""Backwards-compatible re-export of the moved meeting processor.

Real implementation: `src.recording.meeting_processor`.
"""
from __future__ import annotations

from .recording.meeting_processor import (  # noqa: F401
    SUMMARY_PROMPT,
    desktop_dir,
    diarize_and_summarize,
    meetings_dir,
    summarize_meeting,
    transcribe_audio_verbose,
    transcribe_chunks,
    transcribe_chunks_verbose,
    write_markdown_doc,
)
