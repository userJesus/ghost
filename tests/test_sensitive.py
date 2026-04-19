"""Testes pro módulo src.sensitive (scan de PII)."""
from __future__ import annotations

from src.sensitive import scan


def test_scan_empty_returns_empty_list():
    assert scan("") == []
    assert scan(None) == []  # type: ignore[arg-type]


def test_scan_detects_cpf_with_and_without_mask():
    masked = scan("CPF: 123.456.789-00")
    bare = scan("CPF 12345678900")
    assert any(m["type"] == "CPF" for m in masked)
    assert any(m["type"] == "CPF" for m in bare)


def test_scan_detects_email():
    r = scan("contato: jesus.oliveira@example.com pra falar")
    emails = [m for m in r if m["type"] == "Email"]
    assert len(emails) == 1
    assert emails[0]["count"] == 1
    assert "jesus" in emails[0]["sample"]


def test_scan_detects_multiple_types_in_same_text():
    text = "CPF 123.456.789-00, email a@b.com, CEP 01310-100"
    types = {m["type"] for m in scan(text)}
    assert "CPF" in types
    assert "Email" in types
    assert "CEP" in types


def test_scan_counts_multiple_occurrences():
    r = scan("emails: a@b.com, c@d.com, e@f.com")
    emails = next(m for m in r if m["type"] == "Email")
    assert emails["count"] == 3


def test_scan_sample_is_truncated_to_40_chars():
    long_email = "a" * 100 + "@example.com"
    r = scan(long_email)
    sample = next(m for m in r if m["type"] == "Email")["sample"]
    assert len(sample) <= 40


def test_scan_returns_list_type():
    # Contract: retorno é uma lista (não dict) com itens tipados
    result = scan("test 123.456.789-00")
    assert isinstance(result, list)
    for item in result:
        assert "type" in item
        assert "count" in item
        assert "sample" in item
