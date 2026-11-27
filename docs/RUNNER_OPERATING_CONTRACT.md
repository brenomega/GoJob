# Contrato operacional para diagnostician, repairer e revisores do GoJob Runner

Este documento é o contexto de continuidade do runner. A fonte factual mais recente é o `context.json` do incidente, os commits do repositório e o estado privado; não substitua evidência real por estas instruções.

## Objetivo e limites
- O runner é a automação que coordena GPT-6.2 Sol/high para construir GoJob; não escreve código nem testes do produto, não gera currículos e não executa candidaturas reais por conta própria.
- O produto prioriza descoberta ampla de estágios/júnior de tecnologia, RJ presencial/híbrido ou Brasil remoto e programas de estágio/verão; UX de preferências, currículos do usuário, diversidade opcional, ATS existentes, Brave e Telegram de exceções é responsabilidade de agentes do produto.
- O runner mantém `main` estável, `develop` para integração, branches por EAP, Issues/PRs, commits lógicos em português, teste independente, Reviewer técnico + estratégico, CI por SHA e merge commit. Um único usuário GitHub `brenomega` impossibilita `APPROVE` formal de outro ator: os pareceres devem ser agentes independentes e materializados com identidade do HEAD/BASE.
- O Reviewer deve reprovar uma solução tecnicamente correta que não avance a North Star ou que tenha custo de oportunidade inadequado. `STOP/REVISE` corrigível retorna ao Developer em no máximo três rodadas; veto humano/ilegal/consentimento não é contornado.
- O agente de teste deve criar oráculos independentes de falhas plausíveis, não testes triviais para melhorar cobertura, e validar integração real quando necessário. O runner só controla a delegação e as evidências.

## Histórico de incidentes que motivam a arquitetura
- Chess runner: `Unexpected PR snapshot` depois de push, revisão para HEAD antigo; merge manual externo; Docker indisponível erroneamente confundido com defeito da implementação; excesso de testes fracos; pacing artificial e ciclos caros. Evitar force-push, resets, duplicação de PR, uso de parecer obsoleto e loops sem progresso.
- GoJob 3.1.1: `codex` acessível pelo shell NVM mas ausente no systemd; o instalador corrigido deve transportar ambiente executável e confirmar no serviço.
- GoJob 3.1.2: `0.1.2` BLOCKED por Developer; `0.1.3` BLOCKED após revisão, sem reparador; gates retornavam status de sucesso apesar de backlog incompleto. A causa foi classificação/encaminhamento, não necessariamente produto defeituoso.
- GoJob 3.2.0: release ficou em PR aberto por `STOP` do revisor estratégico; a ferramenta encerrava na primeira objeção. V3.3 criou três rodadas e correção entre elas; ainda assim o supervisor roteava apenas saída 70, ignorando erros de ambiente/política.
- Auditoria da v3.2: risco de testes de produto executados no host, origem de CI insuficientemente verificada, proteção de governança incompleta, prova de revisão superficial, edição sem oráculos; riscos continuam até prova de correção, não são automaticamente prioridade acima do fluxo de autorreparo.

## Procedimento em qualquer parada ou execução estagnada
1. **Preservar**: snapshots exatos de Git, state.json, journal, PR/Issue, estágio, histórico de tentativas, logs, retorno do sistema; nunca reset/stash/clean/force push como recuperação automática.
2. **Diagnosticar com evidências**: diferenciar bug de código do runner, bug do processo, problema corrigível de tarefa, falha externa transitória, autorização legítima, falta de dados reais e erro do próprio supervisor. Uma etiqueta `PolicyFailure`/`ExternalFailure` NÃO é prova de que não exista bug do runner.
3. **Planejar reparo no componente correto**: somente o runner-repairer modifica `automation/gojob_runner`, `automation/supervisor`, `automation/tests`, `docs/RUNNER_*` e metadados de versão. O reparador de tarefa é outro agente, convocado pelo runner para trabalhar na worktree de produto.
4. **Validar**: reproduzir falha antes/depois, executar testes do runner que detectem o defeito, `repo_guard`, CI e revisões independentes técnica e estratégica no HEAD/BASE corretos. Nenhuma narrativa do reparador basta.
5. **Limitar tentativas**: após 3 revisões sem consenso ou reparos repetidos sem progresso, preservar context package e escalar; não iniciar novo PR idêntico nem criar tentativas ilimitadas.
6. **Retomar**: reconciliar operações externas com GitHub antes de repetir efeitos; verificar o novo runner carregado em `develop`; não descartar sessões em andamento.

## Investigação proativa
- Uma auditoria independente periódica examina **o runner**, journal recente, os bloqueios reais, confiabilidade da recuperação, revisão estratégica, throughput, custos de contexto e qualidade de testes do runner.
- Proponha um bug apenas se houver evidência e um cenário reproduzível. Prefira corrigir causalidade e cobertura a multiplicar camadas ou enfeites de interface.
- Mudanças de política confirmada, supressão de revisão, permissão nova, credenciais e bypass de consentimento exigem decisão humana. Nunca promova automaticamente uma hipótese como fato.

## Reconstituição de contexto
O dossier do incidente contém evidências e hashes. Onde um dado não está disponível, reconheça explicitamente a lacuna. Contexto externo completo de chats não foi transferido ao Codex e não pode ser alegado; use documentos versionados e memória observacional, sem simular acesso à conversa.
