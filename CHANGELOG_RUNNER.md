# GoJob Runner — CHANGELOG

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
