# Política de testes do GoJob (fundamentada no runner do Xadrez)

Crie teste apenas para falha plausível de requisito, invariantes de negócio ou fronteira entre componentes. Busque testes existentes ANTES de escrever, fortaleça oráculos fracos, mantenha casos negativos e limites. Não criar testes de getter, setter, mappers triviais ou assertions tautológicas. Não usar a implementação como seu próprio valor esperado. Não transformar infraestrutura que se quer validar em mock; integração exige componentes reais. Sem criar/alterar código para passar teste fraco.

Diferencie unit/integration/system/acceptance. Regras puras e transições em testes unitários; IO real, schema, SQLite, concorrência, filas e recuperação em integração; Brave e ATS em fixtures reprodutíveis sem candidatura real; aceitação no BigLinux para liveness externo e Telegram com consentimento.

O harness deve executar gates determinísticos e nunca aceitar testemunhos do Developer como provas. Candidatura após efeito incerto NÃO pode ser repetida. Vagas fora de TI, localização incerta, formatura incompatível, currículos desatualizados e diversidade sem consentimento são cenários adversariais obrigatórios.

Mutation probes dirigidos em regras críticas, com mutação semântica plausível e falha concreta. Não exigir número de testes arbitrário. Sem skips, desabilitar testes ou mockar tudo para limpar CI. Se ambiente indisponível, registrar teste não executado.
