"""Settings service — OpenAI key management + model selection.

Extracted from the pre-refactor GhostAPI methods:
  * get_settings()
  * set_openai_model()
  * save_openai_key()
  * clear_openai_key()

Behavior is byte-for-byte identical. The facade (`GhostAPI`) still catches
exceptions and converts them to `{"error": str}` for the bridge — services
let them propagate.
"""
from __future__ import annotations

from src.config import (
    SUPPORTED_MODEL_IDS,
    SUPPORTED_MODELS,
    get_openai_key,
    get_openai_model,
    load_user_config,
    save_user_config,
)
from src.infra.logging_setup import get_logger

log = get_logger(__name__)


class SettingsService:
    """OpenAI credential + model management."""

    def get_settings(self) -> dict:
        """Return current settings — API key is masked, never exposed raw."""
        key = get_openai_key()
        masked = ""
        if key:
            masked = key[:7] + "..." + key[-4:] if len(key) > 10 else "***"
        return {
            "has_openai_key": bool(key),
            "masked_key": masked,
            "openai_model": get_openai_model(),
            "available_models": SUPPORTED_MODELS,
        }

    def set_openai_model(self, model_id: str) -> dict:
        """Save user's model choice. Must be in SUPPORTED_MODEL_IDS."""
        mid = (model_id or "").strip()
        if mid not in SUPPORTED_MODEL_IDS:
            return {"error": f"Modelo não suportado: {mid}"}
        cfg = load_user_config()
        cfg["openai_model"] = mid
        save_user_config(cfg)
        return {"ok": True, "openai_model": mid}

    def save_openai_key(self, key: str, replace_existing: bool = False) -> dict:
        """Validate the key and save it.

        Three tests:
          1. Basic access (models.list)
          2. Chat completions (tiny 1-token call — cost ~$0.0000003)
          3. Audio/Whisper endpoint availability (best-effort via model list)
        """
        key = (key or "").strip()
        if not key:
            return {"error": "Chave vazia"}
        if not key.startswith("sk-"):
            return {"error": "Formato inválido — deve começar com 'sk-'"}

        current = get_openai_key()
        if current and not replace_existing and current != key:
            return {
                "error": "Já existe uma chave configurada. Remova a atual antes de adicionar outra.",
                "replace_required": True,
            }

        from openai import OpenAI
        client = OpenAI(api_key=key, timeout=15.0)

        # Test 1: basic access
        try:
            models = client.models.list()
            _ = next(iter(models), None)
        except Exception as e:
            err_str = str(e)
            if "401" in err_str or "Incorrect API key" in err_str:
                return {"error": "Chave rejeitada pela OpenAI (401 - inválida)"}
            return {"error": f"Falha ao validar chave: {err_str[:200]}"}

        # Test 2: chat permission (costs ~$0.0000003)
        chat_ok = False
        chat_err = None
        try:
            client.chat.completions.create(
                model="gpt-4.1-mini",
                messages=[{"role": "user", "content": "ok"}],
                max_tokens=1,
            )
            chat_ok = True
        except Exception as e:
            chat_err = str(e)

        if not chat_ok:
            if chat_err and "insufficient_quota" in chat_err:
                return {
                    "error": "Chave válida mas SEM créditos. Adicione saldo em platform.openai.com/billing",
                }
            if chat_err and ("403" in chat_err or "permission" in chat_err.lower()
                             or "insufficient permissions" in chat_err.lower()):
                return {
                    "error": "Chave com RESTRIÇÕES: permissão de chat.completions desabilitada. "
                             "Crie uma chave com 'All' permissions ou habilite 'Model capabilities: Write'.",
                    "permissions": {"models": True, "chat": False, "audio": "unknown"},
                }
            return {"error": f"Chat falhou: {(chat_err or 'erro desconhecido')[:200]}"}

        # Test 3: audio permission via heuristic — whisper-1 appears in the
        # account's model list if audio is enabled for this key.
        audio_ok = True
        try:
            model_ids: list[str] = []
            for m in client.models.list():
                mid = getattr(m, "id", "") or ""
                model_ids.append(mid)
                if len(model_ids) > 200:
                    break
            if "whisper-1" not in model_ids:
                audio_ok = False
        except Exception:
            audio_ok = True  # don't block save on this heuristic

        # Save
        cfg = load_user_config()
        cfg["openai_api_key"] = key
        save_user_config(cfg)

        warnings: list[str] = []
        if not audio_ok:
            warnings.append(
                "Permissão de Whisper não detectada — gravação de reuniões pode falhar."
            )

        return {
            "ok": True,
            "permissions": {
                "models": True,
                "chat": True,
                "audio": audio_ok,
            },
            "warnings": warnings,
        }

    def clear_openai_key(self) -> dict:
        """Remove the stored API key."""
        cfg = load_user_config()
        cfg.pop("openai_api_key", None)
        save_user_config(cfg)
        return {"ok": True}
