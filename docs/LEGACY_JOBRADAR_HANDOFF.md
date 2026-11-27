# JobRadar legado — fonte de evidência a recuperar

Histórico de setembro de 2026 disponível na documentação original em `~/Workspaces/ws-codex/job-radar` (confirmar existência, não assumir). O projeto usava Python, SQLite, systemd, Telegram, Gupy direta/MCP, CIEE, Vagas.com, Freehire, 99jobs, Cia de Talentos/Eureca, RSS, Google Alerts e discovery de editais/programas.

Última versão documentada passou em 223 testes, mas não é prova do estado live em novembro. Verificar primeiro `.env`/DB sem extrair segredos, saúde das fontes e métricas no host. Investigar Gupy paginada/truncamento, MCP zero falsamente healthy, Vagas 429 e problemas de normalização. A captura de 19/11/2026 indicou falsos positivos ADIMAX Estágio Veterinária e NORSUL Estágio Almoxarifado. Não inventar causa nem declarar cobertura global.

Não instalar releases antigas sobre a instalação atual, não resetar banco nem extrair credenciais para GitHub/LLM. Somente o Codex com acesso local realiza auditoria real.
