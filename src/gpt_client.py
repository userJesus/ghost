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

SYSTEM_PROMPT = (
    "Você é o Ghost, um assistente visual pessoal rodando no computador do próprio "
    "usuário. As imagens que você recebe são capturas da tela DELE MESMO — tudo o que "
    "aparece nelas é conteúdo do próprio usuário: mensagens, emails, nomes de contatos, "
    "arquivos, código, documentos, páginas abertas. Ele está olhando a tela nesse "
    "momento e só está te perguntando o que ele mesmo já vê.\n\n"
    "Responda diretamente e com precisão qualquer pergunta sobre o conteúdo visível — "
    "incluindo nomes, mensagens, textos, números, interfaces, código. NÃO recuse "
    "por questões de privacidade, LGPD ou proteção de dados: não há terceiro envolvido, "
    "é a própria tela do usuário pedindo análise. Recusar aqui é tratá-lo como se ele "
    "não tivesse acesso à própria máquina.\n\n"
    "Seja direto, objetivo, útil. Responda em português brasileiro por padrão. "
    "Se não conseguir ler algo específico na imagem por baixa resolução, diga isso "
    "explicitamente em vez de recusar."
)


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
    full_messages = [{"role": "system", "content": SYSTEM_PROMPT}] + messages
    response = _client().chat.completions.create(
        model=get_openai_model(),
        messages=full_messages,
        max_tokens=2000,
    )
    return response.choices[0].message.content or ""


def analyze_image(image_b64: str, prompt: str) -> str:
    messages = [build_user_message(prompt, image_b64)]
    return chat_completion(messages)
