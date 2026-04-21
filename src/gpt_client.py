"""Backwards-compatible re-export of the moved OpenAI client.

Real implementation: `src.integrations.openai_client`.
"""
from __future__ import annotations

from .integrations.openai_client import (  # noqa: F401
    BASE_PERSONA,
    SCREEN_CONTEXT_ADDENDUM,
    SYSTEM_PROMPT,
    analyze_image,
    build_user_message,
    chat_completion,
    completion_kwargs,
    generate_conversation_title,
)
