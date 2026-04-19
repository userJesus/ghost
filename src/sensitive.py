"""Detecção de dados sensíveis (CPF, CNPJ, cartão, email, etc.) em texto.

Função pura — testável sem mock. Usada antes de enviar prompts à OpenAI
pra alertar o usuário caso esteja expondo PII por engano.
"""
from __future__ import annotations

import re
from typing import TypedDict

# Padrões organizados como módulo-level pra serem compiláveis uma vez
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
    """Retorna lista de tipos de PII detectados no texto.

    Cada item tem `type` (ex: 'CPF'), `count` (quantas ocorrências) e
    `sample` (primeiros 40 chars do primeiro match, pra confirmação).

    Retorna lista vazia se nada for encontrado ou se `text` for falsy.
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
