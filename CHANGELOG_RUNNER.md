# GoJob Runner 3.4.2 — correção de contrato Codex e recuperação de protocolo

## Correção motivada por falha real
- `technical.json` e `strategic.json` declaravam `blocking_kind` mas omitiam o campo de `required`; API recusava output schema com HTTP 400 antes de chamar o Reviewer.
- Ambos os contratos agora exigem **todas** as propriedades, inclusive `blocking_kind`. A política de `STOP` e decisão humana não foi relaxada.
- Pré-publicação verifica recursivamente TODOS os schemas de agentes, inclusive objetos aninhados, e rejeita pacote inválido ANTES de `git fetch`, PR ou Codex.
- Clientes Codex (runner principal, atualização, supervisor e watchdog) preparam cópia privada normalizada do schema quando há defeito estrutural recuperável. Não modificam a fonte nem o repositório e não aceitam respostas sem revisão.
- A chamada com erro inválido de schema passa a ser classificada explicitamente como falha de protocolo, não como rejeição do Reviewer.
- Release isolada `3.4.2`: preserva PRs anteriores e worktrees do produto, aceita base remota desde 3.1.1.

## Escopo e limite
Este é um hotfix procedural de transporte. A instalação ainda precisa publicar a release por PR e CI; o próprio autorreparo 3.4.x não roda antes de a versão ser instalada. Não elimina dependências reais (autenticação, redes, cotas) nem prova integração no ambiente do usuário.

# Changelog — GoJob Runner (não produto)

## 3.4.1 — 2026-11-27
- Corrigido espaço final em `stable_supervisor.py:271` que impedia a release de passar no `git diff --cached --check`; corrigido também espaço final em `gojob_runner/gates.py`.
- Gate de pré-publicação examina os arquivos exatos do runner que serão staged e bloqueia espaçamento final, linha em branco no EOF ou newline ausente **antes de criar worktree ou alterar o Git**.
- Adicionados testes de regressão com o defeito reproduzido e verificação de Git real; a worktree interrompida da 3.4.0 permanece intacta.
- Não modifica nem cria código do produto GoJob.

## 3.4.0 — 2026-11-27

- Incidentes investigados por agente Codex independente antes de encerrar o ciclo, inclusive erros 2/76/75/desconhecidos e tarefas bloqueadas após exit 0. Rótulos de erro deixam de ser prova da causa.
- Dossier privado com estado, histórico, pareceres, journal, Git, logs de execução e contratos; hashes, redaction, cache semântico e tentativas limitadas; contexto encaminhado ao reparador e revisores.
- Supervisor externalizado + watchdog systemd independente com `OnFailure` e timer de 10 min; recuperação de falhas mesmo antes de try/except; backup de última versão conhecida.
- Reparos por PR/CI/revisões SHA-bound, promoção canário após merge e rollback; recuperação após merge antes de deploy, sem recriar PR.
- Auditoria proativa diária de fraquezas do próprio runner com hipótese verificável; propostas de alta prioridade seguem a mesma revisão, sem implementação do produto.
- Captura local privada de resultados de invocações Codex, preservando erros e duração sem armazenar o prompt completo.
- Testes adversariais de triagem, ausência de Codex, veto humano, contexto, watchdog, recuperação de rótulo externo, promoção e não repetição de incidente idêntico.
- Limites conscientes: falhas externas irrecuperáveis permanecem registradas; não existe garantia de correção de toda indisponibilidade ou permissão para bypass.

## 3.3.0 — 2026-11-27
- Corrigido defeito procedural primordial: `STOP`, `REVISE` e `BLOCK` com **achados corrigíveis** não terminam a release ou tarefa na primeira revisão. O feedback integral de dois revisores independentes volta ao implementador, que deve produzir novo HEAD e passar por gates, testes delegados, CI e revisões independentes novamente.
- Política unificada de **três rodadas totais de review** para PRs do produto, release do runner (`upgrade_runner.py`) e autorreparo do supervisor (`stable_supervisor.py`); não transformar três negativas em aceitação. Ao atingir limite, preservar PR, HEAD, três pareceres e diagnosticar estagnação sem looping.
- Veto por consentimento, decisão humana, ação proibida ou dependência externa continua bloqueando sem reescrever política (`blocking_kind`). Parecer negativo com evidência de correção não é veto humano automático.
- Journal de revisão amarrado a HEAD/BASE e retomada de commit já criado antes de push; invalidar parecer obsoleto e exigir novo HEAD; merge final exige `--match-head-commit`.
- Fluxo de atualização real cria nova Issue/PR da release 3.3.0 sem sobrescrever o PR bloqueado da v3.2.0, preserva worktrees, commits e estado, e copia apenas código/testes do runner.
- Corrigidas detecção de código de produto em `scripts/` (delegar testes ao Test Engineer) e atualização de HEAD após teste delegado na sincronização.
- Testes da distribuição incluem Git real + transportes Codex/GitHub simulados para aprovação depois de STOP, três rejeições sem merge, veto humano, autorreparo e recuperação pós-commit. Sem implementar produto/testes de produto.
- **Limite explícito:** testes offline não validam Codex/GitHub/systemd reais nem resolvem todos os achados de sandbox/segurança da auditoria anterior.

## 3.2.0 — 2026-11-26
- Correção procedural da fila: persistência `BLOCKED` agora é encaminhada a um agente diagnosticador independente e, se seguro, a outro especialista de reparo; não ignora indefinidamente EAPs bloqueadas.
- Preserva impedimentos legítimos (`STOP`, `BLOCK`, revogação de política, falta de credencial/evidência, recusa de integrador) em `NEEDS_USER`; limite de duas tentativas, nunca falsificar DONE.
- PR original continua sob a mesma EAP; qualquer HEAD novo invalida revisão e CI antigos, exigindo gates/revisores novamente.
- Supervisor registra incidente próprio para bloqueios de política e dependências externas sem classificá-los como crash corrigível do runner.
- Instalador agora atualiza realmente o **código do runner em origin/develop** (a instalação anterior só atualizava supervisor local), mediante worktree isolada, Issue, PR, revisão estratégica+técnica por agentes Codex independentes, CI do HEAD e merge commit.
- Compatibilidade de atualização da base 3.1.1 ou 3.1.2; não copia testes exclusivos da distribuição para o repositório de produto, não altera arquivos da aplicação.
- Saída do quickstart reduzida a resumos dos testes, sem confundir incidentes sintéticos com operação real.

## 3.1.2 — 2026-11-26
- Corrigida a instalação do serviço `systemd --user`: um drop-in gerenciado (`90-gojob-runtime.conf`) preserva o `PATH` validado da sessão, incluindo NVM/Node e o executável Codex; vence overrides anteriores sem apagá-los.
- Instalação idempotente não reinicia supervisor `active`/`activating`, preservando chamadas Codex e worktrees em andamento.
- Regressões do runner validam NVM ausente no PATH básico do systemd, precedência do drop-in, instalação fail-closed e não interrupção durante atualização.
- Mudança restrita ao instalador/operacional do runner; sem código ou testes de produto.

## 3.1.1 — 2026-11-26
- Corrige UX de instalação e inicialização: `quickstart.py` reúne preflight, autenticação quando necessário, teste do runner, clone, bootstrap inicial idempotente e ativação do supervisor.
- O primeiro ciclo é disparado por systemd imediatamente, sem exigir `--task-id`, e a fila prossegue automaticamente nos próximos ciclos.
- Mantém bloqueios seguros em caso de ferramentas ausentes, identidade divergente e repositório preexistente incompatível.

## 3.1.0 — 2026-11-26
- Correção procedural do runner: autoria de testes do produto atribuída explicitamente a Test Engineer Codex independente, antes dos gates e da revisão, sem produção/testes do produto nesta distribuição.
- Recuperação de edições interrompidas, reconciliação de base e validação de evidências de revisão/CI continuam obrigatórias.
- Runner e supervisor compartilham agora a mesma raiz de estado identificada por repositório; evolução também fica isolada por repositório.
- Gates suportam projetos com mais de um stack, sem deixar testes de extensão sem executar porque há backend Python.
- Regras e testes de regressão desta release avaliam o comportamento do **orquestrador**, não implementações de GoJob.

## 3.0.0 — 2026-11-26
- Primeira release independente do **runner de desenvolvimento**, não do produto GoJob.
- Pipeline Codex gpt-6.2-sol/high: tarefas EAP, Issue, branch, developer, oráculos, gates da base, PR, revisão estratégica+técnica, CI, integrator, merge commit, fechamento da Issue.
- Controller controla GitHub usando a conta do usuário; parecer independente não finge ser GitHub APPROVE.
- State/Journal persistente, locks, worktrees isoladas, tentativa bounded de repairs, replay controlado, supervisor externo com reparador independente.
- Backlog e contexto do produto, sem gerar código de GoJob: Codex é responsável pelo desenvolvimento do produto.
- Versão aplica-se SOMENTE ao runner: patch=y para bug, minor=x para erro procedural transversal, major para novas funcionalidades bruscas.

### Limites de validação
- Testes locais offline não certificam a operação do Codex/GitHub/BigLinux; obrigatória prova real do primeiro ciclo, mantendo `main` estável.
