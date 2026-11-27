# Arquitetura-alvo do GoJob — hipóteses, ainda não código

Núcleo: sources -> normalized leads -> dedupe -> per-dimension eligibility -> liveness -> application queue -> deterministic CV selection -> authorized ATS/browser executor -> receipt/reconciliation -> Telegram exceptions. SQLite local e mecanismos existentes do JobRadar são ponto de partida.

O runner **não implementa** nenhuma dessas camadas; distribui tarefas aos agentes, verifica evidências e contexto. Cada decisão técnica precisa de investigação existing-first, não copiar automaticamente esta hipótese.
