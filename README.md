<!-- markdownlint-disable MD033 MD041 -->

<p align="center">
  <img src=".github/assets/hero.svg" alt="Ghost — desktop AI assistant" width="100%"/>
</p>

<p align="center">
  <a href="https://github.com/userJesus/ghost/releases/latest"><img src="https://img.shields.io/github/v/release/userJesus/ghost?include_prereleases&style=for-the-badge&label=release&color=3CB895"/></a>
  <a href="https://github.com/userJesus/ghost/releases"><img src="https://img.shields.io/github/downloads/userJesus/ghost/total?style=for-the-badge&color=3CB895"/></a>
  <a href="#-licença"><img src="https://img.shields.io/badge/license-NCSAL%20v1.0-CC3B50?style=for-the-badge"/></a>
  <a href="#-instalação"><img src="https://img.shields.io/badge/Windows-10%2F11-0078D4?style=for-the-badge&logo=windows11&logoColor=white"/></a>
  <a href="#-instalação"><img src="https://img.shields.io/badge/macOS-11%2B-000000?style=for-the-badge&logo=apple&logoColor=white"/></a>
</p>

<h1 align="center">👻 Ghost</h1>

<p align="center">
  <strong>Um assistente de IA que <ins>some</ins> quando você compartilha a tela.</strong><br/>
  Lê a sua tela, transcreve reuniões, responde em tempo real — e ninguém do outro lado vê que ele existe.
</p>

---

## 📥 Baixar agora

<table align="center">
<tr>
<td align="center" width="420">
  <a href="https://github.com/userJesus/ghost/releases/latest/download/GhostSetup-1.0.0.exe">
    <img src="https://img.shields.io/badge/Baixar%20para%20Windows-GhostSetup%201.0.0.exe-0078D4?style=for-the-badge&logo=windows11&logoColor=white" alt="Windows installer"/>
  </a>
  <br/><sub>Windows 10 / 11 · 89 MB · sem UAC / sem admin</sub>
</td>
<td align="center" width="420">
  <a href="https://github.com/userJesus/ghost/releases/latest/download/Ghost-1.0.0.dmg">
    <img src="https://img.shields.io/badge/Baixar%20para%20macOS-Ghost%201.0.0.dmg-000000?style=for-the-badge&logo=apple&logoColor=white" alt="macOS installer"/>
  </a>
  <br/><sub>macOS 11+ · inclui driver BlackHole 2ch · Apple Silicon + Intel</sub>
</td>
</tr>
</table>

<p align="center">
  <sub>
    🔎 Ver todos os arquivos e somas SHA256 →
    <a href="https://github.com/userJesus/ghost/releases/latest">github.com/userJesus/ghost/releases/latest</a>
  </sub>
</p>

---

## ✨ O que o Ghost faz

<table>
<tr>
<td width="50%" valign="top">

### 🫥 Invisível em screen-share
Liga a flag `WDA_EXCLUDEFROMCAPTURE` do Windows (e o equivalente no macOS) — Zoom, Teams, Meet, OBS e Loom simplesmente **não enxergam** a janela do Ghost, nem durante gravação.

</td>
<td width="50%" valign="top">

### 👁️ Lê sua tela
Captura a tela inteira, uma janela ou uma região. A IA "olha" o print e responde sobre código, erros, documentos, planilhas — o que estiver ali.

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🎙️ Transcreve reuniões
Captura mic + áudio do sistema ao mesmo tempo (Stereo Mix no Windows / **BlackHole 2ch** no Mac, instalado junto) e gera transcrições contínuas em background.

</td>
<td width="50%" valign="top">

### 🧠 Conversas com contexto
Titulagem dinâmica gerada pela IA. **Branch** resume a conversa atual antes de abrir outra — você nunca perde o fio da meada.

</td>
</tr>
<tr>
<td width="50%" valign="top">

### 🔄 Atualizações automáticas
Toda vez que abre, o Ghost consulta a API de releases do GitHub. Quando sai uma versão nova, um banner verde aparece com um botão **Baixar** — você decide quando atualizar.

</td>
<td width="50%" valign="top">

### 🔒 100% local
Logs, histórico, configurações e sua chave da OpenAI nunca saem da sua máquina. Ficam em `~/.ghost` e só a mensagem que você envia para a IA é que trafega (direto para os servidores da OpenAI, não pelos meus).

</td>
</tr>
</table>

---

## 🚀 Primeiros passos

### 1️⃣ Instale

<details>
<summary><strong>🪟 Windows</strong> — clique para expandir</summary>

1. [**Baixe o instalador**](https://github.com/userJesus/ghost/releases/latest/download/GhostSetup-1.0.0.exe) (89 MB)
2. Execute `GhostSetup-1.0.0.exe`. Não precisa de admin — instala em `%LocalAppData%\Programs\Ghost`
3. Marque (opcional):
   - ✅ Atalho na área de trabalho
   - ✅ Iniciar com o Windows
4. Finalizar → Ghost abre sozinho
5. Na primeira tela, cole sua **chave da OpenAI** (`sk-...`)

</details>

<details>
<summary><strong>🍏 macOS</strong> — clique para expandir</summary>

1. [**Baixe o DMG**](https://github.com/userJesus/ghost/releases/latest/download/Ghost-1.0.0.dmg)
2. Abra o `.dmg` → **clique com o botão direito** em `Ghost Installer.pkg` → **Abrir** → **Abrir** na janela de confirmação
   > ⚠️ Se der duplo-clique direto, o macOS bloqueia com _"desenvolvedor não identificado"_ (Gatekeeper). O Ghost ainda não é assinado com Apple Developer ID — [veja abaixo](#-aviso-de-gatekeeper-desenvolvedor-não-identificado) se cair nesse aviso.
3. No passo "Personalizar":
   - ✅ **Ghost.app** (obrigatório)
   - ✅ **BlackHole 2ch** (recomendado — driver virtual de áudio para reuniões)
4. Conceda as permissões quando o macOS pedir:
   - 🎙️ Microfone
   - 🖥️ Gravação de tela
5. Abra o Ghost pelo Launchpad → cole sua chave da OpenAI

Para desinstalar depois: o DMG traz um `uninstall_mac.sh` que pergunta se quer apagar os dados + remover o BlackHole.

##### 🛡️ Aviso de Gatekeeper ("desenvolvedor não identificado")

O macOS mostra esse aviso em **qualquer** app que não foi notarizado pela Apple. Notarização exige conta Apple Developer paga ($99/ano), e o Ghost é distribuído gratuitamente sob licença não-comercial — a conta Apple não está nos planos. O instalador é **auditável**: você pode inspecionar todo o código-fonte neste repo, conferir o `SHA256SUMS.txt` do release, e reconstruir localmente com `scripts/build_mac.sh`.

Escolha uma das opções abaixo (da mais segura para a mais ampla):

**Opção 1 — Clique direito → Abrir** (recomendada, por-app)

Em vez de duplo-clique, clique com o botão direito (ou Control+clique) em `Ghost Installer.pkg` → **Abrir**. O macOS mostra uma janela com um botão **Abrir** que autoriza **só este arquivo**. Mesma coisa para abrir o `.dmg` e depois o `Ghost.app`.

**Opção 2 — System Settings → Privacy & Security** (também por-app)

Tenta abrir o `.pkg` por duplo-clique → macOS bloqueia → abra **Ajustes do Sistema → Privacidade e Segurança** → role até o fim. Vai aparecer "Ghost Installer.pkg foi bloqueado" com um botão **Abrir Mesmo Assim / Open Anyway**. Clique.

**Opção 3 — Remover a flag de quarentena via Terminal** (por-app, permanente)

```bash
sudo xattr -rd com.apple.quarantine ~/Downloads/Ghost-1.0.0.dmg
# depois de instalar:
sudo xattr -rd com.apple.quarantine /Applications/Ghost.app
```

Remove o atributo `com.apple.quarantine` só para esse arquivo — o Gatekeeper para de monitorá-lo. Usado por várias distribuições open-source.

**Opção 4 — Habilitar "Qualquer origem" no sistema inteiro** (⚠️ amplo, menos seguro)

Até o macOS Sierra existia uma opção "Qualquer origem / Anywhere" em Privacidade e Segurança. Apple removeu da UI, mas ela ainda funciona via Terminal:

```bash
sudo spctl --master-disable
```

Isso **desativa o Gatekeeper para todo o sistema** (qualquer app não-notarizado roda sem aviso). Só faça se tiver certeza do que está fazendo. Para reverter:

```bash
sudo spctl --master-enable
```

O status atual pode ser checado com `spctl --status`.

> ℹ️ **Nossa recomendação**: use a Opção 1 ou 2. Autorizam apenas o Ghost, preservando a proteção do Gatekeeper para outros apps.

</details>

### 2️⃣ Atalhos

<table align="center">
<tr><th>Atalho</th><th>O que faz</th></tr>
<tr><td><kbd>Ctrl</kbd>+<kbd>Shift</kbd>+<kbd>G</kbd></td><td>Abre / foca o Ghost de qualquer lugar</td></tr>
<tr><td><kbd>⌘</kbd>+<kbd>Shift</kbd>+<kbd>G</kbd></td><td>Idem, no macOS</td></tr>
<tr><td>Botão <strong>→</strong> do app</td><td>"Encolher para o canto" — vira um ícone 56×56 na borda da tela</td></tr>
<tr><td>Click no ícone docked</td><td>Restaura o app inteiro</td></tr>
</table>

---

## 🎨 Identidade visual

<table>
<tr>
<td align="center" width="230">
  <img src=".github/assets/pixel-ghost.gif" alt="Ghost animated" width="170"/>
  <br/><strong>Ghost</strong><br/>
  <sub>fantasminha pixel-art do empty-state</sub>
</td>
<td valign="middle">

A identidade visual nasceu do próprio modo docked — um ícone verde-menta que fica parado na borda da tela esperando você chamar. A cor principal puxa para "esmeralda líquida" (`#61DBB4` → `#3CB895`) sobre um fundo Mica escuro do Windows 11.

<table>
<tr>
<td align="center"><img src="https://img.shields.io/badge/%20-%2361DBB4-61DBB4?style=for-the-badge&label="/></td>
<td align="center"><img src="https://img.shields.io/badge/%20-%233CB895-3CB895?style=for-the-badge&label="/></td>
<td align="center"><img src="https://img.shields.io/badge/%20-%2300281e-00281e?style=for-the-badge&label="/></td>
<td align="center"><img src="https://img.shields.io/badge/%20-%23131313-131313?style=for-the-badge&label="/></td>
</tr>
<tr>
<td align="center"><sub><code>#61DBB4</code><br/>accent-1</sub></td>
<td align="center"><sub><code>#3CB895</code><br/>accent-2</sub></td>
<td align="center"><sub><code>#00281E</code><br/>on-accent</sub></td>
<td align="center"><sub><code>#131313</code><br/>mica-base</sub></td>
</tr>
</table>

</td>
</tr>
</table>

---

## 🗑️ Desinstalar (seus dados ficam seguros)

| SO       | Como desinstalar                                  | Onde ficam seus dados    |
|----------|---------------------------------------------------|--------------------------|
| 🪟 Windows | **Configurações → Apps** → Ghost → Desinstalar     | `C:\Users\<você>\.ghost` |
| 🍏 macOS   | Abra o DMG → duplo-clique em `uninstall_mac.sh`    | `~/.ghost`               |

Em ambos os casos o desinstalador **pergunta se você quer apagar os dados** (logs, configurações, histórico, chave da OpenAI) ou **mantê-los**. Se reinstalar depois mantendo os dados, seu histórico volta como se nada tivesse acontecido.

---

## ⚖️ Licença

O Ghost é distribuído sob a **Non-Commercial Source-Available License (NCSAL) v1.0** — código aberto para ler, estudar, modificar e contribuir. Só não pode ser **usado comercialmente**.

<table>
<tr>
<th>✅ Permitido</th>
<th>❌ Proibido sem licença comercial</th>
</tr>
<tr>
<td>

- Uso pessoal, educacional, pesquisa
- Estudar e modificar o código
- Contribuir com PRs ao projeto
- Compartilhar sem cobrar

</td>
<td>

- Venda, SaaS, consultoria paga
- Incorporar em produto pago
- Anúncio / assinatura / paywall
- Operação interna de empresa com fim lucrativo

</td>
</tr>
</table>

**Fundamento legal (Brasil):** Lei nº 9.609/98 (Lei do Software), Lei nº 9.610/98 (Direitos Autorais) e Art. 184 do Código Penal (reclusão de 2 a 4 anos + multa por violação comercial).

Para licenciamento comercial, fale com o autor ↓

---

## 🤝 Contribuir

Pull requests bem-vindas, contanto que respeitem a licença não-comercial.
Veja [CONTRIBUTING.md](CONTRIBUTING.md) e [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md).

Build local a partir do código-fonte: [`scripts/build_windows.bat`](scripts/build_windows.bat) ou [`scripts/build_mac.sh`](scripts/build_mac.sh). A versão é lida de [`src/version.py`](src/version.py) — única fonte de verdade para todos os instaladores.

---

## 👤 Autor

<table>
<tr>
<td valign="top">

**Jesus Oliveira**

[![LinkedIn](https://img.shields.io/badge/LinkedIn-ojesus-0A66C2?style=flat&logo=linkedin&logoColor=white)](https://www.linkedin.com/in/ojesus)
[![GitHub](https://img.shields.io/badge/GitHub-userJesus-181717?style=flat&logo=github&logoColor=white)](https://github.com/userJesus)
[![Email](https://img.shields.io/badge/Email-contato.jesusoliveira%40gmail.com-D44638?style=flat&logo=gmail&logoColor=white)](mailto:contato.jesusoliveira@gmail.com)

</td>
</tr>
</table>

---

<p align="center">
  <sub>
    Copyright © 2026 Jesus Oliveira.
    Source-available under NCSAL v1.0.
    Proibido o uso comercial sem licença separada.
    Ver <a href="LICENSE">LICENSE</a> para detalhes.
  </sub>
</p>
