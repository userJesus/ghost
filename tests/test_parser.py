"""Tests for src/parser.py — markdown code-block extraction."""
from __future__ import annotations

from src import parser


def test_extract_code_blocks_returns_language_and_code():
    """extract_code_blocks picks up the language tag and strips trailing whitespace."""
    text = (
        "Aqui vai um exemplo em Python:\n"
        "```python\n"
        "def foo():\n"
        "    return 42\n"
        "```\n"
        "E um em JS:\n"
        "```js\n"
        "const x = 1;\n"
        "```\n"
    )
    blocks = parser.extract_code_blocks(text)
    assert len(blocks) == 2
    assert blocks[0][0] == "python"
    assert blocks[0][1] == "def foo():\n    return 42"
    assert blocks[1] == ("js", "const x = 1;")

    # No fences -> empty list.
    assert parser.extract_code_blocks("plain text, no code") == []

    # Blank language tag is preserved (empty string, still a tuple).
    (lang, code), = parser.extract_code_blocks("```\nabc\n```")
    assert lang == ""
    assert code == "abc"


def test_strip_code_blocks_replaces_fences_with_placeholder():
    text = (
        "Intro\n"
        "```python\n"
        "x = 1\n"
        "```\n"
        "Outro"
    )
    stripped = parser.strip_code_blocks(text)
    assert "```" not in stripped
    assert "x = 1" not in stripped
    assert "[código abaixo]" in stripped
    # Intro/Outro surrounding text is preserved.
    assert stripped.startswith("Intro")
    assert stripped.endswith("Outro")


def test_pick_main_code_returns_largest_block():
    """pick_main_code picks the (lang, code) tuple with the longest code."""
    # Empty input -> None.
    assert parser.pick_main_code([]) is None

    # Single block -> itself.
    single = [("python", "print(1)")]
    assert parser.pick_main_code(single) == ("python", "print(1)")

    # Three blocks, middle one is longest.
    blocks = [
        ("py", "a"),
        ("js", "xx" * 50),
        ("rs", "bb"),
    ]
    lang, code = parser.pick_main_code(blocks)
    assert lang == "js"
    assert code == "xx" * 50
