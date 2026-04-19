# Ghost

> Desktop AI assistant for **Windows** and **macOS** — always-on-top, invisible to screen-share, reads your screen, transcribes meetings, and answers via OpenAI.

<p align="center">
  <img src="assets/icon_256.png" width="128" alt="Ghost"/>
</p>

<p align="center">
  <a href="#-instalação"><img alt="Windows" src="https://img.shields.io/badge/Windows-10%2F11-0078D4?logo=windows11&logoColor=white"/></a>
  <a href="#-instalação"><img alt="macOS" src="https://img.shields.io/badge/macOS-11%2B-000000?logo=apple&logoColor=white"/></a>
  <a href="#-licença"><img alt="License" src="https://img.shields.io/badge/license-NCSAL%20v1.0-important"/></a>
  <a href="https://github.com/userJesus/ghost/releases"><img alt="Releases" src="https://img.shields.io/github/v/release/userJesus/ghost?include_prereleases&label=release"/></a>
</p>

---

## ✨ Features

- **Answer questions** from the screen (press `Ctrl+Shift+G` / `⌘+Shift+G`).
- **Screen capture** (full, window, area) + vision models to "see" what you're doing.
- **Meeting transcription** with a virtual audio driver (BlackHole on macOS, Stereo Mix on Windows).
- **Conversation branches** that AI-summarise the context before forking.
- **Dynamic, AI-generated chat titles**.
- **Invisible to screen-share** (`WDA_EXCLUDEFROMCAPTURE`) — safe during Zoom/Teams/Meet demos.
- **Compact bar** and **docked-to-edge** modes for minimal footprint.
- **Voice input** (push-to-talk).
- **Auto-updates**: checks GitHub Releases and shows a banner when a new version is available.
- **100% local data**: logs, settings, history, and your OpenAI key stay in `~/.ghost` — nothing leaves your machine except prompts to OpenAI.

---

## 📦 Instalação

### Windows (10 / 11)

1. Baixe o instalador da [página de releases](https://github.com/userJesus/ghost/releases/latest):
   `GhostSetup-<versão>.exe`
2. Execute o instalador. Ele instala em `%LocalAppData%\Programs\Ghost` (sem precisar de admin).
3. Opções durante a instalação: atalho na área de trabalho, iniciar com o Windows.
4. Abra o Ghost pelo menu Iniciar. Na primeira execução ele pede sua chave da OpenAI.

### macOS (11+)

1. Baixe `Ghost-<versão>.dmg` da [página de releases](https://github.com/userJesus/ghost/releases/latest).
2. Abra o DMG e execute **Ghost Installer.pkg**.
3. O instalador pergunta se deseja incluir o driver **BlackHole 2ch** — marque se pretende usar o modo de reuniões. Ele captura o áudio dos apps de reunião (Zoom/Teams/Meet).
4. Conceda, quando solicitado, as permissões de **Microfone** e **Gravação de tela** em **Preferências do Sistema → Privacidade e Segurança**.
5. Abra o Ghost pelo Launchpad.

### Desinstalação e seus dados

| SO      | Como desinstalar                                | Onde ficam seus dados   |
|---------|-------------------------------------------------|-------------------------|
| Windows | **Configurações → Apps** → Ghost → Desinstalar  | `%USERPROFILE%\.ghost`  |
| macOS   | Abra o DMG → execute `uninstall_mac.sh`          | `~/.ghost`              |

Em ambos os casos, o desinstalador **pergunta** se você quer **apagar os dados** (logs, configurações, histórico, chave da OpenAI) ou **mantê-los**. Se você reinstalar depois mantendo os dados, o histórico volta automaticamente.

---

## 🚀 Uso rápido

| Atalho           | Ação                                               |
|------------------|----------------------------------------------------|
| `Ctrl+Shift+G`   | Abre/foca o Ghost                                  |
| `→` (no app)     | "Encolher para o canto" (modo docked, 56×56 px)    |
| Clique no docked | Restaura o app                                     |

---

## 🔄 Atualizações automáticas

Toda vez que você abrir o Ghost, ele consulta a API de **releases do GitHub** e compara com sua versão instalada. Se houver uma nova, aparece um banner com o botão **Baixar** que abre a página de release no navegador. Não há download/instalação silenciosa — você tem controle total.

---

## ⚖️ Licença

Ghost é distribuído sob a **Non-Commercial Source-Available License (NCSAL) v1.0**.

### Em resumo

| Permitido ✅                                           | Proibido sem licença comercial ❌                         |
|--------------------------------------------------------|----------------------------------------------------------|
| Uso pessoal, educacional, de pesquisa                  | Venda, SaaS, consultoria paga usando o Ghost             |
| Estudar, modificar e contribuir com o código           | Incorporar em produtos pagos                             |
| Redistribuir sem cobrar (mantendo a licença)           | Monetização por anúncio, assinatura, paywall             |
| Fazer forks para fins não-comerciais                   | Relicenciar sob termos mais permissivos                  |

### Fundamento legal (Brasil)

A licença é respaldada pelos seguintes diplomas legais:

- **Lei nº 9.609/98** (Lei do Software) — Arts. 1º, 2º, 9º, 12
- **Lei nº 9.610/98** (Lei de Direitos Autorais) — Arts. 7º-XII, 28, 29, 46
- **Código Penal, Art. 184** — violação de direitos autorais com fim de lucro: reclusão de 2 a 4 anos + multa

Leia o texto integral em [LICENSE](LICENSE).

### Licenciamento comercial

Para uso comercial, contate o autor:

- **Jesus Oliveira** — `contato.jesusoliveira@gmail.com`
- **LinkedIn** → [linkedin.com/in/ojesus](https://www.linkedin.com/in/ojesus)
- **GitHub** → [github.com/userJesus](https://github.com/userJesus)

---

## 🛠️ Build a partir do código-fonte

Pré-requisitos: Python 3.12+, `git`.

### Preparar

```bash
git clone https://github.com/userJesus/ghost.git
cd ghost
python -m venv .venv
# Windows:
.venv\Scripts\activate
# macOS:
source .venv/bin/activate
pip install -r dev-requirements.txt
```

### Rodar em modo desenvolvimento

```bash
python main.py
```

### Build do instalador

**Windows** (requer [Inno Setup 6](https://jrsoftware.org/isdl.php)):

```bat
scripts\build_windows.bat
REM saída: installer\windows\Output\GhostSetup-<versão>.exe
```

**macOS** (requer Xcode CLT + `iconutil` nativo):

```bash
bash scripts/build_mac.sh
# saída: installer/macos/Output/Ghost-<versão>.dmg
```

Ambos os pipelines:
1. Geram o ícone (`scripts/make_icons.py`) a partir de `assets/icon_ghost.svg`.
2. Empacotam o app (`pyinstaller` ou `py2app`).
3. Produzem o instalador nativo (`.exe` / `.pkg` + `.dmg`).

A versão é lida de [`src/version.py`](src/version.py) — única fonte de verdade.

---

## 🗂️ Estrutura

```
ghost/
├── main.py                     # entry-point
├── src/
│   ├── version.py              # versão + metadados do autor
│   ├── api.py                  # ponte JS ↔ Python (webview)
│   ├── updater.py              # checagem de atualizações no GitHub
│   ├── gpt_client.py, history.py, ...
├── web/                        # Alpine.js + HTML/CSS
├── assets/
│   ├── icon_ghost.svg          # fonte
│   ├── icon.ico                # Windows
│   └── icon.iconset/           # macOS (→ icon.icns)
├── installer/
│   ├── windows/
│   │   └── ghost.iss           # Inno Setup
│   └── macos/
│       ├── build_pkg.sh
│       ├── distribution.xml    # productbuild
│       └── Resources/          # welcome/conclusion/license
├── scripts/
│   ├── make_icons.py
│   ├── build_windows.bat
│   ├── build_mac.sh
│   └── uninstall_mac.sh
├── tests/
├── pyproject.toml
├── ghost.spec                  # PyInstaller
├── setup_mac.py                # py2app
└── LICENSE                     # NCSAL v1.0
```

---

## 🤝 Contribuindo

Pull requests são bem-vindas, contanto que respeitem a licença non-commercial.
Veja [CONTRIBUTING.md](CONTRIBUTING.md) e [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

---

## 👤 Autor

**Jesus Oliveira**
- LinkedIn → [linkedin.com/in/ojesus](https://www.linkedin.com/in/ojesus)
- GitHub → [github.com/userJesus](https://github.com/userJesus)
- E-mail → `contato.jesusoliveira@gmail.com`

---

<sub>Copyright © 2026 Jesus Oliveira. Source-available under NCSAL v1.0. Proibido o uso comercial sem licença separada. Ver [LICENSE](LICENSE) para detalhes.</sub>
