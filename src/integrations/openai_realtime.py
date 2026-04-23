"""OpenAI Realtime — ephemeral session/token minting.

The actual realtime voice session is established via WebRTC between the
browser (pywebview) and OpenAI's `/v1/realtime` endpoint. This module only
handles the server-side handshake: it POSTs the real API key to OpenAI's
`/v1/realtime/client_secrets` endpoint and receives a short-lived
`client_secret` token (~60s TTL) that the browser can safely use to
negotiate the WebRTC peer connection. The real API key never leaves
Python.

References:
  - https://platform.openai.com/docs/guides/realtime
  - https://platform.openai.com/docs/api-reference/realtime-sessions
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request

from src.infra.logging_setup import get_logger

log = get_logger(__name__)

# Default realtime model. `gpt-realtime` is the GA release — cheaper than
# the older `gpt-4o-realtime-preview` ($32/$64 vs $100/$200 per 1M audio
# tokens) and higher-quality voice. Supports vision inputs natively via
# `conversation.item.create` with image content parts, which opens the
# door to collapsing the "voice agent + separate vision sub-agent" loop
# into a single model call once the automation tools are in.
DEFAULT_REALTIME_MODEL = "gpt-realtime"

# Default TTS voice for OpenAI Realtime (alloy, ash, ballad, coral, echo,
# sage, shimmer, verse). 'coral' soa natural em pt-BR.
DEFAULT_REALTIME_VOICE = "coral"


def mint_ephemeral_token(
    api_key: str,
    model: str = DEFAULT_REALTIME_MODEL,
    voice: str = DEFAULT_REALTIME_VOICE,
    instructions: str = "",
    tools: list[dict] | None = None,
    timeout: float = 10.0,
) -> dict:
    """Mint a short-lived client_secret for browser-side WebRTC.

    Returns {ok: True, token, model, voice, expires_at} on success.
    Returns {error: "..."} on failure (network, auth, quota, etc.).

    The returned token is scoped to a single realtime session. The browser
    will use it in the Authorization header of its SDP offer POST. OpenAI
    rotates these tokens automatically every ~60s while the session stays
    alive — expiration doesn't kill an in-progress call, only new offers.
    """
    if not api_key or not isinstance(api_key, str):
        return {"error": "API key inválida"}

    body = {
        "session": {
            "type": "realtime",
            "model": model,
            "audio": {
                "output": {"voice": voice},
            },
        },
    }
    # Session-level instructions + tool catalog get applied when the browser
    # sends its `session.update` event. We still include them here so the
    # first utterance already has the persona loaded (avoids a blank-slate
    # greeting before session.update lands).
    if instructions:
        body["session"]["instructions"] = instructions
    if tools:
        body["session"]["tools"] = tools

    data = json.dumps(body).encode("utf-8")
    req = urllib.request.Request(
        "https://api.openai.com/v1/realtime/client_secrets",
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "User-Agent": "Ghost/realtime",
        },
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
            parsed = json.loads(raw.decode("utf-8"))
    except urllib.error.HTTPError as e:
        try:
            err_body = e.read().decode("utf-8", errors="replace")
        except Exception:
            err_body = ""
        log.warning("realtime token mint HTTP %s: %s", e.code, err_body[:300])
        if e.code == 401:
            return {"error": "Chave OpenAI rejeitada (401)"}
        if e.code == 403:
            return {"error": "Chave sem permissão para Realtime API"}
        if e.code == 429:
            return {"error": "Sem créditos ou limite atingido na OpenAI"}
        return {"error": f"OpenAI HTTP {e.code}: {err_body[:200]}"}
    except urllib.error.URLError as e:
        log.warning("realtime token mint network error: %s", e)
        return {"error": f"Falha de rede: {e.reason}"}
    except Exception as e:
        log.exception("realtime token mint unexpected error")
        return {"error": f"{type(e).__name__}: {e}"}

    # Response shape (OpenAI):
    #   { "value": "ek_...", "expires_at": 1234567890, "session": {...} }
    token = parsed.get("value") or ""
    expires_at = parsed.get("expires_at") or 0
    if not token:
        return {"error": "Resposta da OpenAI sem client_secret"}

    return {
        "ok": True,
        "token": token,
        "model": model,
        "voice": voice,
        "expires_at": expires_at,
    }
