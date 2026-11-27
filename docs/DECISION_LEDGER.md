# Livro de decisões do GoJob

Decisões confirmadas pelo usuário são preservadas aqui, com data, evidência, status e substituição explícita. A IA pode propor novas decisões, não autoatribuir autoridade.

## D-001 — Codex desenvolve o GoJob
- Estado: confirmado
- Runner v3.0.0 é uma ferramenta separada, não o produto. Todas as implementações do produto devem nascer de Issues e PRs por agentes Codex.

## D-002 — IA no produto
- Estado: confirmado
- Evitar IA no GoJob de produção; usar somente quando necessária e justificada. O runner usa GPT-6.2 Sol High.

## D-003 — Currículos e plataforma
- Estado: confirmado
- Currículos são enviados pelo usuário; nunca gerados pelo GoJob. Plataformas com currículo estruturado existente não são reescritas automaticamente. Personalizações autorizadas por candidatura são permitidas.

## D-004 — Escopo flexível
- Estado: confirmado
- Áreas, senioridade, modalidades e estados devem ser escolhas do usuário; defaults são TI, estágio/júnior, com programas gerais e de verão.

## D-005 — Autonomia e segurança
- Estado: confirmado
- Alertar no Telegram quando não for possível submeter, com motivo. Nunca repetir submissão de efeito incerto; nunca burlar autenticação, CAPTCHA ou provas.

## D-006 — Workflow GitHub
- Estado: confirmado
- Branch EAP, commits em português, Issues, PR, revisão por outro agente e merge commit. Todos usam uma conta GitHub, portanto parecer independente é certificado localmente, não GitHub APPROVE nativo.
