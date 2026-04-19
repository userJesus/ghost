"""Configuração centralizada de logging para o Ghost.

Usa a lib `logging` padrão com formatter customizado. Emite timestamps,
nível, módulo e mensagem. Nível global controlado por env var
`GHOST_LOG_LEVEL` (default INFO).

Uso:
    from src.logging_config import get_logger
    log = get_logger(__name__)
    log.info("tudo certo")
    log.error("deu ruim: %s", err)
"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from pathlib import Path

_DEFAULT_FORMAT = "%(asctime)s [%(levelname)-5s] %(name)s: %(message)s"
_DATE_FORMAT = "%H:%M:%S"

_configured = False


def _parse_level(value: str | None) -> int:
    if not value:
        return logging.INFO
    value = value.strip().upper()
    if value.isdigit():
        return int(value)
    return getattr(logging, value, logging.INFO)


def _log_dir() -> Path:
    return Path.home() / ".ghost"


def configure(level: int | str | None = None, log_file: Path | None = None) -> None:
    """Configura o logging root. Idempotente — chamadas repetidas são ignoradas."""
    global _configured
    if _configured:
        return

    lvl = _parse_level(level) if isinstance(level, str) or level is None else int(level)

    root = logging.getLogger()
    root.setLevel(lvl)

    # Limpa handlers pré-existentes (pywebview/libs podem ter adicionado)
    for h in list(root.handlers):
        root.removeHandler(h)

    formatter = logging.Formatter(_DEFAULT_FORMAT, datefmt=_DATE_FORMAT)

    stream = logging.StreamHandler(sys.stderr)
    stream.setFormatter(formatter)
    stream.setLevel(lvl)
    root.addHandler(stream)

    target_file = log_file or (_log_dir() / "ghost.log")
    try:
        target_file.parent.mkdir(parents=True, exist_ok=True)
        file_handler = RotatingFileHandler(
            target_file,
            maxBytes=2 * 1024 * 1024,
            backupCount=3,
            encoding="utf-8",
        )
        file_handler.setFormatter(formatter)
        file_handler.setLevel(lvl)
        root.addHandler(file_handler)
    except Exception as exc:
        root.warning("file log disabled: %s", exc)

    # Silencia libs barulhentas
    for noisy in ("urllib3", "httpx", "openai", "PIL", "httpcore"):
        logging.getLogger(noisy).setLevel(max(lvl, logging.WARNING))

    _configured = True


def get_logger(name: str) -> logging.Logger:
    """Factory principal. Garante que logging foi configurado antes."""
    if not _configured:
        configure(os.environ.get("GHOST_LOG_LEVEL"))
    return logging.getLogger(name)
