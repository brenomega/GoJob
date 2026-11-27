# Backlog EAP — GoJob: dependências, entregas e critérios de saída

**Regra de sequenciamento:** uma fase só passa se gates observáveis estiverem completos. Spikes podem correr paralelos quando não dependem de dados privados. Sem cronograma artificial, quantidade fixa de PRs ou cobrança de progressos fictícios. Cada item corresponde em geral a uma Issue GitHub vinculada a EAP; granularidade ajustável pelo planner com justificativa.

## P0 — gênese, recuperação e baseline (dependência para todas fases)
| EAP | Issue pretendida | Dependência | Critério verificável |
|---|---|---|---|
| 0.0.1 | Bootstrap mínimo de repositório público | nenhuma | Git main genesis, develop criado, AGENTS/docs/gates sem segredos; exceção documentada |
| 0.1.1 | Auditoria live do JobRadar legado | 0.0.1 | `BASELINE_CURRENT.md` com SHA/cmds, métricas reais, fonte por fonte, banco preservado e evidências |
| 0.1.2 | Auditoria do canal Brave/PPO e soluções existing-first | 0.0.1 | Matriz comparativa reproduzível MV3+NativeMessaging/CDP/Playwright/AutoApply/open-browser-use; decisão adiada até prova |
| 0.1.3 | Corpus de referência e labels independentes | 0.1.1 | Casos realistas e negativos ADIMAX/NORSUL, remoto/RJ, programas; matriz versionada sem dados sensíveis |
| 0.2.1 | Implementar harness MVP com GitHub checks | 0.0.1 | Agentes com funções distintas, context packets/hash, estados, revisão SHA-bound, recovery e provas adversariais |
| 0.2.2 | Exercitar runner com PR inofensivo | 0.2.1 | Issue/branch/commits/PR/comment/review/CI/merge/close e resume, sem forjar aprovação nativa |

## P1 — confiabilidade do radar (antes de aplicar automaticamente)
| EAP | Issue | Dependência | Critério verificável |
|---|---|---|---|
| 1.1.1 | Reproduzir e corrigir falsos positivos tech | 0.1.3 | ADIMAX/NORSUL rejeitados; suporte TI aceito; explicação por evidência |
| 1.1.2 | Filtro RJ/remoto Brasil e formação | 0.1.3 | matriz de geografia e conclusão 2027.2–2028.2; UNKNOWN não promovido |
| 1.2.1 | Auditoria e expansão de programas | 0.1.1, 0.1.3 | fontes atuais, dedupe de landing/programa e yield exclusivo; incluir programas válidos |
| 1.2.2 | Métricas/frescor/cobertura por source | 1.1.1, 1.2.1 | relatório por fonte com denominador e falhas; sem alegar 100% mercado |
| 1.3.1 | Gating de candidaturas `OPEN_CONFIRMED` | 1.1.2 | lead/UNKNOWN não habilita submeter; prova de liveness antes do efeito |

## P2 — candidato e plataformas
| EAP | Issue | Dependência | Critério |
|---|---|---|---|
| 2.1.1 | Candidate Profile local com proveniência | 0.2.2 | formulário de fatos e respostas aprovadas, versões, redaction, SQLite protegido |
| 2.1.2 | Platform Registry e inspeção de prontidão | 2.1.1 | cadastro, sessão, modelo ATS, currículo atualizado e capability por plataforma; dados não presumidos |
| 2.2.1 | Biblioteca 5–10 currículos versionados | 2.1.1 | selecionar perfil objetivo por família, provar ausência de informação inventada |

## P3 — executor local navegador (DRY-RUN até release gate)
| EAP | Issue | Dependência | Critério |
|---|---|---|---|
| 3.1.1 | Spike Brave MV3 + Native Messaging | 0.1.2 | protocolo tipado, origin allowlist, perfil/sessão isolados, fixture real; sem acesso a cookies |
| 3.1.2 | Seleção fundamentada de executor e adaptadores | 3.1.1 | relatório existing-first com prova de fluxo de pelo menos duas famílias de formulário |
| 3.2.1 | Application outbox e estados idempotentes | 2.1.2, 1.3.1 | SQLite transaction/recovery, `SUBMISSION_UNCERTAIN` sem replay |
| 3.2.2 | Preenchimento de formulários simples e anexos | 3.1.2, 3.2.1, 2.2.1 | fixtures/marcações DOM; nunca presumir sucesso por clique |
| 3.2.3 | ATS com cadastro próprio / formulários longos | 3.2.2 | campos aprovados, respostas guardadas, challenge handling, confirmação real/sandbox |
| 3.3.1 | Evidence & reconciliation | 3.2.3 | prova de submissão, detecção de duplicidade e inspeção de estado externo após timeout |

## P4 — Telegram e rollout
| EAP | Issue | Dependência | Critério |
|---|---|---|---|
| 4.1.1 | Telegram como exceção | 3.3.1 | `NEEDS_USER` com razão exata, sucessos somente consulta; rollout não quebra bot legado |
| 4.1.2 | Modo assistido supervisionado | 4.1.1 | amostra controlada sem erro de dados, interrupção segura, trilha de evidências |
| 4.2.1 | AUTO_SUBMIT por allowlist | 4.1.2 | autorização explícita, taxas por plataforma, kill switch, consentimento, prova de não replay |
| 4.2.2 | Relatório de resultados e feedback de recrutamento | 4.2.1 | submitted/unknown/needs-user/duplicados, entrevistas e rejeições com origem verificável |

## Critérios de stop/go
- De P0 para P1: nenhum arquivo sensível publicado; ambiente antigo preservado; runner provado num PR controlado.
- P1 para P3: corpus e métricas; filtro negativo passa; fonte `PARTIAL/UNKNOWN` não autoriza inscrever; arquitetura original mantida enquanto não houver motivo empiricamente fundamentado.
- P3 para P4: browser sandbox e profiles seguros; nenhuma submissão acidental; testes de timeout/duplicidade; approval e proofs de formulário; sem falsificar capacidade de plataforma.
- `AUTO_SUBMIT`: habilitação gradual após evidência ponta a ponta autorizada e kill switch; não inferir consentimento geral apenas dos currículos carregados.

## Adendo de tarefas obrigatórias para a revisão v2
| EAP | Trabalho | Dependências | Critério |
|---|---|---|---|
| 0.0.2 | Auditar código-fonte Xadrez v3.6.9 e buscar `orchestrator.py` local | 0.0.1 | relatório REUSE/ADAPT/REJECT com evidência de código, sem executar scripts históricos; `LEGACY_SOURCE_PARTIAL` se legado ausente |
| 0.1.4 | Congelar constituição, decisões e trilha objetivo→tarefa | 0.0.1 | decision ledger e context index com SHA e conflito identificado |
| 0.2.0 | Implementar supervisor mínimo e incident bundle | 0.0.2, 0.1.4 | supervisor independente sobrevive a crash forçado do runner; captura incident_id e evidência |
| 0.2.4 | Implementar reviewer estratégico S0/S1 | 0.1.4, 0.2.1 | PR tecnicamente correto mas desalinhado é reprovado por oráculo independente |
| 0.2.5 | Reparação autônoma de runner com canário e rollback | 0.2.0, 0.2.1, 0.2.4 | fault injection inicia repairer, PR, revisão por outro agente, gates e troca segura de versão |
| 0.2.6 | Medir throughput/estagnação/cota | 0.2.1 | métricas de caminho crítico, review rework, quota e tempo perdido; sem pacing artificial |

A tarefa `0.2.2` (PR inofensivo) continua prova útil de integração, mas **não certifica autonomia**. Para liberar execução desassistida prolongada, exigir ainda `0.2.0`, `0.2.4` e `0.2.5` com testes adversariais. `0.2.1` deve incluir suporte ao revisor estratégico e acoplamento ao supervisor por contrato, ainda que as implementações completas sejam tarefas separadas.
