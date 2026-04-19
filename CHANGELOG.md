# Changelog

Todas as mudanças notáveis deste projeto serão documentadas neste arquivo.

O formato é baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.0.0/), e este projeto segue [Semantic Versioning](https://semver.org/lang/pt-BR/).

## [Unreleased]

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

[Unreleased]: https://github.com/jesusoliveira/ghost/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/jesusoliveira/ghost/releases/tag/v0.1.0
