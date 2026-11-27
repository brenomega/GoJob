# GoJob Runner v4.1.0 — execução desacoplada e produtividade verificável

## 4.1.0 — 27/11/2026

### Incidente diagnosticado
- Entre 13h20 e 15h44, PR #13 publicou oito commits e duas rodadas de revisões `REVISE`, mas nenhum merge. A segunda reparação executava `codex exec` síncrono quando o usuário pressionou Ctrl+C; parte do estado ficava no Git local e a sessão não fornecia sinal de vida durante o processamento.
- O instalador repetia autenticação e 134 testes a cada invocação, aguardava Developer/revisores e não oferecia monitoramento conciso. Reviewer estratégico e técnico rodavam sequencialmente, e a revisão de uma rodada só recebia checkpoint depois dos dois resultados; uma interrupção provocava custo duplicado.
- CI só era checado após reviews caros, desperdiçando tokens em commits rejeitados deterministicamente. A release com oito commits não tinha margem para outro reparo, mas o sistema poderia ainda convocar implementador desnecessariamente.

### Correções
- `quickstart.py --repo CAMINHO --source-sha SHA` monta o pacote a partir do commit informado e solicita ao systemd o início assíncrono do controlador. `--status` consulta o ledger local.
- Revisores independentes executam em paralelo, cada parecer é salvo atomicamente ao concluir (por SHA/base/papel), e a retomada reaproveita apenas resultados válidos. O histórico de objeções é enviado às próximas rodadas para reduzir mudanças arbitrárias de escopo.
- CI do HEAD é verificado antes da revisão de agentes. O orçamento de oito commits é examinado antes de invocar outro implementador; não se contorna a regra com push/force-push.
- Codex em serviço registra heartbeat a cada 20 segundos. O controlador mantém a saída temporária privada, limita os trechos lidos para diagnóstico e encerra o grupo de processos em caso de timeout.
- O lock da release é compartilhado entre versões. Supervisor e watchdog compartilham o lock do autorreparo e reservam a tentativa antes de editar. Uma intenção de merge persistida permite reconciliar a integração antes de publicar ou editar novamente.
- A implantação usa a geração completa do merge aprovado, com manifesto de integridade e troca atômica. O watchdog pode iniciar a partir da geração anterior se a atual falhar ao importar.
- Testes de regressão cobrem checkout limpo, interrupção entre pareceres e após merge, CI por snapshot, orçamento persistente, veto humano, escopo e fallback.

### Limites
- O controlador do host deve confirmar o novo CI, o merge em `develop`, a execução dos serviços e os timers ativos para a geração aprovada antes de registrar `INSTALLED`. O merge exige proteção estrita do check `verify` do GitHub Actions em `develop`, inclusive para administradores.
- Um PR já esgotado em oito commits e reprovado permanece preservado e exige continuação em nova release ou decisão; o controlador não burla orçamento nem revisão.
- Versões anteriores do worker em execução podem concluir a tentativa já iniciada; systemd aplica a nova unidade em ciclos subsequentes.

---

# GoJob Runner v4.1.0 — release recuperável e plano de controle independente

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

- Continuidade de revisão: consulta read-only da candidata anterior PR #13; histórico e correções anteriores enviados como evidência aos agentes da nova release, sem merge não aprovado.
