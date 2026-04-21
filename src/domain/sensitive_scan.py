"""PII detection on free-form text (CPF, CNPJ, card, email, phone, CEP).

Pure-function module. No I/O, no network. Pre-compiled regex patterns
scanned against the input; returns a list of matches with counts + samples.

Used by the chat service before a message is sent to OpenAI so the UI can
warn the user if they are about to leak personal data by accident.
"""
from __future__ import annotations

import re
from typing import TypedDict

_PATTERNS: dict[str, re.Pattern[str]] = {
    "CPF": re.compile(r"\b\d{3}\.?\d{3}\.?\d{3}-?\d{2}\b"),
    "CNPJ": re.compile(r"\b\d{2}\.?\d{3}\.?\d{3}/?\d{4}-?\d{2}\b"),
    "Cartão de crédito": re.compile(r"\b(?:\d[ -]*?){13,16}\b"),
    "Email": re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b"),
    "Telefone": re.compile(r"\b\(?\d{2}\)?\s*9?\d{4}-?\d{4}\b"),
    "CEP": re.compile(r"\b\d{5}-?\d{3}\b"),
}


class SensitiveMatch(TypedDict):
    type: str
    count: int
    sample: str


def scan(text: str) -> list[SensitiveMatch]:
    """Return list of PII types detected in `text`.

    Each entry: {type, count, sample} where sample is the first match
    truncated to 40 chars. Empty list if nothing found or text is falsy.
    """
    if not text:
        return []
    results: list[SensitiveMatch] = []
    for name, pattern in _PATTERNS.items():
        matches = pattern.findall(text)
        if matches:
            results.append({
                "type": name,
                "count": len(matches),
                "sample": str(matches[0])[:40],
            })
    return results
