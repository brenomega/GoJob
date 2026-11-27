# GoJob — North Star (Produto, não Runner)

GoJob será um radar e executor local de candidaturas com baixa intervenção humana. **O produto deve ser projetado e implementado exclusivamente por agentes Codex sob o runner.** Nenhum código prévio de produto neste pacote foi criado pelo assistente.

## Descoberta é a função prioritária
Reaproveitar e auditar a instalação operacional JobRadar BR antes de mexer nela. Coleta multicamadas: ATS, CIEE, Gupy, portais de universidades, sites das empresas, agregadores e landing pages de programas de estágio e verão. Resolver fontes incompletas, deduplicar e verificar candidaturas abertas; medir cobertura por corpus independente, não alegar 100% do mercado. **Não aceitar vaga de Veterinária/Almoxarifado como TI por palavra-chave genérica.**

## Configuração do usuário
A UI pede áreas (inicialmente TI; expansível), senioridade (inicialmente estágio e júnior, além de programas de estágio, summer jobs/graduate e trainees compatíveis), modalidades (remoto, híbrido, presencial), e estados em que aceita presencial/híbrido. Remoto deve comprovar elegibilidade para residência do candidato. Formatura flexível, sem exigência de uma data fixa arbitrária.

## Currículos / plataforma / aplicação
O usuário fornece 5–10 currículos, não gerar currículos. Seleção por família profissional com fatos confirmados. Dois formulários: perfil/dados incluindo diversidade **facultativa e autodeclarada**, e plataformas/cadastros/sessões verificadas. Para ATS com perfil próprio (Gupy/Vagas), reutilizar o perfil já atualizado, **não criar ou atualizar currículo estruturado automaticamente**. Campos específicos de candidatura podem ser personalizados com informações aprovadas, como texto Gupy até 1.500 caracteres e três habilidades solicitadas.

## Autonomia e segurança
Usar navegador Brave existente e experiência PPO Native Messaging como referência a pesquisar; não copiar sem inspeção. Preferir API pública para coleta; autenticar na própria sessão autorizada quando necessário. Não capturar/exportar cookies, não contornar CAPTCHA/MFA ou fazer provas. Registro durável de intenção antes do clique, comprovante oficial para envio confirmado, efeito incerto bloqueia replay. Telegram original JobRadar recebe vagas em que a inscrição não foi concluída, com justificativa, quando modo executor estiver ativo. Ficar próximo de zero intervenção, sem sacrificar validade/privacidade.

## IA
Evitar IA no produto GoJob por padrão; usar somente quando justificada por avaliação de custo/benefício e autorizada. O runner de engenharia usa Codex `gpt-6.2-sol` High, uma responsabilidade separada.

## Evolução estratégica
Os defaults e o produto podem evoluir por hipóteses testáveis e evidência verificável, mas alterações de compromissos explícitos exigem decisão do usuário. O Reviewer estratégico deve reprovar soluções tecnicamente corretas que desviam destas metas.

## Métricas
Precisão da elegibilidade, recall em amostra auditada independente, yield único das fontes, saúde/frescor das fontes, programas de verão encontrados, inscrições confirmadas, erros e duplicidades evitadas, NEEDS_USER, resposta a candidatura e tempo de execução. Volumes de alertas ou de testes não são resultados de produto.
