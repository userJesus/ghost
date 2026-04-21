"""Tests for src/services/settings_service.py.

Covers get_settings masking, set_openai_model validation, save_openai_key
guard rails (empty, wrong prefix, re-add without replace), and clear.
"""
from __future__ import annotations

from src.services.settings_service import SettingsService


class TestGetSettings:
    def test_no_key_returns_masked_empty(self, tmp_ghost_home):
        svc = SettingsService()
        r = svc.get_settings()
        assert r["has_openai_key"] is False
        assert r["masked_key"] == ""
        assert isinstance(r["available_models"], list)
        assert len(r["available_models"]) > 0

    def test_long_key_is_masked_to_prefix_ellipsis_suffix(self, tmp_ghost_home):
        from src.config import save_user_config
        save_user_config({"openai_api_key": "sk-proj-abcdefghijklmnopqrstuvwxyz1234"})
        svc = SettingsService()
        r = svc.get_settings()
        assert r["has_openai_key"] is True
        # shape: first 7 chars + "..." + last 4 chars
        assert r["masked_key"].startswith("sk-proj")
        assert "..." in r["masked_key"]
        assert r["masked_key"].endswith("1234")
        # Full key must NOT leak
        assert "abcdef" not in r["masked_key"]

    def test_short_key_masked_to_triple_asterisk(self, tmp_ghost_home):
        from src.config import save_user_config
        save_user_config({"openai_api_key": "sk-short"})
        svc = SettingsService()
        r = svc.get_settings()
        assert r["masked_key"] == "***"


class TestSetOpenaiModel:
    def test_unknown_model_rejected(self, tmp_ghost_home):
        svc = SettingsService()
        r = svc.set_openai_model("gpt-fake-nonexistent")
        assert "error" in r
        assert "não suportado" in r["error"]

    def test_valid_model_saved(self, tmp_ghost_home):
        svc = SettingsService()
        r = svc.set_openai_model("gpt-4.1-mini")
        assert r.get("ok") is True
        assert r.get("openai_model") == "gpt-4.1-mini"
        # Persists
        r2 = svc.get_settings()
        assert r2["openai_model"] == "gpt-4.1-mini"

    def test_empty_model_id_rejected(self, tmp_ghost_home):
        svc = SettingsService()
        r = svc.set_openai_model("")
        assert "error" in r


class TestSaveOpenaiKey:
    """The validation stages that DON'T require a real OpenAI key / network."""

    def test_empty_key_rejected(self, tmp_ghost_home):
        svc = SettingsService()
        r = svc.save_openai_key("")
        assert r["error"] == "Chave vazia"

    def test_wrong_prefix_rejected(self, tmp_ghost_home):
        svc = SettingsService()
        r = svc.save_openai_key("not-a-real-key")
        assert "Formato inválido" in r["error"]

    def test_already_configured_blocks_without_replace_flag(self, tmp_ghost_home):
        from src.config import save_user_config
        save_user_config({"openai_api_key": "sk-existing-key-123"})
        svc = SettingsService()
        r = svc.save_openai_key("sk-brand-new-key-456", replace_existing=False)
        assert "replace_required" in r and r["replace_required"] is True


class TestClearKey:
    def test_clear_removes_key(self, tmp_ghost_home):
        from src.config import load_user_config, save_user_config
        save_user_config({"openai_api_key": "sk-to-remove", "openai_model": "gpt-4.1-mini"})
        svc = SettingsService()
        r = svc.clear_openai_key()
        assert r["ok"] is True
        cfg = load_user_config()
        assert "openai_api_key" not in cfg
        # Other settings survive
        assert cfg.get("openai_model") == "gpt-4.1-mini"
