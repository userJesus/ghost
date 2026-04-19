import json
import os
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")

# Default model (usado se config.json não tiver 'openai_model')
OPENAI_MODEL_DEFAULT = os.getenv("OPENAI_MODEL", "gpt-4.1-mini").strip()

# Modelos suportados — todos precisam ser compatíveis com:
#  - chat.completions (text + vision)
#  - response_format json_object
#  - max_tokens
#  - system + user messages
# Preços: input/output por 1M tokens (USD), conforme tabela OpenAI.
# Ordem: economia → balanceados → flagship.
SUPPORTED_MODELS = [
    # --- Economy (baratos, ainda multimodais) ---
    {
        "id": "gpt-5-nano",
        "name": "GPT-5 nano",
        "description": "Modelo mais leve da família 5. Rápido e ultra barato.",
        "tier": "economy",
        "input_price": 0.05,
        "output_price": 0.40,
    },
    {
        "id": "gpt-4.1-nano",
        "name": "GPT-4.1 nano",
        "description": "Menor da família 4.1. Barato, rápido, ainda multimodal.",
        "tier": "economy",
        "input_price": 0.10,
        "output_price": 0.40,
    },
    {
        "id": "gpt-4o-mini",
        "name": "GPT-4o mini",
        "description": "Multimodal econômico da geração 4o. Bom pro dia a dia.",
        "tier": "economy",
        "input_price": 0.15,
        "output_price": 0.60,
    },
    # --- Balanced (melhor custo-benefício) ---
    {
        "id": "gpt-5-mini",
        "name": "GPT-5 mini",
        "description": "Geração 5 em versão intermediária. Resposta ágil + qualidade alta.",
        "tier": "balanced",
        "input_price": 0.25,
        "output_price": 2.00,
    },
    {
        "id": "gpt-4.1-mini",
        "name": "GPT-4.1 mini",
        "description": "Equilíbrio entre custo e qualidade. Padrão anterior.",
        "tier": "balanced",
        "input_price": 0.40,
        "output_price": 1.60,
    },
    # --- Flagship (máxima qualidade) ---
    {
        "id": "gpt-5",
        "name": "GPT-5",
        "description": "Flagship de raciocínio + código + vision. Estado da arte.",
        "tier": "flagship",
        "input_price": 1.25,
        "output_price": 10.00,
    },
    {
        "id": "gpt-4.1",
        "name": "GPT-4.1",
        "description": "Melhor no código da geração 4. Contexto longo (1M).",
        "tier": "flagship",
        "input_price": 2.00,
        "output_price": 8.00,
    },
    {
        "id": "gpt-4o",
        "name": "GPT-4o",
        "description": "Flagship multimodal rápido. Legado mas sólido.",
        "tier": "flagship",
        "input_price": 2.50,
        "output_price": 10.00,
    },
]
# Enriquece cada modelo com custo estimado por 100 perguntas (base: 2000 in + 500 out tokens).
# Ajuda o usuário a comparar de forma concreta em vez de ler "$/1M tokens".
def _enrich_model(m: dict) -> dict:
    cost = (2000 * m["input_price"] + 500 * m["output_price"]) / 1_000_000 * 100
    m["cost_per_100"] = round(cost, 3)
    m["recommended"] = m["id"] == "gpt-5-mini"
    return m

SUPPORTED_MODELS = [_enrich_model(m) for m in SUPPORTED_MODELS]
SUPPORTED_MODEL_IDS = {m["id"] for m in SUPPORTED_MODELS}

# User config lives in ~/.ghost/config.json and takes priority over .env.
USER_CONFIG_DIR = Path.home() / ".ghost"
USER_CONFIG_FILE = USER_CONFIG_DIR / "config.json"


def load_user_config() -> dict:
    """Read ~/.ghost/config.json, return {} if missing/invalid."""
    try:
        if USER_CONFIG_FILE.exists():
            return json.loads(USER_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception:
        pass
    return {}


def save_user_config(data: dict) -> None:
    USER_CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    USER_CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


def get_openai_key() -> str:
    """Prefer user's saved key, fall back to .env, empty string if neither."""
    cfg = load_user_config()
    key = (cfg.get("openai_api_key") or "").strip()
    if key:
        return key
    return os.getenv("OPENAI_API_KEY", "").strip()


def get_openai_model() -> str:
    """Prefer user's saved model choice; fall back to .env or default.
    Só retorna modelos em SUPPORTED_MODEL_IDS (evita model incompatível)."""
    cfg = load_user_config()
    saved = (cfg.get("openai_model") or "").strip()
    if saved and saved in SUPPORTED_MODEL_IDS:
        return saved
    if OPENAI_MODEL_DEFAULT in SUPPORTED_MODEL_IDS:
        return OPENAI_MODEL_DEFAULT
    return "gpt-4.1-mini"


# Back-compat constant. Computed lazily via the function for per-call freshness.
# Do NOT rely on this at import time — read get_openai_key() / get_openai_model()
# in API call paths.
OPENAI_API_KEY = get_openai_key()
OPENAI_MODEL = get_openai_model()

CODE_INSTRUCTION = (
    "\n\nIMPORTANTE: estruture sua resposta EXATAMENTE neste formato:\n\n"
    "## Explicação\n"
    "(explicação breve do que o código faz e por que)\n\n"
    "## Código\n"
    "```<linguagem>\n"
    "(código completo, pronto para copiar e colar, sem comentários desnecessários)\n"
    "```\n\n"
    "## Como usar\n"
    "(instruções curtas de uso, se aplicável)"
)

PRESETS = {
    "Responder pergunta": (
        "A imagem mostra uma pergunta, formulário, ou questão. "
        "Identifique a pergunta e forneça a resposta mais precisa e completa possível. "
        "Se houver alternativas (múltipla escolha), indique qual é a correta e explique por quê. "
        "Responda em português."
    ),
    "Explicar erro": (
        "A imagem mostra uma mensagem de erro, stack trace ou tela de falha. "
        "Explique o que significa o erro, qual a causa provável e como resolver. "
        "Se a solução envolver código, retorne o código COMPLETO da correção."
        + CODE_INSTRUCTION
    ),
    "Resumir conteúdo": (
        "Resuma o conteúdo principal dessa tela em bullets curtos. "
        "Destaque informações importantes. Responda em português."
    ),
    "Traduzir": (
        "Traduza todo o texto visível nessa imagem para português brasileiro. "
        "Mantenha formatação e estrutura quando possível."
    ),
    "💻 Escrever código": (
        "A imagem descreve um problema, requisito ou funcionalidade a ser implementada. "
        "Escreva o código COMPLETO que resolve o problema, pronto para uso em produção. "
        "Inclua imports necessários e tratamento de erros."
        + CODE_INSTRUCTION
    ),
    "💻 Corrigir código": (
        "A imagem mostra código com bugs ou problemas. "
        "Identifique os bugs e retorne a versão CORRIGIDA e COMPLETA do código. "
        "Não retorne apenas o trecho corrigido — retorne o arquivo inteiro com as correções aplicadas."
        + CODE_INSTRUCTION
    ),
    "💻 Refatorar código": (
        "A imagem mostra código que precisa ser melhorado. "
        "Refatore para ficar mais limpo, legível e idiomático, mantendo o comportamento. "
        "Retorne o código COMPLETO refatorado."
        + CODE_INSTRUCTION
    ),
    "💻 Explicar código": (
        "A imagem mostra código-fonte. Analise em detalhes: o que faz, como funciona "
        "linha por linha (para partes complexas), possíveis bugs, e sugestões de melhoria. "
        "Responda em português."
    ),
    "💻 Converter linguagem": (
        "A imagem mostra código em alguma linguagem. Converta para Python (ou para a linguagem "
        "que fizer mais sentido pelo contexto). Mantenha o comportamento idêntico."
        + CODE_INSTRUCTION
    ),
    "💻 Gerar testes": (
        "A imagem mostra código que precisa de testes. Gere testes unitários completos "
        "cobrindo casos normais, casos extremos e casos de erro. Use o framework de testes "
        "idiomático da linguagem (pytest para Python, Jest para JS, etc)."
        + CODE_INSTRUCTION
    ),
    "📜 Analisar página completa": (
        "A imagem mostra uma página web/documento capturada inteira via scroll. "
        "Analise TODO o conteúdo visível e retorne: "
        "(1) um resumo estruturado dos pontos principais, "
        "(2) informações relevantes que encontrou, "
        "(3) se houver perguntas ou CTAs, identifique-os. "
        "Responda em português com markdown e bullets."
    ),
    "Descrever livremente": (
        "Descreva o que você vê nessa tela de forma detalhada. "
        "Responda em português."
    ),
}

CODE_PRESETS = {
    "Explicar erro",
    "💻 Escrever código",
    "💻 Corrigir código",
    "💻 Refatorar código",
    "💻 Converter linguagem",
    "💻 Gerar testes",
}
