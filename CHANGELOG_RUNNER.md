# GoJob Runner v4.0.0 — release recuperável e plano de controle independente

## 4.0.0 — 27/11/2026

### Falha raiz
- PR #11 (v3.4.2): implementador reportou código concluído e testes locais aprovados, mas `systemctl --user` foi negado no sandbox. O controlador interpretava `BLOCKED` como erro terminal e abortava a instalação, embora o supervisor novo ainda não estivesse instalado.
- V3.x dependia de atualizador síncrono e de um supervisor anterior para corrigir uma versão que ainda não tinha sido publicada; seus incidentes de instalação não chegavam ao Codex reparador.

### Mudanças estruturais
- Introduz `release_control.py`: estados duráveis de release, journal JSONL privado, locks de processo, captura de Git, diff, documentos, feedback, saídas redigidas dos agentes e relatórios de erro em dossiê com SHA-256 e armazenamento atômico.
- Introduz `release_agent_recovery.py`: diagnóstico read-only por Codex Sol/High e até dois implementadores de recuperação por bloqueio; orçamento separado das três rodadas de Reviewer. Erros inesperados do controlador também geram diagnóstico independente quando o Codex está disponível.
- Introduz `release_worker.py`: pacote específico imutável por versão, serviço e timer systemd de atualização com execução independente do supervisor antigo, backoff por falha e estado `NEEDS_USER` quando não há progresso seguro.
- `quickstart.py`: instala o controlador antes da atualização; em falha de upgrade agenda retomada sem afirmar instalação concluída; pré-verificação com isolamento de módulos/testes de integração em subprocessos.
- `upgrade_runner.py`: Developer `BLOCKED` passa para diagnóstico e reparador, sem pular gates. Diferencia release publicada (VERIFYING) de instalada (INSTALLED); reconciliação após merge. As revisões mantêm HEAD SHA, CI e limite de três rodadas.
- `bootstrap.py`: publica só arquivos de runner/protocolo/testes e arquitetura; não produz código do GoJob.
- Testes de integração abrangem Git real, handoff entre agentes, bloqueio do systemd no sandbox, dossiês, retries, exclusividade, implementação após rejeição, retenção do PR e verificação de promoção pós-merge.

### Limites
- Em falta de Codex, GitHub, disco, rede ou host systemd, a coleta/retomada será de melhor esforço. Nenhuma política humana é contornada; não há garantia de reparo de toda falha, nem equivalência entre testes offline e execução real no BigLinux.
- Código gerado ainda requer gates e revisão. Falhas do instalador *antes da instalação do worker* continuam um risco de bootstrap, agora mitigado por validação local anterior.

Versões anteriores (3.1.1–3.4.2) continuam preservadas no histórico e nos PRs existentes. Este pacote usa nova branch de atualização e não modifica diretamente o código do produto.

### Correções após a primeira revisão do PR #13

- O instalador gera um pacote completo com manifesto por SHA. O host pode preparar o worker com `python -I -B automation/supervisor/quickstart.py --repo <checkout> --source-sha <SHA-do-candidato> --enable`. A instalação do supervisor e do watchdog usa o SHA do merge confirmado. A troca preserva a geração anterior por inteiro.
- A retomada reutiliza pareceres do mesmo PR, HEAD e BASE. O controlador registra intenção e confirmação antes de fechar a Issue e exige o job `verify` de `gojob-verify` para o par revisado. O merge depende de proteção estrita de `develop` com o check do GitHub Actions, inclusive para administradores.
- Vetos persistem em `NEEDS_USER`. Supervisor e watchdog compartilham a exclusão e a reserva prévia de tentativas. O broker confere o índice contra o plano e os agentes recebem um ambiente permitido e contexto redigido.
- O controlador no BigLinux deve verificar os timers ativos e os serviços do supervisor e do watchdog antes de gravar `INSTALLED`. Os testes locais simulam systemd e verificam a promoção e o retorno à geração anterior.
