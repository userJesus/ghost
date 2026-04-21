"""Backwards-compatible re-export of the moved PII scanner.

Real implementation: `src.domain.sensitive_scan`.
"""
from __future__ import annotations

from .domain.sensitive_scan import SensitiveMatch, scan

__all__ = ["SensitiveMatch", "scan"]
