"""Mixins that split GhostAPI across focused files.

See src/api.py for the orchestrating class.
"""
from .window import WindowMixin
from .capture import CaptureMixin
from .chat import ChatMixin
from .meeting import MeetingMixin

__all__ = ["WindowMixin", "CaptureMixin", "ChatMixin", "MeetingMixin"]
