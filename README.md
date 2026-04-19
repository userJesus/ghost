# 👻 Ghost

Popup flutuante que captura a tela e envia para o GPT analisar.

## Instalação

```bash
cd D:\ghost
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Copie `.env.example` para `.env` e coloque sua `OPENAI_API_KEY` (já está configurada).

## Executar

**Modo console (com logs):**
```bash
python main.py
```

**Modo silencioso (sem console):**
Dê duplo clique em `run.bat` ou execute:
```bash
pythonw main.py
```

## Como usar

1. A janela flutuante aparece sempre no topo. Arraste pelo topo para mover.
2. Escolha um preset (Responder pergunta, Explicar erro, Traduzir, etc.) ou selecione "Prompt customizado" e digite.
3. Clique em **📷 Tela inteira** ou **✂️ Selecionar área**.
4. Para "Selecionar área": arraste com o mouse para definir a região. `ESC` cancela.
5. A resposta aparece na caixa de texto e é copiada automaticamente para o clipboard.

## Stack

- PySide6 (Qt) — UI flutuante frameless
- mss — captura de tela
- openai — GPT-4.1-mini com vision
- pyperclip — clipboard
- python-dotenv — variáveis de ambiente

## Estrutura

```
ghost/
├── main.py
├── run.bat
├── requirements.txt
├── .env
├── src/
│   ├── config.py       # env + presets de prompt
│   ├── capture.py      # captura de tela e seletor de região
│   ├── gpt_client.py   # wrapper OpenAI Vision
│   └── ui.py           # janela PySide6
```

## Modelo

Usando `gpt-4.1-mini` por padrão. Para trocar, edite `.env`:
```
OPENAI_MODEL=gpt-4o
```
