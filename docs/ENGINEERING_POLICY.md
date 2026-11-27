# Política de engenharia — produto GoJob

1. Estude código real do JobRadar e PPO antes de substituir; preserve SQLite, bot e segredos. Compare soluções existentes e bibliotecas; cite evidências atuais.
2. Preferir solução local simples, design incremental e testes que detectem defeitos; evitar novas infraestruturas sem benefício mensurável.
3. Se novos requisitos conflitam com decisões confirmadas, solicitar mudança de governança por Issue, parecer estratégico e decisão humana quando necessário. Não modificar texto da North Star para justificar implementação.
4. Artefatos de produção não podem ser criados pelo runner. Todos são implementados por Developer Codex, revisados e integrados.
5. Dados sensíveis do candidato são privados, não carregados em contexto de Codex nem copiados ao GitHub. Testar com fixtures sintéticas.
6. Modelos de IA evitados no produto, Codex exclusivo do desenvolvimento. 
