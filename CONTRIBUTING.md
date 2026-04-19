# Contribuindo com o Ghost

Obrigado pelo interesse em contribuir. Este guia cobre o essencial para reportar bugs, sugerir features e enviar pull requests que sejam fáceis de revisar e aceitar.

Antes de qualquer coisa, leia o [Código de Conduta](CODE_OF_CONDUCT.md). Participar do projeto significa concordar em seguir essas regras.

## Reportando bugs

Abra uma issue em [github.com/userJesus/ghost/issues](https://github.com/userJesus/ghost/issues) com o rótulo `bug`. Inclua:

- Descrição curta do que aconteceu
- Passos para reproduzir (numerados, o mais objetivos possível)
- Comportamento esperado vs. o que de fato aconteceu
- Versão do Ghost, versão do Python e build do Windows (`winver`)
- Log relevante (`ghost.log` na raiz do projeto) ou stack trace
- Screenshot, se ajudar a entender o problema

Antes de abrir, procure nas issues existentes — tem boa chance de alguém já ter reportado.

## Sugerindo features

Abra uma issue com o rótulo `enhancement` descrevendo:

- O problema que você está tentando resolver (não a solução)
- Um caso de uso concreto
- Como você imagina que funcionaria, se já tiver uma ideia

Sugestões alinhadas ao diferencial do projeto (privacidade, invisibilidade, velocidade) têm prioridade. Mudanças grandes merecem discussão na issue antes de virar PR — economiza retrabalho para os dois lados.

## Setup de desenvolvimento

```bash
# 1. Fork o repositório no GitHub

# 2. Clone seu fork
git clone https://github.com/SEU-USUARIO/ghost.git
cd ghost

# 3. Crie um branch descritivo
git checkout -b feat/nome-da-feature

# 4. Ambiente virtual + dependências
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pip install -r requirements-dev.txt

# 5. Faça suas mudanças, commit e push
git add .
git commit -m "feat: descrição curta"
git push origin feat/nome-da-feature

# 6. Abra um Pull Request contra a branch master
```

## Padrões de código

- **Formatação e lint**: `ruff check .` precisa passar sem erros. Rode `ruff format .` antes de commitar.
- **Typecheck**: `mypy .` precisa passar. Toda função pública deve ter anotações de tipo completas.
- **Docstrings**: estilo Google. Obrigatórias em módulos, classes e funções públicas. Descreva o _porquê_, não o _o quê_.
- **Nomes**: variáveis, funções e classes em inglês. Strings da UI e comentários explicativos em português.
- **Imports**: organizados pelo `ruff` (isort-compatible). Sem imports não utilizados.
- **Tamanho**: funções devem caber em uma tela. Se passar disso, provavelmente vale quebrar.

## Convenção de commits

Seguimos [Conventional Commits](https://www.conventionalcommits.org/pt-br/):

```
<tipo>(<escopo opcional>): <descrição>

[corpo opcional]

[rodapé opcional]
```

Tipos aceitos:

- **feat** — nova funcionalidade
- **fix** — correção de bug
- **docs** — mudanças só em documentação
- **refactor** — mudança de código que não altera comportamento
- **test** — adição ou ajuste de testes
- **chore** — tarefas de manutenção (deps, build, config)
- **perf** — melhoria de performance
- **style** — formatação, ponto-e-vírgula, etc. (sem mudança de lógica)

Exemplos:

```
feat(capture): adicionar captura de monitor secundário
fix(tts): corrigir vazamento de handle de áudio ao cancelar
docs: atualizar roadmap com v0.3
```

Commits atômicos são preferidos. Se o PR tem mais de 5 commits desorganizados, considere fazer squash antes do review.

## Rodando testes

```bash
pytest                  # suíte completa
pytest test/test_x.py   # arquivo específico
pytest -k nome          # filtrar por nome
pytest --cov=src        # com cobertura
```

Toda feature nova precisa de teste. Todo bug fix precisa de um teste de regressão que quebre sem o fix.

## Processo de review

1. Abra o PR com descrição clara: o que mudou, por quê, e como testar.
2. Marque issues relacionadas com `Closes #123` no corpo do PR.
3. CI precisa estar verde (testes, lint, typecheck).
4. Um mantenedor revisa em até alguns dias. Mudanças solicitadas viram commits novos no mesmo branch.
5. Depois do approve, o merge é feito com squash para manter o histórico limpo.

PRs pequenos e focados são aprovados mais rápido. Se você está mexendo em várias coisas, é melhor abrir PRs separados.

Dúvidas? Abra uma issue com o rótulo `question` ou entre em contato: contato.jesusoliveira@gmail.com.
