"""Tests for src/config.py — model catalogue + user config persistence."""
from __future__ import annotations

import json

from src import config

REQUIRED_FIELDS = {"id", "name", "description", "tier", "input_price", "output_price"}


def test_supported_models_have_required_fields():
    """Every entry in SUPPORTED_MODELS must carry the fields the UI relies on."""
    assert len(config.SUPPORTED_MODELS) > 0
    for m in config.SUPPORTED_MODELS:
        missing = REQUIRED_FIELDS - m.keys()
        assert not missing, f"model {m.get('id')!r} missing fields: {missing}"
        assert isinstance(m["id"], str) and m["id"]
        assert m["tier"] in {"economy", "balanced", "flagship"}
        assert isinstance(m["input_price"], (int, float)) and m["input_price"] > 0
        assert isinstance(m["output_price"], (int, float)) and m["output_price"] > 0


def test_supported_models_have_unique_ids():
    ids = [m["id"] for m in config.SUPPORTED_MODELS]
    assert len(ids) == len(set(ids)), f"duplicate model ids: {ids}"
    # SUPPORTED_MODEL_IDS is the set form used by get_openai_model().
    assert config.SUPPORTED_MODEL_IDS == set(ids)


def test_get_openai_model_default(tmp_ghost_home):
    """With no user config, get_openai_model() returns OPENAI_MODEL_DEFAULT
    (when it's a supported id) or the hardcoded 'gpt-4.1-mini' fallback."""
    # tmp_ghost_home guarantees USER_CONFIG_FILE does not exist.
    assert not config.USER_CONFIG_FILE.exists()

    got = config.get_openai_model()
    # Must be one of the supported ids, and must match the documented fallback chain.
    assert got in config.SUPPORTED_MODEL_IDS
    if config.OPENAI_MODEL_DEFAULT in config.SUPPORTED_MODEL_IDS:
        assert got == config.OPENAI_MODEL_DEFAULT
    else:
        assert got == "gpt-4.1-mini"


def test_set_and_get_openai_model(tmp_ghost_home):
    """Saving a supported id -> get returns it. Saving an unsupported id ->
    get falls back to default (invalid ids are silently ignored)."""
    # Valid id round-trip.
    config.save_user_config({"openai_model": "gpt-5"})
    assert config.get_openai_model() == "gpt-5"

    # Invalid id is rejected by get_openai_model() (returns default fallback).
    config.save_user_config({"openai_model": "gpt-nonexistent-9000"})
    got = config.get_openai_model()
    assert got != "gpt-nonexistent-9000"
    assert got in config.SUPPORTED_MODEL_IDS


def test_model_cost_per_100_is_calculated():
    """_enrich_model must have added cost_per_100 and recommended flags to each model."""
    for m in config.SUPPORTED_MODELS:
        assert "cost_per_100" in m
        assert isinstance(m["cost_per_100"], (int, float))
        assert m["cost_per_100"] > 0
        # Formula: (2000 * in_price + 500 * out_price) / 1e6 * 100, rounded to 3.
        expected = round(
            (2000 * m["input_price"] + 500 * m["output_price"]) / 1_000_000 * 100,
            3,
        )
        assert m["cost_per_100"] == expected

    # Exactly one model is the recommended one, and it's gpt-5-mini.
    recommended = [m for m in config.SUPPORTED_MODELS if m.get("recommended")]
    assert len(recommended) == 1
    assert recommended[0]["id"] == "gpt-5-mini"


def test_has_openai_key_detection(tmp_ghost_home):
    """No key anywhere -> get_openai_key() == ''. Saving a key -> returned.
    User-config key takes priority over env var."""
    # No config, no env (env is scrubbed in fixture).
    assert config.get_openai_key() == ""

    # User config with key.
    config.save_user_config({"openai_api_key": "sk-user-123"})
    assert config.get_openai_key() == "sk-user-123"

    # File is valid JSON with the key inside.
    data = json.loads(config.USER_CONFIG_FILE.read_text(encoding="utf-8"))
    assert data == {"openai_api_key": "sk-user-123"}


def test_load_user_config_handles_missing_and_invalid(tmp_ghost_home):
    """Missing file -> {}. Corrupt file -> {} (silent fallback, never raises)."""
    assert config.load_user_config() == {}

    # Write garbage — loader must swallow the error.
    config.USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    config.USER_CONFIG_FILE.write_text("{not valid json", encoding="utf-8")
    assert config.load_user_config() == {}
