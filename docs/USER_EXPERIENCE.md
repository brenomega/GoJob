# Contrato de experiência do GoJob (para implementação pelos agentes Codex)

## Onboarding
1. Formulário 1: áreas de interesse (TI pré-selecionada, extensível), senioridade (estágio e júnior iniciais; incluir programas de estágio, summer jobs e programas generalistas com tecnologia), modalidade desejada (remoto, híbrido, presencial) e estados disponíveis para as duas últimas. Remoto requer elegibilidade geográfica comprovável. O usuário confirma dados pessoais, formação e respostas reutilizáveis.
2. Dados de diversidade: opcionais, autodeclarados e separados de dados profissionais; consentimento específico por plataforma/candidatura. Jamais inferir raça/deficiência/gênero. Falta de resposta não deve ser preenchida automaticamente.
3. Currículos: entre cinco e dez variantes fornecidas pelo usuário, sem geração automática. Escolha por evidência de requisitos e perfil da vaga. Nunca editar documentos originais.
4. Formulário 2: status por plataforma: possui conta, perfil completo, currículo atualizado, sessão autenticada ou exige login. Estados verificados, não apenas declarações. Segredos nunca persistidos em plano-texto.

## Candidatura
- Primeiro tente candidatura sem login quando suportada. Depois sessão autenticada em perfil Brave dedicado, respeitando termos, permissões e escopo.
- Gupy/Vagas.com/ATS com currículo próprio: use cadastro existente quando verificado; NÃO criar nem editar currículo estruturado por padrão. Se desatualizado, enviar NEEDS_USER.
- Gupy: campo apresentação com até 1.500 caracteres e três habilidades quando solicitadas; escolher SOMENTE respostas/habilidades previamente confirmadas pelo usuário e admissíveis, sem inventar. Caso impossível, NEEDS_USER.
- Formulários arbitrários: usar dados autorizados, parar para perguntas novas, testes, MFA ou CAPTCHA.
- Antes do envio, criar intenção durável, usar idempotência, confirmar recibo. Estado `SUBMISSION_UNCERTAIN` não é retry.
- O Telegram original do JobRadar deve receber SOMENTE exceções de candidaturas (motivo específico, URL e ação). Durante migração, notificações de radar podem permanecer em canal/mode separado. Sem dados pessoais sensíveis.

## Métricas
- Precisão da elegibilidade e recall sobre corpus independente; yield exclusivo por fonte; programas fora de ATS; tempo até detecção; taxa de candidaturas confirmadas e duplicadas; NEEDS_USER e entrevistas futuras. Não prometer 100% do mercado.
