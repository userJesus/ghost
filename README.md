# 👻 Ghost

> Assistente de IA desktop que **desaparece em screen share**. Feito para quem precisa pensar em voz alta sem revelar o que está na tela.

[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Platform: Windows](https://img.shields.io/badge/platform-Windows%2010%2F11-lightgrey.svg)](#)
[![Build](https://img.shields.io/badge/build-passing-brightgreen.svg)](#)

## ✨ O que é

Ghost é um assistente de IA flutuante para desktop que resolve um problema específico: **conversar com uma IA sem que a janela apareça quando você compartilha a tela**.

Usando a flag `WDA_EXCLUDEFROMCAPTURE` do Windows, a janela do Ghost é filtrada no nível do sistema antes de qualquer software de captura (Zoom, Teams, Meet, OBS, Discord) ver o frame. Para você, está lá. Para quem está do outro lado da chamada, simplesmente não existe.

Por baixo: `pywebview` + Python + Alpine.js na UI, OpenAI API no cérebro, Whisper para transcrição e Fluent Design no visual.

## 🎯 Features

- **Chat com IA** — conversas com modelos OpenAI, streaming de respostas em tempo real
- **Captura de tela** — tela inteira ou seleção de região arrastando o mouse
- **Transcrição de áudio** — microfone + áudio do sistema via Whisper (útil em reuniões)
- **Gravação de reuniões** — captura contínua com transcrição automática
- **TTS** — respostas faladas em voz natural
- **Histórico local** — todas as conversas salvas no disco, sem cloud
- **Drag-and-drop** — arraste imagens ou arquivos direto na janela
- **Detecção de info sensível** — avisa antes de enviar conteúdo que parece ser credencial
- **Modo compacto** — reduz a janela a uma barra fina quando não está em uso
- **Fluent Design** — acrílico, Mica e animações nativas do Windows 11
- **Invisível em screen share** — o diferencial que motiva o projeto

## 📸 Screenshots

![Ghost](docs/screenshots/main.png)

> As capturas de interface completas e o guia visual de usabilidade estão em [`docs/ghost-usabilidade.pdf`](docs/ghost-usabilidade.pdf).

## 🚀 Começando

### Pré-requisitos

- Python 3.12 ou superior
- Windows 10 versão 2004 (build 19041) ou superior — a flag de exclusão de captura depende disso
- Uma chave de API da OpenAI

### Instalação

```bash
git clone https://github.com/jesusoliveira/ghost.git
cd ghost

python -m venv .venv
.venv\Scripts\activate

pip install -r requirements.txt

copy .env.example .env
# edite .env e coloque sua OPENAI_API_KEY

python main.py
```

Para rodar sem console (janela silenciosa), use `pythonw main.py` ou dê duplo clique em `run.bat`.

## ⌨️ Atalhos

| Atalho | Contexto | Ação |
|---|---|---|
| `Ctrl+Shift+G` | Global | Mostrar/ocultar a janela do Ghost |
| `Enter` | Chat | Enviar mensagem |
| `Shift+Enter` | Chat | Nova linha sem enviar |
| `Ctrl+C` | Chat | Interromper resposta em streaming |
| `Ctrl+Shift+S` | Global | Capturar tela inteira |
| `Ctrl+Shift+A` | Global | Selecionar região da tela |
| `Ctrl+Shift+R` | Global | Iniciar/parar gravação de reunião |
| `Esc` | Seletor de área | Cancelar seleção |

## 🔧 Troubleshooting

**SmartScreen bloqueou a execução:** como o binário ainda não é assinado, o Windows SmartScreen pode avisar na primeira vez. Clique em **Mais informações** → **Executar assim mesmo**.

**A janela aparece no screen share mesmo assim:** verifique se seu Windows é build 19041 ou superior (`winver` no terminal). Versões anteriores não suportam `WDA_EXCLUDEFROMCAPTURE`.

**Áudio do sistema não é capturado:** confirme que nenhum outro app está com acesso exclusivo ao dispositivo e que o driver suporta loopback (a maioria suporta via WASAPI).

**Gatekeeper (macOS):** ainda não aplicável. A versão Mac está planejada para v0.2 e virá com instruções específicas de primeira execução.

**A chave da OpenAI não é aceita:** confira se não há espaços no `.env` e se a chave tem créditos ativos na conta.

## 🛠 Desenvolvimento

### Setup do ambiente

```bash
git clone https://github.com/jesusoliveira/ghost.git
cd ghost
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt
```

### Rodar testes

```bash
pytest
```

### Lint e typecheck

```bash
ruff check .
mypy .
```

### Padrões de código

- Tipagem estática em todas as funções públicas (validada com `mypy --strict`)
- Docstrings no estilo Google em módulos, classes e funções públicas
- Formatação e lint via `ruff` (configurado em `pyproject.toml`)
- Imports organizados automaticamente pelo `ruff`
- Nomes de variáveis e funções em inglês; mensagens de UI e comentários em português

Antes de abrir um PR, rode `pytest && ruff check . && mypy .` e garanta que tudo passa.

## 🗺 Roadmap

- **v0.2 — Mac port**: adaptação para macOS usando a API `sharingType` do `NSWindow` para o equivalente ao screen-share invisível
- **v0.3 — i18n**: suporte a múltiplos idiomas na UI (en, pt-BR, es) com detecção automática
- **v1.0 — Distribuição**: auto-update via Squirrel, code signing do binário Windows, instalador MSI e notarização no macOS

## 🤝 Contribuindo

Pull requests são muito bem-vindos. Leia o [CONTRIBUTING.md](CONTRIBUTING.md) antes de começar — ele explica o fluxo de fork, o padrão de commits e o processo de review.

Se você encontrou um bug ou tem uma ideia, abra uma [issue](https://github.com/jesusoliveira/ghost/issues).

Todos os participantes do projeto seguem o [Código de Conduta](CODE_OF_CONDUCT.md).

## 📄 Licença

Distribuído sob a licença MIT. Veja [`LICENSE`](LICENSE) para o texto completo.

---

Feito com ☕ por [Jesus Oliveira](https://github.com/jesusoliveira).
