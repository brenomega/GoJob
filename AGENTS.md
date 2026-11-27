# GoJob — Instruções permanentes aos agentes Codex

**Objetivo:** desenvolver o produto GoJob. O runner e o produto são distintos; este repositório é o produto que será criado exclusivamente pelos agentes.

Leia `docs/PRODUCT_NORTH_STAR.md`, `docs/USER_EXPERIENCE.md`, `docs/ENGINEERING_POLICY.md`, `docs/TESTING_POLICY.md`, `docs/DECISION_LEDGER.md`, `docs/STRATEGIC_REVIEW.md` e a Issue EAP antes de editar.

Não implemente por hábito. Pesquise soluções existentes, inspecione o JobRadar original e o experimento Brave/PPO com evidências, compare manutenção, licença e riscos, e só então escolha. Não copie o runner do Xadrez.

**Regras de negócio:** scraping multi-fonte e programas de estágio inclusive summer; filtros configuráveis por área, senioridade, modalidade e estado; usuário fornece currículos e dados pessoais opcionais de diversidade. Escolha variante com fatos reais. Evite IA no produto. Não reescreva perfis Gupy/Vagas existentes; use apenas personalização autorizada. Telegram para exceções explicadas.

**Segurança:** não ler/cadastrar segredos de candidatos, cookies, vaults ou bases reais no desenvolvimento. Não realizar candidaturas reais sem aprovação de rollout. Não burlas de CAPTCHA/MFA, não inventar dados, não retentar envio incerto, não alterar consentimento sem usuário.

**Processo:** GitHub via controlador. Dev altera só sua worktree e cria commits `tipo(escopo): imperativo em português [#EAP]`. Não acionar `gh`/push/merge diretamente na sessão do Codex. Outro agente é revisor técnico e outro estratégico. Só integrar após gates e CI reais.

**Autoria de testes do produto:** o Developer implementa o produto e entrega código/contratos; o runner convoca um **Test Engineer independente**, responsável por criar ou fortalecer os testes do produto em uma sessão própria. Ele deve basear oráculos em requisitos antes de depender da implementação. O runner não cria testes do produto, apenas confere escopo, executa gates e aciona review.

**Testes:** testes devem detectar falhas plausíveis com oráculos independentes; não multiplicar testes de getters/mocks, não eliminar falhas. Testar restrições de localidades, diversidade, filtragem, programas, duplicação de candidaturas e recuperação. Evidência externa ausente implica bloqueio, não teste fictício.

**Evolução:** North Star é referencial e pode evoluir com proposta baseada em evidências e crítica adversarial; decisões confirmadas pelo usuário não podem ser alteradas silenciosamente.
