# Runner v4: plano de controle durável para a release

**Fronteira de responsabilidade:** este documento descreve a automação que desenvolve o GoJob, não a implementação do produto. O Codex não recebe credenciais do GitHub e não pode executar commits, merges, reset ou modificar a configuração systemd do host.

## Problema observado

Em 27/11/2026, o PR #11 do runner chegou à primeira rodada de correção. O implementador relatou testes locais aprovados, porém `systemctl --user` retornou `Operation not permitted` no sandbox. O contrato foi devolvido como `BLOCKED` e `upgrade_runner.release_repair` lançava `SetupError` sem passar a entrega a outro Codex. O instalador finalizava antes da versão nova chegar a `develop`; o supervisor antigo nunca poderia reparar essa release.

## Arquitetura de controle

- **Instalador host:** verifica autenticação, localiza o repositório e instala `gojob-runner-release.timer` e `gojob-runner-release.service` *antes* de iniciar a atualização. Não ativa o supervisor da nova versão antes do merge.
- **Controlador de release:** executa em worktree isolada e usa um lock por versão; preserva Git, PR, commits e logs. Journal JSONL privado e status atômico registram a etapa em execução.
- **Reviewer estratégico e técnico:** dois Codex read-only avaliam HEAD/base exatos. Rejeição corrigível entra em `REPAIRING`. O máximo é três *rodadas de revisão*, não três chamadas totais ao Codex.
- **Diagnóstico independente:** um agente read-only recebe a rejeição, contexto do runner, arquivos alterados, evidências de Git e relato do implementador. Ele distingue impedimento humano, falha de código, dependência externa e restrição de sandbox.
- **Implementador de recuperação:** até duas sessões de correção *dentro da mesma rodada*, sem GitHub writes. Pode concluir código iniciado por outra sessão. Só `DONE` com plano de commits verificado prossegue para gates locais e nova revisão. Nunca se infere `DONE` de uma mensagem otimista.
- **Integração:** o host controla commits, CI ligado ao SHA e merge condicionado ao commit aprovado. Depois do merge, o worker habilita o supervisor da nova release. O supervisor antigo não participa da sua própria substituição.
- **Limites:** se Codex/GitHub estiver indisponível, a evidência permanece e o timer agenda nova tentativa. Um veto humano ou três revisões rejeitadas ficam em `NEEDS_USER` e não são contornados. Recursos externos incertos não são reexecutados sem reconciliar o efeito.

## Semântica do systemd

O sandbox do Codex não tem obrigação de acessar o user bus do BigLinux. `Operation not permitted` **dentro do agente** não é oráculo para afirmar que o serviço real falhou. O desenvolvedor deve verificar código e testes locais e declarar a validação host pendente. O controlador host é responsável pela instalação, `systemctl --user` real e verificação pós-merge. Nunca permitir que essa separação seja usada para ignorar gates de código, revisões, CI ou autorizações não delegáveis.

## Telemetria e troubleshooting

- `~/.local/share/gojob-runner/release-control/<version>/events.jsonl`: transições de estado e tentativas (privado)
- `~/.local/share/gojob-runner/release-control/<version>/status.json`: estado mais recente
- `~/.local/share/gojob-runner/release-control/<version>/incidents/*/dossier.json`: contexto dos incidentes (privado)
- `~/.local/share/gojob-runner/release-control/<version>/agent-logs/`: fragmentos privados e redigidos da saída Codex de agentes e revisores, incorporados nos dossiês posteriores
- `~/.local/share/gojob-runner/release-worktrees/<version>-review-cycle.json`: pareceres completos persistidos
- `journalctl --user -u gojob-runner-release.service`: logs da retomada automática, separada do desenvolvimento do produto

## Critérios de prontidão

1. Teste de implementador `BLOCKED` com diff válido e permissão de systemd indisponível: diagnóstico e segunda sessão de implementação; só avança com commit, gates e novo review.
2. Falha de transporte Codex: registra incidente; nova execução reusa Git e journal sem sobrescrever worktree.
3. Reviewer rejeita duas vezes: mantém HEAD/BASE, ambas as justificativas e novas revisões; terceira rejeição pausa com diagnóstico.
4. Serviços externos simulados em integração offline, Git real; nenhum teste offline deve ser apresentado como prova de instalação no BigLinux real.
5. O supervisor v3.x ainda ativo não é usado para atualizar a v4.

## Referências investigadas (padrões, não dependências adicionadas)

- Temporal Durable Execution: https://docs.temporal.io/durable-execution
- Temporal event history/retries: https://docs.temporal.io/evaluate/understanding-temporal
- LangGraph fault tolerance and checkpoints: https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/fault-tolerance.mdx
- OpenHands interrupted event concerns: https://github.com/OpenHands/software-agent-sdk/issues/4487
- OpenHands report after interrupted runs: https://github.com/OpenHands/OpenHands/issues/20079
- Reddit, coding-agent loops: https://www.reddit.com/r/LocalLLaMA/comments/1qjw326/running_local_coding_agents_as_a_constrained/
- Stack Overflow, D-Bus sandbox: https://stackoverflow.com/questions/75211904/systemctl-error-failed-to-get-d-bus-connection-operation-not-permitted

A escolha foi adaptar padrões em Python stdlib e systemd já presentes, em vez de introduzir Temporal, Redis, banco de jobs ou stack web exclusivamente para um único runner local.
