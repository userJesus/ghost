"""Realtime voice agent orchestrator (BETA).

Bridges Ghost to OpenAI's Realtime API. The actual audio + events travel
via WebRTC between the browser (pywebview) and OpenAI — this service only:

  1. Mints ephemeral `client_secret` tokens server-side via
     `src.integrations.openai_realtime`. The real API key never leaves
     Python.
  2. Builds the `tools` catalog — a JSON Schema list of functions the
     model is allowed to call. Each tool maps 1:1 to an existing
     `pywebview.api.*` method the frontend can invoke directly, so the
     agent can drive Ghost (screenshot, minimize, scroll, clipboard, etc.)
     without any new logic on the Python side.
  3. Returns the persona instructions + tool catalog + token to the
     frontend, which uses them to establish the RTCPeerConnection.

Design notes:

  - The tool handlers live in the browser (see `web/js/realtime-agent.js`)
    because that's where the WebRTC data channel lives. Handlers call
    `window.pywebview.api.<method>(...)` and return the result as the
    tool call's output. This reuses every bridge method GhostAPI already
    exposes — no new per-tool Python code needed.

  - Session instructions are Portuguese (pt-BR) because the product's
    UI language is pt-BR. The persona emphasizes that the model CAN
    take actions (tool calls) and should do so proactively when the user
    asks for something Ghost can execute.
"""
from __future__ import annotations

from src.config import get_openai_key
from src.infra.logging_setup import get_logger
from src.integrations.openai_realtime import (
    DEFAULT_REALTIME_MODEL,
    DEFAULT_REALTIME_VOICE,
    mint_ephemeral_token,
)

log = get_logger(__name__)


# System instructions (pt-BR). Tells the model who it is, how to behave,
# and that it has direct access to tools that control Ghost.
AGENT_INSTRUCTIONS = (
    "Você é o Ghost, um assistente de voz em tempo real integrado ao "
    "computador Windows do usuário. Fala português brasileiro de forma "
    "natural, direta e breve — como um colega ajudando por chamada de voz. "
    "\n\n"
    "Você tem controle real do Ghost através de FERRAMENTAS (tool calls). "
    "Quando o usuário pedir algo que você pode executar, CHAME a ferramenta "
    "imediatamente — não peça confirmação para ações reversíveis (capturar "
    "tela, minimizar, maximizar, ler área de transferência). Só confirme "
    "antes de abrir URLs ou qualquer ação que envolva conteúdo externo. "
    "\n\n"
    "Depois de chamar uma ferramenta, comente BREVEMENTE o resultado "
    "(ex: 'Pronto, capturei a tela' / 'Janela maximizada'). Se a "
    "ferramenta retornar erro, explique o que falhou em uma frase curta. "
    "\n\n"
    "Quando o usuário perguntar sobre o que está na tela, primeiro chame "
    "`analyze_screen` com o preset adequado — ela tira a screenshot e "
    "pede ao GPT para analisar. Narre a análise de volta em voz. "
    "\n\n"
    "Respostas verbais: curtas (1-3 frases), sem markdown, sem listas — é "
    "áudio. Se o usuário pedir algo longo ou código, ofereça enviar no "
    "chat escrito em vez de falar tudo."
)


def build_tool_catalog() -> list[dict]:
    """Return the tool definitions passed to the realtime session.

    Shape matches OpenAI's function-calling JSON Schema — each entry is a
    `{type: "function", name, description, parameters}` object. The
    realtime API sends `response.function_call_arguments.done` events when
    the model decides to call one; the browser intercepts these and
    dispatches to the matching `pywebview.api.*` method.
    """
    return [
        {
            "type": "function",
            "name": "take_screenshot",
            "description": (
                "Captura a tela inteira do usuário (monitor atual). Use "
                "antes de analisar conteúdo visual ou quando o usuário "
                "pedir para você ver a tela."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        {
            "type": "function",
            "name": "capture_region",
            "description": (
                "Abre um seletor de região na tela para o usuário arrastar "
                "um retângulo. Use quando o usuário disser 'captura essa "
                "parte', 'só esse canto da tela', etc."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        {
            "type": "function",
            "name": "scroll_capture",
            "description": (
                "Captura a página inteira rolando até o fim. Use quando o "
                "usuário pedir para ler 'a página toda' ou 'o artigo "
                "completo'. O monitor padrão é o 1."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "monitor_index": {
                        "type": "integer",
                        "description": "Índice do monitor (use 1 se não souber).",
                        "default": 1,
                    },
                    "max_scrolls": {
                        "type": "integer",
                        "description": "Máximo de rolagens (padrão 20, até 100).",
                        "default": 20,
                    },
                },
                "required": [],
            },
        },
        {
            "type": "function",
            "name": "analyze_screen",
            "description": (
                "Captura a tela e pede ao GPT para analisar com um preset "
                "específico. Use quando o usuário perguntar 'o que essa "
                "tela está mostrando', 'me explica esse erro', 'responde "
                "essa pergunta'. Retorna o texto analisado — fale-o em voz."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "preset": {
                        "type": "string",
                        "description": (
                            "Preset de análise. Valores válidos: "
                            "'Responder pergunta', 'Explicar erro', "
                            "'Resumir conteúdo', 'Traduzir', "
                            "'Descrever livremente'."
                        ),
                        "enum": [
                            "Responder pergunta",
                            "Explicar erro",
                            "Resumir conteúdo",
                            "Traduzir",
                            "Descrever livremente",
                        ],
                    },
                    "extra_text": {
                        "type": "string",
                        "description": "Contexto adicional (opcional).",
                    },
                },
                "required": ["preset"],
            },
        },
        {
            "type": "function",
            "name": "minimize_window",
            "description": (
                "Encolhe o Ghost para um ícone de 56x56 no canto direito "
                "da tela (docking). Use quando o usuário pedir para "
                "'minimizar', 'encolher', 'tirar do caminho'. O usuário "
                "clica no ícone para restaurar o tamanho normal."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        {
            "type": "function",
            "name": "hide_window",
            "description": (
                "ESCONDE completamente a janela do Ghost. Só use se o "
                "usuário pedir explicitamente para 'sumir', 'esconder "
                "totalmente' ou 'ficar invisível'. AVISE o usuário antes "
                "que ele precisa usar Ctrl+Shift+G para trazer de volta."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        {
            "type": "function",
            "name": "maximize_window",
            "description": (
                "Maximiza o Ghost para ocupar a área útil da tela. Use "
                "quando o usuário pedir para 'abrir tela cheia', "
                "'maximizar', 'expandir'."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        {
            "type": "function",
            "name": "exit_maximized",
            "description": (
                "Sai do modo maximizado, voltando pro tamanho de janela. "
                "Use quando o usuário pedir 'deixa menor', 'sai da tela "
                "cheia'."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        {
            "type": "function",
            "name": "dock_to_edge",
            "description": (
                "Encolhe o Ghost pra um ícone de 56x56 no canto direito da "
                "tela. Use quando o usuário pedir pra 'colocar no canto', "
                "'só um pontinho'."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        {
            "type": "function",
            "name": "restore_from_edge",
            "description": (
                "Restaura o Ghost do ícone na borda pro tamanho normal. "
                "Use quando o usuário pedir pra 'trazer o Ghost de volta'."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        {
            "type": "function",
            "name": "list_windows",
            "description": (
                "Lista todas as janelas abertas visíveis no sistema. Use "
                "quando o usuário pedir 'que janelas estão abertas', "
                "'quais apps estão rodando'."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        {
            "type": "function",
            "name": "list_monitors",
            "description": "Lista os monitores disponíveis com resolução.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        {
            "type": "function",
            "name": "read_clipboard",
            "description": (
                "Lê o conteúdo de texto atual da área de transferência. "
                "Use quando o usuário disser 'olha o que eu copiei', "
                "'analisa o que tá no clipboard'."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        {
            "type": "function",
            "name": "open_url",
            "description": (
                "Abre uma URL HTTP(S) no navegador padrão do usuário. "
                "Peça CONFIRMAÇÃO antes de chamar — é uma ação externa."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "url": {
                        "type": "string",
                        "description": "URL completa (http:// ou https://).",
                    },
                },
                "required": ["url"],
            },
        },
        {
            "type": "function",
            "name": "toggle_watch",
            "description": (
                "Ativa/desativa o modo Vigiar (captura periódica da tela "
                "em background). Use quando o usuário pedir pra 'ficar "
                "olhando a tela' ou 'parar de vigiar'."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "enabled": {
                        "type": "boolean",
                        "description": "true pra ativar, false pra desativar.",
                    },
                    "interval": {
                        "type": "number",
                        "description": "Intervalo em segundos (padrão 3).",
                        "default": 3.0,
                    },
                },
                "required": ["enabled"],
            },
        },
        {
            "type": "function",
            "name": "start_window_drag",
            "description": (
                "Inicia arrasto da janela do Ghost. Use quando o usuário "
                "pedir 'move a janela', 'arrasta pra esse canto'."
            ),
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
        {
            "type": "function",
            "name": "set_capture_visibility",
            "description": (
                "Define se o Ghost aparece em screen share / captura. "
                "Útil quando o usuário quer mostrar algo em reunião sem "
                "que o Ghost apareça."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "visible": {
                        "type": "boolean",
                        "description": (
                            "true = visível na captura, "
                            "false = invisível (WDA_EXCLUDEFROMCAPTURE)."
                        ),
                    },
                },
                "required": ["visible"],
            },
        },
    ]


class RealtimeAgentService:
    """Realtime voice-agent orchestrator (BETA).

    Reads the OpenAI API key from user config, mints an ephemeral token,
    and hands the frontend everything it needs to open the WebRTC session.
    """

    def __init__(self):
        self._active = False

    def create_session(self) -> dict:
        """Mint a short-lived token + return session config for the browser.

        Returns:
          {ok: True, token, model, voice, instructions, tools, expires_at}
            on success — the browser immediately establishes WebRTC with this.
          {error: "..."} on failure.
        """
        key = get_openai_key()
        if not key:
            return {"error": "Configure a chave da OpenAI em Configurações"}

        result = mint_ephemeral_token(
            api_key=key,
            model=DEFAULT_REALTIME_MODEL,
            voice=DEFAULT_REALTIME_VOICE,
            instructions=AGENT_INSTRUCTIONS,
            tools=build_tool_catalog(),
        )
        if "error" in result:
            return result

        self._active = True
        log.info(
            "realtime session minted: model=%s voice=%s expires_at=%s",
            result.get("model"), result.get("voice"), result.get("expires_at"),
        )
        return {
            "ok": True,
            "token": result["token"],
            "model": result["model"],
            "voice": result["voice"],
            "expires_at": result["expires_at"],
            "instructions": AGENT_INSTRUCTIONS,
            "tools": build_tool_catalog(),
        }

    def end_session(self) -> dict:
        """Mark the session as no longer active. The browser tears down
        the WebRTC peer on its own — this is just for our local bookkeeping
        (e.g. preventing double-start when the user mashes the button)."""
        self._active = False
        return {"ok": True}

    def is_active(self) -> bool:
        return self._active
