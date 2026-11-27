# Lições do runner do Xadrez (fonte, não código copiado)

O ZIP legado completo `xadrez-codex-runner-v2 (1).zip` contém `orchestrator.py`, múltiplos overlays v3.6.x, auditorias e logs. Nunca publicar o ZIP integral: inclui estados operacionais e potencialmente dados de sessão. Usar apenas como inspiração para SHA-bound PR reviews, proteção de worktrees, dedupe GitHub, recovery checkpoint, gates por base e supervisor independente.

Incidentes reais: `Unexpected PR snapshot` após publicação do PR e parecer preso ao HEAD anterior; merge externo com certificação incompleta; dependência Docker negada no sandbox sem ser bug do produto; retrabalho por relatórios otimistas; testes unitários frágeis e muitos casos redundantes.

Evitar pacing artificial e quota de número de commits como objetivo. Garantir no GoJob que reviewer estratégico recebe North Star e alternativas independentemente da narrativa do dev. Testes usam oráculo independente e riscos plausíveis.
