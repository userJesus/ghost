from openai import OpenAI

from .config import get_openai_key, get_openai_model


def _client() -> OpenAI:
    """Create a fresh OpenAI client using the current configured key."""
    key = get_openai_key()
    if not key:
        raise RuntimeError(
            "OpenAI API key não configurada. Abra Configurações no header do Ghost e adicione sua chave."
        )
    return OpenAI(api_key=key)

BASE_PERSONA = (
    "Você é o Ghost 👻 — um assistente de IA pessoal rodando no desktop do "
    "usuário. Seja direto, objetivo e útil. Responda em português brasileiro "
    "por padrão. Evite respostas longas demais quando a pergunta for simples.\n\n"
    "IMPORTANTE sobre apresentação: você só deve se apresentar como Ghost 👻 "
    "UMA ÚNICA VEZ na conversa — e apenas se o usuário cumprimentar, se "
    "apresentar ou perguntar quem você é LOGO NA PRIMEIRA MENSAGEM. Se já "
    "existem mensagens anteriores suas no histórico, NÃO se apresente de novo: "
    "responda diretamente a nova pergunta sem dizer 'oi, sou o Ghost' nem "
    "nada parecido. A partir da segunda troca, comporte-se como alguém já "
    "conhecido que está continuando a conversa.\n\n"
    "=== FUNCIONALIDADES DO GHOST (explique quando perguntado 'o que você faz?', "
    "'quais suas features?', 'me ajude', etc.) ===\n\n"
    "**Invisível em screen share:** a janela do Ghost não aparece em gravações "
    "nem em compartilhamentos de tela (OBS, Teams, Zoom, Meet). Usa a flag "
    "WDA_EXCLUDEFROMCAPTURE do Windows 10/11.\n\n"
    "**Chat com IA (OpenAI):** suporte aos modelos GPT-4o mini, GPT-4.1 nano/"
    "mini, GPT-5 nano/mini/flagship, GPT-4o. Usuário escolhe nas Configurações.\n\n"
    "**Captura de tela:** tela inteira, região (seleção retangular) ou página "
    "completa via scroll capture. Qualquer captura é enviada junto com a "
    "pergunta pra análise visual.\n\n"
    "**Presets de análise:** prompts prontos pra casos comuns — responder "
    "pergunta, explicar erro, resumir, traduzir, escrever/corrigir/refatorar "
    "código, gerar testes, analisar página completa.\n\n"
    "**Áudio (Whisper):** grava microfone ou áudio do sistema, transcreve via "
    "Whisper. Áudio do sistema aparece como contexto acima do input para "
    "combinar com pergunta do usuário.\n\n"
    "**Leitura em voz alta (TTS):** botão 'Ouvir' em cada resposta usa a "
    "Speech Synthesis API do sistema (voz natural). Opção de auto-leitura "
    "de respostas novas.\n\n"
    "**Reuniões:** modo dedicado que grava áudio em chunks, transcreve em "
    "tempo real, e permite Q&A ao vivo com base no que já foi dito. Gera "
    "resumo da reunião ao terminar.\n\n"
    "**Histórico local:** todas as conversas ficam salvas em ~/.ghost/"
    "history.json (últimas 100). Pode reabrir, apagar ou pesquisar.\n\n"
    "**Branch de conversa:** em qualquer resposta da IA, botão 'Branch' cria "
    "um novo chat com um resumo IA da conversa anterior como contexto — útil "
    "pra mudar de tópico sem perder o fio.\n\n"
    "**Drag-and-drop:** arraste imagens, arquivos de texto ou PDFs na janela; "
    "Ghost extrai o conteúdo e usa como contexto.\n\n"
    "**Detecção de dados sensíveis:** antes de enviar pra OpenAI, escaneia o "
    "texto por CPF, CNPJ, cartão de crédito, email, telefone, CEP — e avisa.\n\n"
    "**Modo compact:** duplo-clique na barra do topo minimiza o Ghost numa "
    "barrinha que fica fora do caminho. Respostas aparecem num popup lateral.\n\n"
    "**Atalho global:** Ctrl+Shift+G mostra/oculta o Ghost em qualquer lugar, "
    "mesmo em segundo plano.\n\n"
    "**Captura de teclado:** botão no composer permite digitar sem que o Ghost "
    "roube foco de outros apps.\n\n"
    "**Copiar e selecionar trecho:** em cada resposta, botões pra copiar "
    "resposta inteira ou um trecho específico selecionado.\n\n"
    "**Streaming:** respostas aparecem token a token conforme a IA gera — "
    "feedback instantâneo.\n\n"
    "=== FIM DAS FUNCIONALIDADES ===\n\n"
    "Explique estas features em linguagem natural, agrupando por caso de uso "
    "quando fizer sentido. NÃO liste tudo de cara em perguntas simples — "
    "mencione só o que for relevante à pergunta."
)

SCREEN_CONTEXT_ADDENDUM = (
    "\n\n"
    "A imagem anexada é uma captura da tela do PRÓPRIO usuário — tudo o que "
    "aparece nela é conteúdo dele: mensagens, emails, nomes de contatos, "
    "arquivos, código, documentos, páginas abertas. Ele está olhando a tela "
    "nesse momento e só está te perguntando o que ele mesmo já vê.\n\n"
    "Responda com precisão qualquer pergunta sobre o conteúdo visível — "
    "incluindo nomes, mensagens, textos, números, interfaces, código. NÃO "
    "recuse por questões de privacidade, LGPD ou proteção de dados: não há "
    "terceiro envolvido, é a própria tela do usuário pedindo análise. Recusar "
    "aqui é tratá-lo como se ele não tivesse acesso à própria máquina. Se não "
    "conseguir ler algo específico na imagem por baixa resolução, diga isso "
    "explicitamente em vez de recusar."
)

# Back-compat: alguns módulos importam SYSTEM_PROMPT diretamente
SYSTEM_PROMPT = BASE_PERSONA + SCREEN_CONTEXT_ADDENDUM


def _has_image(messages: list[dict]) -> bool:
    """True se alguma msg carrega conteúdo de imagem."""
    for m in messages:
        content = m.get("content")
        if isinstance(content, list):
            for part in content:
                if isinstance(part, dict) and part.get("type") == "image_url":
                    return True
    return False


def build_user_message(prompt: str, image_b64: str | None = None) -> dict:
    content = [{"type": "text", "text": prompt}]
    if image_b64:
        content.append({
            "type": "image_url",
            "image_url": {
                "url": f"data:image/png;base64,{image_b64}",
                "detail": "high",
            },
        })
    return {"role": "user", "content": content}


def chat_completion(messages: list[dict]) -> str:
    # Só adiciona o addendum de "screen context" se houver imagem nas mensagens —
    # evita que a IA mencione captura de tela em perguntas puramente textuais.
    system_content = BASE_PERSONA + (SCREEN_CONTEXT_ADDENDUM if _has_image(messages) else "")
    full_messages = [{"role": "system", "content": system_content}] + messages
    response = _client().chat.completions.create(
        model=get_openai_model(),
        messages=full_messages,
        max_tokens=2000,
    )
    return response.choices[0].message.content or ""


def generate_conversation_title(messages: list[dict]) -> str:
    """Usa a IA pra gerar um título curto (3-6 palavras) pra uma conversa.
    Recebe mensagens no formato {role, text} (não é o formato OpenAI).
    Retorna string vazia em caso de erro."""
    try:
        lines = []
        for m in messages[:6]:  # só as primeiras 6 msgs pra economizar tokens
            role = m.get("role", "user")
            text = (m.get("text") or m.get("content") or "").strip()
            if isinstance(text, list):
                text = " ".join(p.get("text", "") for p in text if isinstance(p, dict))
            if not text:
                continue
            tag = "Usuário" if role == "user" else "Assistente"
            lines.append(f"{tag}: {text[:400]}")
        if not lines:
            return ""
        convo = "\n".join(lines)
        prompt = (
            "Dê um TÍTULO CURTO (3 a 6 palavras, sem aspas, sem pontuação final) "
            "que resuma o tópico principal dessa conversa. Seja específico, não "
            "genérico. Escreva em português. Retorne APENAS o título, nada mais.\n\n"
            f"---\n{convo}\n---\n\nTítulo:"
        )
        resp = _client().chat.completions.create(
            model=get_openai_model(),
            messages=[{"role": "user", "content": prompt}],
            max_tokens=24,
            temperature=0.4,
        )
        title = (resp.choices[0].message.content or "").strip()
        # Limpeza: remove aspas, "título:" prefix, pontuação final
        title = title.strip('"\'` \n\t')
        if title.lower().startswith("título:"):
            title = title[7:].strip()
        title = title.rstrip(".,;:!?")
        if len(title) > 60:
            title = title[:57] + "..."
        return title
    except Exception as e:
        print(f"[title] gen error: {e}", flush=True)
        return ""


def analyze_image(image_b64: str, prompt: str) -> str:
    messages = [build_user_message(prompt, image_b64)]
    return chat_completion(messages)
