# Auditoria da instalação JobRadar BR

Auditoria EAP `0.1.1`, realizada em 26/11/2026 pelo relógio do host (`America/Sao_Paulo`). Base GoJob `0ab2efcedf8abc4eeb9e40cb941def92d573a4ed`. [Issue GoJob #1](https://github.com/brenomega/GoJob/issues/1)

A Gupy MCP, a 99jobs e as seis fontes de descoberta concluíram todas as execuções observadas. O CIEE coletou em três de quatro execuções e falhou na mais recente. A Gupy direta e a Vagas.com falharam em todas as execuções da amostra. A Freehire terminou a última execução com 247 rejeições de normalização. O filtro instalado aceitou as entradas reconstruídas de ADIMAX e NORSUL como `ELIGIBLE / TECH_PLAUSIBLE`.

## Instalação e cadência efetiva

A instalação em `~/Workspaces/ws-codex/job-radar` declara `job-radar-br` versão `0.1.0` no `pyproject.toml` e no metadado instalado. O diretório está sem metadados Git. O SHA-256 do manifesto ordenado de 51 arquivos de código e configuração inspecionados é `357dc8671fef6437c8c67d3c5777579648ae752c6b6806fd9363fe33d0ef51a5`. Comparados com `MANIFEST.sha256` da distribuição, 39 arquivos coincidem, 11 divergem e um está ausente do manifesto original. A licença do legado não foi identificada no pacote inspecionado.

| Unidade do usuário | Configuração instalada | Evidência de execução |
| --- | --- | --- |
| `job-radar.timer` | `OnBootSec=5min`, `OnUnitActiveSec=2h`, `AccuracySec=2min`, `Persistent=true`. Link em `timers.target.wants`. Serviço `oneshot` executa `.venv/bin/radar cycle` na instalação. | O journal registra oito ciclos no dia 26/11, entre 05:57:22 e 20:00:04. Os sete intervalos entre inícios ficaram entre 120 e 122 min, com mediana de 120,01 min. O systemd registrou oito inícios concluídos com `JOB_RESULT=done`. |
| `job-radar-enrichment.timer` | `OnBootSec=10min`, `OnUnitActiveSec=10min`, `AccuracySec=1min`, `Persistent=true`. Serviço executaria `enrichment-dispatch --limit 20`. `enrichment.enabled=false` em `config/settings.toml`. | Nenhum link desse timer em `timers.target.wants` e nenhuma execução da unidade no journal consultado. |

Os arquivos `.timer` instalados correspondem às cópias distribuídas. Os `.service` instalados divergem em bytes das cópias da distribuição. A cadência observada vem do journal das unidades instaladas. `systemctl --user list-timers` e `systemctl --user show` receberam `Operation not permitted` no acesso ao barramento, deixando os estados atuais dos timers e o próximo disparo sem verificação. `radar/cycle.py` e `radar/discovery/runner.py` consultam `next_due_at` por fonte, com a cadência nominal como fallback. Portanto, o intervalo de uma fonte também depende de quando o timer executa o próximo ciclo elegível.

## Inventário e saúde observada

Janela solicitada ao journal local da unidade `job-radar.service` de 19/11 às 00:00:00 até 26/11 às 21:38:40. Os oito resumos disponíveis estão todos em 26/11 e contêm 46 execuções de fonte. As cadências vêm de `config/sources.toml`, `config/discovery.toml` e `config/watch_pages.toml`, conferidas com o registro de adaptadores em `radar/sources/__init__.py` e `radar/discovery/config.py`. As contagens vêm de `source_runs`, emitido por `radar/cli.py`. As horas referem-se ao início do ciclo que contém a última coleta de cada fonte.

| Fonte | Cadência nominal | Execuções e último resultado | Recebidos / persistidos na janela |
| --- | ---: | --- | ---: |
| `gupy` · API direta | 120 min | 7 `FAILED`, todas com HTTP 404 em `recovery`. Última às 20:00. | 0 / 0 |
| `gupy_mcp` · busca MCP | 120 min | 7 `SUCCESS`, sendo 6 `live` e 1 `recovery`. Última às 20:00 com 206 recebidos e 143 persistidos. | 1.428 / 991 |
| `ciee` · API de estágio | 240 min | 3 `SUCCESS` e 1 `FAILED`. Última às 18:00 com falha de requisição e zero recebidos. | 14.540 / 5.814 |
| `vagas` · listagem e detalhe Vagas.com | 240 min | 4 `FAILED` em `recovery`, 3 com HTTP 403 e a última às 18:00 com falha de requisição. | 0 / 0 |
| `99jobs` · listagem e detalhe | 360 min | 3 `SUCCESS`. Última às 20:00. Ao todo, 12 resultados de verificação ficaram `UNKNOWN` e zero tiveram abertura confirmada. | 18 / 18 |
| `freehire` · agregador | 120 min | 1 `SUCCESS`, 5 `PARTIAL` e 1 `FAILED`. Última às 20:00 com 247 recebidos e 247 rejeitados na normalização. | 1.694 / 163 |
| `watch:cia-talentos` · índice de programas | 360 min | 3 `SUCCESS`. Última às 20:00. | 1 / 1 |
| `watch:eureca` · índice de programas | 360 min | 3 `SUCCESS`. Última às 20:00. | 5 / 5 |
| `rss:google-news-programas` | 360 min | 3 `SUCCESS`. Última às 20:00. | 145 / 115 |
| `rss:google-news-inscricoes` | 360 min | 3 `SUCCESS`. Última às 20:00. | 96 / 96 |
| `notice:mma-estagios` · editais | 720 min | 1 `SUCCESS` às 14:00. | 713 / 13 |
| `notice:tcu-estagios` · editais | 720 min | 1 `SUCCESS` às 14:00. | 87 / 4 |
| `google_alerts` · IMAP | 360 min | Desabilitada na configuração. Zero execuções observadas. | — |

O coletor de canais públicos do Telegram existe no código e o carregador depende de entradas `[[telegram]]` em `discovery.toml`, que não estavam presentes. O notificador de saída está habilitado e os oito resumos registram 11 notificações. Os totais por fonte somam observações repetidas entre ciclos. Na descoberta, `persisted` conta eventos de lead. Seu `unique_yield` conta novos leads, enquanto nos adaptadores diretos o campo conta novas vagas no repositório. Na amostra, o CIEE registrou 52 novas vagas e a Gupy MCP, quatro. Essas medidas dependem da ordem de ingestão e do histórico do radar. A amostra não mede cobertura de mercado, qualidade dos 11 alertas ou a quantidade de programas de verão encontrados.

### Falhas e hipóteses verificadas

- **Gupy direta.** Sempre falhou com HTTP 404 na amostra. `radar/sources/gupy.py` pagina com `offset` e `pagination.total`, usa até 100 por página e aplica limites configurados de 600 por consulta e 1.200 no total. A busca separa presencial/híbrido no RJ de remoto no Brasil com localidades fixas no adaptador. `radar/cycle.py` conserva o checkpoint quando um `SUCCESS` é truncado. Sem uma resposta válida no período, o comportamento de paginação do endpoint ficou sem verificação live.
- **Gupy MCP.** Todos os lotes observados tiveram dados. Em `radar/sources/gupy_mcp.py`, uma busca estruturada vazia aciona uma sondagem ampla que gera `PARTIAL` quando esta encontra resultados. Quando as duas consultas devolvem zero, ainda há caminho para `SUCCESS`. A proteção de `radar/cycle.py` depende de estado anterior e, para MCP, de pelo menos cinco registros na tentativa anterior. Assim, classificar zero como saudável continua possível no código, sem ocorrência identificada na amostra.
- **Vagas.com.** HTTP 403 e falhas de requisição explicam os resultados observados. HTTP 429 não apareceu nas execuções da amostra. `radar/sources/vagas.py` interrompe consultas a detalhes no primeiro 429 dessa etapa e guarda itens da listagem com detalhe incompleto. `radar/eligibility.py` mantém esses itens em `UNKNOWN` quando a incompletude consta dos dados da fonte.
- **Freehire e CIEE.** O journal registrou erros de normalização em 6 de 7 execuções da Freehire e 1.516 rejeições de esquema somadas. O CIEE somou 36 rejeições de esquema e 8.690 rejeições de negócio. A última falha do CIEE ocorreu na requisição. O resumo agregado não identifica a mudança exata no payload da Freehire.

## Filtros e canários

Em `config/settings.toml`, o escopo presencial/híbrido configurado é RJ e o remoto aceita Brasil. Em `radar/eligibility.py`, o filtro trata prazo, exclusividade explícita, geografia, conclusão acadêmica, senioridade e área. A janela de conclusão de dez/2027 a jun/2029 é fixa no código. Geografia ou conclusão ambígua pode resultar em `UNKNOWN`. A classificação da oportunidade reconhece estágio, júnior, trainee, graduate e programa de talentos sem distinguir verão como categoria. `assess_job` rejeita `NON_TECH`, mas permite `TECH_PLAUSIBLE` quando os demais critérios passam. Na etapa de notificação, `radar/db.py::should_notify` exige `ELIGIBLE`, `OPEN_CONFIRMED` e ausência de notificação anterior ou supressão.

| Canário e evidência pública | Veredito esperado pela área do anúncio | Resultado no código instalado |
| --- | --- | --- |
| ADIMAX, CIEE `6299518`, estágio de Veterinária no RJ com atividades de nutrição animal e clínicas. O anúncio público aparece como indisponível. [CIEE](https://portal.ciee.org.br/quero-uma-vaga/?codigoVaga=6299518) | Fora de TI. | `ELIGIBLE / TECH_PLAUSIBLE / internship`, motivo `objective_rules_passed`. |
| NORSUL, Gupy `12924593`, Estágio Almoxarifado Caxias, RJ, com cursos de Administração, Engenharia de Produção ou Logística. [Anúncio Norsul](https://norsul.gupy.io/job/eyJqb2JJZCI6MTI5MjQ1OTMsInNvdXJjZSI6Imd1cHlfcG9ydGFsIn0%3D?jobBoardSource=gupy_portal) · [Listagem Gupy](https://portal.gupy.io/job-search/term%3Destagio) | Fora de TI. | `ELIGIBLE / TECH_PLAUSIBLE / internship`, motivo `objective_rules_passed`. |

As duas entradas reproduzem a aceitação indevida pelo classificador. Os radicais de Veterinária e Almoxarifado estão ausentes de `CLEAR_NON_TECH_TITLE_TERMS`, e `_tech_status` devolve `TECH_PLAUSIBLE` mesmo sem evidência técnica positiva. Como controles, o título `Estágio em Suporte Técnico` no RJ retornou `ELIGIBLE / TECH_CONFIRMED`, e o título de Almoxarifado com `state=ES` retornou `INELIGIBLE / TECH_PLAUSIBLE` por geografia.

**Causa do alerta histórico de 19/11 não demonstrada.** As entradas são projeções dos anúncios públicos, com estado e modalidade preenchidos para exercitar o filtro. Os arquivos de log rotacionados começam em 25/11 às 09:40 e terminam em 26/11 às 20:08. O journal consultado também ficou sem resumos do dia 19. O código registra IDs internos aleatórios nos eventos de elegibilidade e notificação, e não foi possível vincular os anúncios aos alertas daquela data nem recuperar o payload de origem.

## Preservação e reprodução

Os instantâneos de 21:38:40 e 21:41:55 de 26/11 registraram SHA-256 idênticos, com mesmos inode, tamanho, mtime e ctime, para `.env`, `data/radar.db` e `logs/radar.log{,.1,.2,.3}`. Ambos registraram ausentes as extensões SQLite `-wal`, `-shm` e `-journal`. A captura usou descritores `O_RDONLY | O_NOATIME | O_NOFOLLOW`, hashing opaco e comparação de metadados antes e depois de cada leitura. O banco não foi aberto pelo SQLite e o `.env` não foi carregado. O CLI legado foi evitado porque até comandos de consulta inicializam o repositório, sincronizam dados auxiliares e instalam um handler de log. `--dry-run` ainda persiste dados.

Os hashes individuais ficaram em `antes.json` e `depois.json`, arquivos `0600` em um diretório local `0700` de `/tmp`. O SHA-256 da concatenação dos dois manifestos, nessa ordem, é `f8a71852a5ac2bd82ed99b9afa967c3bea02b782bc3807e0a265bd90ba73e165`. Os manifestos são evidência local temporária. Para conferir a soma conjunta no diretório privado, use `cat antes.json depois.json | sha256sum`.

Comandos para conferir o código e a cadência configurada, a partir da instalação legada.

```bash
{ find radar -type f -name '*.py'
  printf '%s\n' pyproject.toml config/{discovery,priority_companies,settings,sources,watch_pages}.toml
} | LC_ALL=C sort | xargs -d '\n' sha256sum | sha256sum
rg -n '^(OnBootSec|OnUnitActiveSec|AccuracySec|Persistent)=' ~/.config/systemd/user/job-radar{,-enrichment}.timer
rg -n '^\[|^(enabled|cadence_minutes|id)\s*=' config/{sources,discovery,watch_pages}.toml
```

A coleta dos resultados usou o `MESSAGE` JSON do journal, que o CLI emite em várias linhas. O núcleo da extração abaixo imprime somente campos agregáveis de fontes conhecidas e classes derivadas do campo `error`.

```bash
journalctl --user-unit=job-radar.service --since='2026-11-19 00:00:00' \
  --until='2026-11-26 21:38:40' --no-pager -o json \
  --output-fields=MESSAGE,_STREAM_ID,USER_UNIT,JOB_TYPE,JOB_RESULT |
python -c '
import json
import re
import sys
sources = set("gupy gupy_mcp ciee vagas 99jobs freehire watch:cia-talentos watch:eureca "
              "rss:google-news-programas rss:google-news-inscricoes notice:mma-estagios notice:tcu-estagios".split())
patterns = {"404": r"\b404\b", "403": r"\b403\b", "429": r"\b429\b",
            "requisição": r"request failed|connection|network", "esquema": r"schema|normaliza"}
buffers = {}
for line in sys.stdin:
    entry = json.loads(line)
    stream, message = entry.get("_STREAM_ID"), entry.get("MESSAGE")
    if stream is None or not isinstance(message, str):
        continue
    if message == "{":
        buffers[stream] = [message]
    elif stream in buffers:
        buffers[stream].append(message)
        if message != "}":
            continue
        try:
            cycle = json.loads("\n".join(buffers.pop(stream)))
        except json.JSONDecodeError:
            continue
        for run in cycle.get("source_runs", []):
            if run.get("source_id") in sources:
                fields = ("source_id", "mode", "status", "received", "persisted",
                          "open_confirmed", "schema_rejected", "unknown_count", "unique_yield")
                error = str(run.get("error") or "").lower()
                classes = [label for label, pattern in patterns.items() if re.search(pattern, error)]
                print(cycle["started_at"], *(run.get(field) for field in fields), ",".join(classes))
'
```

O ensaio do filtro importou apenas o classificador instalado, fora da pasta legada e com ambiente limpo.

```bash
legacy="$HOME/Workspaces/ws-codex/job-radar"
cd /tmp
env -i PATH=/usr/bin:/bin LC_ALL=C.UTF-8 PYTHONPATH="$legacy" "$legacy/.venv/bin/python" -B - <<'PY'
from datetime import UTC, datetime
from radar.eligibility import assess_job

now = datetime(2026, 11, 19, 15, tzinfo=UTC)
cases = [
    ("Estágio em Veterinária", "Nutrição animal, clínicas veterinárias e mercado pet.", "RJ", "unknown",
     [{"source_id": "ciee", "raw_json": '{"areaProfissional":"Veterinária"}'}]),
    ("Estágio Almoxarifado Caxias", "Cursos: Administração, Engenharia de Produção ou Logística.", "RJ",
     "onsite", []),
    ("Estágio em Suporte Técnico", "", "RJ", "onsite", []),
    ("Estágio Almoxarifado Caxias", "", "ES", "onsite", []),
]
for title, description, state, mode, rows in cases:
    job = dict(title=title, description=description, state=state, work_mode=mode, country="BR")
    result = assess_job(job, rows, now=now)
    print(title, state, result.status, result.tech_status, result.opportunity_type, *result.reasons)
PY
```
