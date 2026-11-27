# Política de recuperação e produtividade da release (v4.1)

O runner não é avaliado por volume de código, commits ou chamadas LLM, mas por releases integradas, tarefas desbloqueadas, correções testadas, qualidade dos reviews e tempo/custo até completar o ciclo.

## Gargalo comprovado

No PR #13, 27/11/2026, a publicação original levou até 15h03 e a segunda revisão `REVISE` foi registrada às 15h41 com oito commits no PR. Às 15h44 uma execução `subprocess.run(..., timeout=2400)` de reparador foi interrompida com Ctrl+C. O processo original não emitia progresso interno nem checkpoints parciais durante a revisão.

## Regras da v4.1

1. `quickstart` instala o worker e solicita start assíncrono. Somente o serviço systemd coordena agentes longos; terminal não é dono da release.
2. Todo HEAD/BASE deve passar no CI vinculado antes de gastar recursos de revisão; não substituir CI por resultado do Developer.
3. Dois revisores read-only podem rodar em paralelo. Cada um recebe o mesmo HEAD/base e guarda seu parecer integral individualmente. Uma interrupção não repete parecer válido.
4. Parecer seguinte deve verificar achados anteriores; regressão nova justifica novo achado, mudança arbitrária de padrão não.
5. Implementador só pode alterar arquivos permitidos; planejamento de commits respeita até oito por PR, preferindo correções concisas que deixem espaço para próximas rodadas. Exaustão gera bloqueio explicado, não nova chamada inútil.
6. Os agentes têm heartbeat, duração e log privado. Erro de rede, limite externo ou processo encerrado preserva worktree e journal; retry programado ocorre sem usuário relançar o comando.
7. Lock global de release impede dois controladores novos de modificar o mesmo repositório ao mesmo tempo. Atualizações de versões anteriores já em execução podem ainda completar; não executar reset/force-push para as interromper.
8. Passar nos testes offline e CI do PR não prova implantação no BigLinux. `INSTALLED` requer ativação local real do supervisor.

## Evidência de aceitação necessária

- `quickstart` normal retorna sem esperar Codex; `--status` consulta offline.
- Em falha durante segunda revisão: primeiro parecer concluído reutilizado e segundo refeito, sem revisar HEAD obsoleto.
- Em `CI failure`: nenhum revisor Codex é convocado.
- No último commit permitido: nenhuma nova sessão de implementador é convocada; journal inclui achados e situação.
- Timeout de Codex encerra descendentes, emite evento e preserva dirty diff.
- Git real em repositório descartável confirma PR→CI→revisão→merge commit sem tocar produto.

Não confundir paralelismo com aprovação automática. Os papéis continuam independentes, com checks vinculados a SHA e limites para decisões não delegáveis.

## Continuidade da release v4.0 rejeitada

O PR #13 ainda não foi integrado: o HEAD remoto original tinha oito commits, incluindo cinco correções após a primeira revisão, e dois pareceres `REVISE`. A v4.1 parte da `develop` estável e **não considera esses commits aprovados**. O controlador busca a branch anterior como evidência somente leitura e transmite seu SHA, commits e arquivos modificados aos revisores e ao reparador. Eles devem comparar correções de empacotamento imutável, CI, autorreparo e watchdog que podem ter ficado ausentes da nova candidata, validar o mérito e solicitar a portabilidade apropriada quando necessário.

Essa reconciliação **não importa automaticamente** todo o código anterior: ele não passou nos revisores. Se a branch anterior desaparecer, o processo registra a lacuna sem inventar contexto ou abortar toda a release. O novo PR deve mencionar o PR #13 e preservar seu histórico, não excluí-lo silenciosamente.
