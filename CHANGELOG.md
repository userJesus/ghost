# Changelog

Todas as mudanças notáveis deste projeto serão documentadas neste arquivo.

O formato é baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/), e este projeto segue [Semantic Versioning](https://semver.org/lang/pt-BR/).

## [Unreleased]

## [1.0.0] - 2026-04-19

### Added

- **macOS support**: full py2app build pipeline, productbuild `.pkg` installer with optional
  BlackHole 2ch virtual-audio driver, and `.dmg` distribution.
- **Dynamic version** in `src/version.py` — single source of truth for all installers, py2app
  and PyInstaller.
- **Auto-update checker**: queries GitHub Releases on startup and surfaces a banner in the
  app when a newer version is available. Manual "check now" exposed via
  `check_for_updates(force=True)`.
- **Non-commercial license (NCSAL v1.0)** replacing MIT. Cites Brazilian software law
  (Lei 9.609/98), copyright law (Lei 9.610/98) and criminal code (Art. 184 CP).
- **Uninstaller prompts** on both Windows and macOS asking whether to wipe `~/.ghost`
  user data, with the full path shown.
- **Author metadata** (Jesus Oliveira, LinkedIn/GitHub) embedded in both installers and
  exposed via the `get_app_info` API for the webview.

### Changed

- Restructured `installer/` into `installer/windows/` and `installer/macos/` subfolders.
- Installer icon now matches the docked-mode icon (green gradient + white ghost) rendered
  from `assets/icon_ghost.svg` via custom Pillow+`svg.path` rasterizer.
- Release workflow now ships both Windows and macOS artifacts.

## [0.1.0] - 2026-04-18

### Added

- Chat com IA usando a API da OpenAI, com suporte a múltiplos modelos configuráveis via `.env`
- Streaming de respostas em tempo real, com possibilidade de interrupção via `Ctrl+C`
- Captura de tela inteira e seleção de região arrastando o mouse, com cancelamento por `Esc`
- Transcrição de áudio via Whisper, captando simultaneamente microfone e áudio do sistema (loopback WASAPI)
- Gravação de reuniões com transcrição automática contínua em background
- Text-to-speech (TTS) para ouvir respostas da IA
- Histórico local de conversas persistido em disco, sem envio para cloud
- **Invisibilidade em screen share** via flag `WDA_EXCLUDEFROMCAPTURE` do Windows — o diferencial principal do projeto
- Drag-and-drop de imagens e arquivos diretamente na janela
- Detecção heurística de informação sensível (tokens, credenciais, secrets) antes do envio
- Modo compacto que reduz a janela a uma barra fina quando não está em uso
- Interface Fluent Design com acrílico/Mica, animações nativas e tema seguindo o sistema
- Atalho global `Ctrl+Shift+G` para mostrar/ocultar a janela de qualquer lugar do sistema
- Atalhos globais para captura de tela, seleção de região e início/parada de gravação
- Stack baseada em pywebview 5+, Alpine.js, Python 3.12+, openai 1.54+, Pillow, pywin32, pynput, soundcard e numpy

[Unreleased]: https://github.com/userJesus/ghost/compare/v1.0.0...HEAD
[1.0.0]: https://github.com/userJesus/ghost/releases/tag/v1.0.0
[0.1.0]: https://github.com/userJesus/ghost/releases/tag/v0.1.0
