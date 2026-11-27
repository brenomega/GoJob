"""GoJob *development* runner v3.x — the Codex agents build the PRODUCT.

Never imports job-search/application code. Host controller alone performs GitHub writes,
Codex developers only edit a task worktree. Every review and CI binds exact base/head.
"""
from __future__ import annotations
import argparse, fcntl, hashlib, json, os, re, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from . import VERSION, MODEL, EFFORT
from .agent_privacy import redact, report_context
from .review_policy import collect_reviews, disposition, review_feedback, MAX_REVIEW_ROUNDS
from .codex import Codex
from .context import context, render
from .state import Journal, load, save_atomic
from .tools import (GitHub,git,command,assert_clean,commit_audit,verify_product,
                    PolicyFailure, BaseAdvanced, ExternalFailure,TransientFailure,MergeProtectionRequired,apply_commit_plan,worktree_changes,
                    product_code_changes,is_product_test_path)

SCHEMAS=Path(__file__).resolve().parent/'schemas'
TEST_STAGES=('TEST_IN_PROGRESS','TEST_RECOVERY')

class Runner:
    def __init__(self,repo,state_root=None,codex=None,scm=None):
        self.repo=Path(repo).expanduser().resolve()
        repo_key=hashlib.sha256(str(self.repo).encode()).hexdigest()[:16]
        self.root=Path(state_root or Path.home()/'.local/share/gojob-runner/state'/repo_key).expanduser().resolve()
        self.root.mkdir(parents=True,exist_ok=True,mode=0o700)
        self.state_path=self.root/'state.json'
        self.state=load(self.state_path,{'runner_version':VERSION,'tasks':{},'repo_identity':str(self.repo)})
        if self.state.get('repo_identity') not in (None,str(self.repo)):
            raise PolicyFailure('Estado persistido pertence a outro repositório; não reutilizar checkpoints')
        if 'repo_identity' not in self.state and self.state.get('tasks'):
            raise PolicyFailure('Estado legado sem identidade de repositório: migração explícita necessária')
        self.state['repo_identity']=str(self.repo)
        self.journal=Journal(self.root)
        self.lock=threading.RLock()
        self.codex=codex or Codex()
        self.scm=scm or GitHub(self.repo)
    def entry(self,tid):
        with self.lock:return dict(self.state['tasks'].get(tid,{}))
    def record(self,tid,event,**values):
        with self.lock:
            item=self.state['tasks'].setdefault(tid,{})
            for key in ('reason','blocked_reason'):
                if key in values:
                    values[key]=redact(values[key])
            for key in ('report','blocker_diagnosis','blocker_repair_report','integrator'):
                if key in values:
                    values[key]=report_context(values[key])
            item.update(values);item['updated_at']=time.time()
            save_atomic(self.state_path,self.state)
            self.journal.event(event,tid,{'stage':item.get('stage'),'head':item.get('head'),'pr':item.get('pr')})
    def branch(self,task):
        slug=re.sub(r'[^a-z0-9]+','-',task['title'].lower()).strip('-')[:40]
        return f"{task.get('type','feat')}/{task['id']}-{slug}"
    def worktree(self,task,branch):
        # task id is strict EAP, not a user path.
        loc=self.root/'worktrees'/task['id']
        loc.parent.mkdir(exist_ok=True)
        if loc.exists():
            if git(loc,'branch','--show-current')!=branch:
                raise PolicyFailure('Worktree aponta a branch inesperada; não apagar nem trocar')
            return loc
        # Git disallows second checkout of a branch, protecting against duplication.
        existing=git(self.repo,'branch','--list',branch)
        if existing:
            git(self.repo,'worktree','add',str(loc),branch)
        else:
            git(self.repo,'worktree','add','-b',branch,str(loc),'origin/develop')
        return loc
    def _prompt(self,role,task,work,base,extras=''):
        pack=context(work,task,base,role)
        return (f'PAPEL: {role}. Leia o AGENTS.md na BASE e arquivos atuais da branch. '
          'Apenas a ferramenta Codex altera conteúdo; não execute gh, push, pull, rebase, reset, stash, clean ou merge. '
          'Não toque em segredos, perfis pessoais ou autenticação de usuário. '
          'O objetivo é desenvolver o produto GoJob, NÃO inventar currículos nem inserir IA no produto sem justificativa. '
          'Se não for possível verificar, sinalize BLOCKED. '
          f'CONTEXTO DE BASE (SHA fixo):\n{render(pack)}\nINSTRUÇÕES ESPECÍFICAS:\n{extras}')
    def _agent(self,role,prompt,work,schema,write=False):
        return self.codex.run(role,prompt,work,SCHEMAS/schema,write=write)
    def _verify(self,task,work,base):
        assert_clean(work)
        audit=commit_audit(work,base,task['id'])
        changed=git(work,'diff','--name-only',f'{base}..HEAD').splitlines()
        immutable_without_human=(
            'AGENTS.md', 'docs/PRODUCT_NORTH_STAR.md',
            'docs/SECURITY_POLICY.md', 'docs/DECISION_LEDGER.md',
        )
        if any(x.startswith(immutable_without_human) for x in changed):
            raise PolicyFailure('Alteração de compromisso explícito do usuário requer autorização humana, não merge automático')
        if task.get('type')!='runner_fix':
            protected=(
                'automation/gojob_runner/', 'automation/supervisor/', 'automation/tests/',
                '.github/workflows/', 'config/quality_gates.json', 'AGENTS.md',
                'RUNNER_VERSION', 'CHANGELOG_RUNNER.md',
                'docs/PRODUCT_NORTH_STAR.md', 'docs/SECURITY_POLICY.md',
                'docs/TESTING_POLICY.md', 'docs/DECISION_LEDGER.md',
                'docs/BACKLOG.json', 'docs/ROADMAP.md',
                'docs/ENGINEERING_POLICY.md', 'docs/USER_EXPERIENCE.md',
                'docs/STRATEGIC_REVIEW.md',
            )
            if any(x.startswith(protected) for x in changed):
                raise PolicyFailure('Task de produto tentou modificar runner/governança/CI. Abrir proposta separada para revisão de política')
        verification=verify_product(work,task,base)
        return audit,verification
    def _delegate_product_tests(self,task,work,base):
        """Only Codex TEST ENGINEER writes product tests; controller gates outputs.

        A resumed task never fabricates test success. The HEAD anchors the exact
        implementation tested; a repair or rebase invalidates prior assessment.
        """
        if task.get('type')=='runner_fix':return
        sources=product_code_changes(work,base)
        if not sources:return
        tid=task['id']
        head=git(work,'rev-parse','HEAD')
        saved=self.entry(tid)
        staged,dirty=worktree_changes(work)
        if staged:raise PolicyFailure('Índice staged antes do Test Engineer; preservar para diagnóstico')
        if dirty:
            if (saved.get('stage') not in TEST_STAGES
                or saved.get('test_source_head')!=head or not all(map(self._product_test_path,dirty))):
                raise PolicyFailure('Diff de testes interrompido diverge do papel ou do HEAD; preservar para diagnóstico')
        elif saved.get('test_source_head')==head and saved.get('test_head')==head:
            return
        self.record(tid,'TEST_ENGINEER_START',stage='TEST_IN_PROGRESS',test_source_head=head)
        prompt=self._prompt('INDEPENDENT TEST ENGINEER',task,work,base,
            'Você, e NÃO o runner, é responsável por conceber e implementar testes DO PRODUTO. '
            'Comece por critérios de aceite, ameaças e contratos; derive oráculos independentes '
            'antes de confiar nas escolhas do Developer. Inspecione todos os níveis de testes; '
            'reutilize ou fortaleça, sem bloat. Use mocks apenas onde não escondam o risco. '
            'Escreva SOMENTE testes e fixtures em caminhos de teste; não modifique o produto, CI, '
            'contratos, versão ou governança. Não execute git write; devolva commit_plan exato. '
            'Se os testes existentes já detectam os riscos desta mudança, NÃO crie casos redundantes; '
            'explique quais testes, quais oráculos, quais falhas plausíveis e por que não houve diffs. '
            'Se houver edições de testes interrompidas, inspecione o diff, preserve o trabalho e conclua o plano. '
            f'HEAD do produto: {head}. Fontes alteradas: {sources}. '
            'Todo resultado de integração não testado deve aparecer como limitação, nunca PASS.')
        report=self._agent('independent-test-engineer',prompt,work,'test_engineer.json',write=True)
        if report.get('status')!='DONE':
            self.record(tid,'TEST_ENGINEER_BLOCK',stage='BLOCKED',test_report=report)
            raise PolicyFailure('Test Engineer bloqueou: revisão/especificação necessária')
        changed=worktree_changes(work)[1]
        forbidden=[name for name in changed if not self._product_test_path(name)]
        if forbidden:
            raise PolicyFailure('Test Engineer alterou fora dos caminhos de teste: '+str(forbidden[:8]))
        if changed:
            apply_commit_plan(work,tid,report)
            if git(work,'rev-parse','HEAD')==head:
                raise PolicyFailure('Plano de commits do Test Engineer não avançou o HEAD')
        else:
            # No-op is legitimate only with a substantiated explanation of
            # existing concrete test evidence, not merely DONE or green CI.
            if not report.get('existing_test_evidence') or not report.get('failure_modes'):
                raise PolicyFailure('Test Engineer não adicionou testes nem identificou oráculos existentes')
        completed=git(work,'rev-parse','HEAD')
        self.record(tid,'TEST_ENGINEER_COMPLETE',stage='TESTED',test_source_head=completed,
                    test_head=completed,test_report=report)

    @staticmethod
    def _product_test_path(path):
        return is_product_test_path(path) and path!='tests/test_bootstrap.py' and not path.startswith('automation/')

    def _validate_review(self,report,head,base,decision_key):
        if report.get('head_sha')!=head or report.get('base_sha')!=base:
            raise PolicyFailure('Parecer não corresponde ao SHA atual')
        if decision_key=='strategic' and not all(report.get(k) for k in ('opportunity_cost','alternative','north_star_impact')):
            raise PolicyFailure('Revisão estratégica superficial')
        if decision_key=='technical' and (not isinstance(report.get('real_evidence'),list)
            or not report.get('real_evidence') or not report.get('tests_assessed')):
            raise PolicyFailure('Revisão técnica sem avaliação de testes e evidências reais')
        if (not isinstance(report.get('findings'),list) or not isinstance(report.get('rationale'),str)
            or len(report['rationale'].strip())<12):
            raise PolicyFailure('Parecer sem fundamentação verificável')
    def synchronize_base(self,task):
        """Append a safe merge from latest develop: no rebase, reset or force-push.

        A new head invalidates both prior reviews and CI. If merge conflicts cannot be
        resolved, preserve dirty worktree and checkpoint rather than hiding history.
        """
        tid=task['id'];saved=self.entry(tid)
        branch=saved.get('branch') or self.branch(task)
        work=self.worktree(task,branch)
        assert_clean(work)
        with self.lock:
            git(self.repo,'fetch','origin','develop',timeout=240)
        latest=git(work,'rev-parse','origin/develop')
        old_head=git(work,'rev-parse','HEAD')
        old_base=saved.get('base')
        if latest==old_base:
            raise TransientFailure('PR base avançou mas fetch ainda não propagou; retry limitado')
        self.record(tid,'BASE_SYNC_INTENT',stage='SYNCING',previous_base=old_base,new_base=latest)
        try:
            git(work,'merge','--no-ff','-m',f'chore(sync): integre develop à tarefa [#{tid}]','origin/develop',timeout=240)
        except ExternalFailure:
            # Resolver is a separate workspace-write agent; it may only complete the merge.
            report=self._agent('merge-conflict-resolver',self._prompt('MERGE CONFLICT RESOLVER',task,work,old_base or latest,
                f'HEAD antigo {old_head}. Resolva SOMENTE conflitos de merge com origin/develop. '
                'Preserve ambas as mudanças de negócio, não rebase/reset/stash/force-push. '
                f'Resolva arquivos e devolva plano; o controlador concluirá merge commit com título chore(sync): integre develop à tarefa [#{tid}]. '
                'Teste os contratos afetados. Se impossível, BLOCKED.'),work,'developer.json',write=True)
            if report.get('status')!='DONE':
                raise PolicyFailure('Conflito de merge não resolvido; worktree preservada')
            # Unmerged index entries persist until *host git add*. The agent only
            # edits resolved content, it cannot stage paths in the Codex sandbox.
            # Verify all conflicted files were resolved in the filesystem first.
            unresolved=git(work,'diff','--name-only','--diff-filter=U').splitlines()
            for name in unresolved:
                candidate=work/name
                if candidate.is_file() and any(marker in candidate.read_bytes() for marker in (b'<<<<<<< ',b'=======\n',b'>>>>>>> ')):
                    raise PolicyFailure('Marcadores de conflito persistem: '+name)
            # The host, not the sandboxed Codex session, completes the merge commit.
            _,changed=worktree_changes(work)
            from .tools import validate_path
            for path in changed:validate_path(work,path)
            git(work,'add','--all')
            if git(work,'ls-files','-u'):
                raise PolicyFailure('Resolução deixou conflitos no índice Git; não confirmar')
            git(work,'commit','-m',f'chore(sync): integre develop à tarefa [#{tid}]')
        assert_clean(work)
        new_head=git(work,'rev-parse','HEAD')
        if new_head==old_head or git(work,'merge-base',latest,new_head)!=latest:
            raise PolicyFailure('Merge da base não confirmado; não publicar HEAD antigo')
        self._delegate_product_tests(task,work,latest)
        audit,verification=self._verify(task,work,latest)
        new_head=audit['head']
        git(work,'push','origin',branch,timeout=240)
        self.record(tid,'BASE_SYNCED',stage='PR',base=latest,head=new_head,
                    review_head=None,ci_head=None,strategic=None,technical=None,local_gates=verification)
        return {'old_head':old_head,'new_head':new_head,'new_base':latest}

    def _apply_developer_plan(self, work, tid, report):
        _, dirty = worktree_changes(work)
        forbidden = [path for path in dirty if is_product_test_path(path)
                     or path.startswith(('automation/', '.github/', 'docs/RUNNER_'))
                     or path in {'AGENTS.md', 'RUNNER_VERSION', 'CHANGELOG_RUNNER.md',
                                 'docs/RELEASE_ARCHITECTURE.md'}]
        if forbidden:
            raise PolicyFailure('Diff fora do papel Developer; preservar para reconciliação pelo responsável')
        return apply_commit_plan(work, tid, report)

    def _blocked_resolution(self,task):
        """One bounded independent diagnosis and repair of a previously BLOCKED task.

        This never certifies acceptance, changes governance, or bypasses reviews.
        Only DEV BLOCKED and repair-after-REVISE have safe automatic paths.
        All other policy refusals remain terminal and visible.
        """
        tid=task['id'];saved=self.entry(tid)
        if saved.get('stage')!='BLOCKED':
            raise PolicyFailure('Recovery requires persisted BLOCKED task')
        attempts=int(saved.get('blocker_recovery_attempts') or 0)
        if attempts>=2:
            self.record(tid,'BLOCKER_EXHAUSTED',stage='NEEDS_USER',
                        blocked_reason=saved.get('reason','Sem progresso após duas tentativas'))
            return {'task':tid,'state':'NEEDS_USER','reason':'Limite de recuperação atingido'}
        branch=saved.get('branch') or self.branch(task)
        work=self.worktree(task,branch)
        staged,dirty=worktree_changes(work)
        if staged:
            self.record(tid,'BLOCKER_STAGED',stage='NEEDS_USER',
                        blocked_reason='Índice Git staged exige reconciliação; não descartar trabalho')
            return {'task':tid,'state':'NEEDS_USER','reason':'Índice staged desconhecido'}
        if not saved.get('pr') and not (isinstance(saved.get('report'),dict)
                                          and saved['report'].get('status')=='BLOCKED'
                                          and 'Developer relatou bloqueio' in saved.get('reason','')):
            self.record(tid,'BLOCKER_POLICY_EXCLUDED',stage='NEEDS_USER',
                        blocked_reason='Falha de política/estado sem bloqueio explícito do Developer')
            return {'task':tid,'state':'NEEDS_USER','reason':'Bloqueio não delegável ao especialista'}
        # Inspect the original review decisions and exact HEAD independently.
        current_head=git(work,'rev-parse','HEAD')
        has_pr=bool(saved.get('pr'))
        base=saved.get('base') or git(work,'merge-base','HEAD','origin/develop')
        if has_pr:
            reviewed=self._saved_review(saved,saved['pr'],current_head,base)
            if not reviewed:
                self.record(tid,'BLOCKER_STALE_REVIEW',stage='NEEDS_USER',
                            blocked_reason='PR sem parecer verificável para o snapshot atual')
                return {'task':tid,'state':'NEEDS_USER','reason':'Revisão indisponível para PR, HEAD e BASE'}
            verdict=disposition(reviewed['strategic'],reviewed['technical'])
            if verdict.action=='NEEDS_USER':
                self.record(tid,'BLOCKER_HUMAN_GATE',stage='NEEDS_USER',blocked_reason=verdict.reason)
                return {'task':tid,'state':'NEEDS_USER','reason':verdict.reason}
            if verdict.action=='ACCEPT':
                self.record(tid,'BLOCKER_NON_REVIEW',stage='NEEDS_USER',
                            blocked_reason='Parecer aprovado; recusa de integrador/CI não autoriza edição')
                return {'task':tid,'state':'NEEDS_USER','reason':'Sem reparo de revisão autorizado'}
            if int(saved.get('review_rounds_total') or 0)>=MAX_REVIEW_ROUNDS:
                self.record(tid,'BLOCKER_REVIEW_BUDGET',stage='NEEDS_USER',
                            blocked_reason='Limite de três rodadas de revisão esgotado')
                return {'task':tid,'state':'NEEDS_USER','reason':'Review budget exhausted'}
            self.scm.snapshot(saved['pr'],branch,current_head,base)
        # Diagnose only after confirming the scope and any review authorization.
        summary={key:report_context(saved.get(key)) for key in
                 ('reason','report','findings','strategic','technical','review_head','review_history',
                  'review_rounds_total','pr','updated_at')}
        prompt=self._prompt('INDEPENDENT BLOCKER DIAGNOSTICIAN',task,work,base,
            'Não confunda problema real de acesso, autenticação, falta de dados ou política com bug do runner. '
            'Diga se um NOVO agente pode produzir mudança legítima, usando somente evidências acessíveis; '
            'NEEDS_USER quando faltar acesso autorizado, prova externa ou requisito impossível; '
            'POLICY_STOP se tentar reduzir o escopo, mudar aceitação ou contrariar um reviewer. '
            'Não invente resultados de inspeção local ou testes. Não edite arquivos. '
            f'REGISTRO DO BLOQUEIO: {json.dumps(summary,ensure_ascii=False,default=str)[:9500]} '
            f'HEAD ATUAL: {current_head}; worktree dirty={bool(dirty)}. '
            'Se houver REVISE, priorize corrigir os achados existentes, sem alterar as regras da Issue.')
        diagnosis=self._agent('blocker-diagnostician',prompt,work,'blocker_triage.json',write=False)
        decision=diagnosis.get('decision')
        evidence=diagnosis.get('evidence')
        if (not isinstance(evidence,list) or not any(isinstance(item,str) and item.strip() for item in evidence)
            or not diagnosis.get('cause') or not diagnosis.get('plan')):
            raise PolicyFailure('Diagnóstico sem evidências, causa e plano verificáveis')
        if decision not in ('REPAIR','NEEDS_USER','POLICY_STOP'):
            raise PolicyFailure('Diagnóstico de bloqueio inválido: sem decisão autorizada')
        diagnosis=report_context(diagnosis)
        self.record(tid,'BLOCKER_DIAGNOSED',blocker_diagnosis=diagnosis,
                    blocker_recovery_attempts=attempts+1)
        if decision!='REPAIR':
            self.record(tid,'BLOCKER_NEEDS_USER',stage='NEEDS_USER',
                        blocked_reason=diagnosis.get('cause','Bloqueio não reparável de forma segura'))
            return {'task':tid,'state':'NEEDS_USER','reason':decision}
        # A *new* Codex session, not the original Developer or Reviewer, attempts
        # a scoped modification; the trusted controller performs Git writes.
        prompt=self._prompt('INDEPENDENT BLOCKER REPAIR SPECIALIST',task,work,base,
            'Você é outro agente e NÃO pode aprovar o próprio trabalho. '
            'Corrija o problema causal e preserve todas as restrições/reviews existentes. '
            'Não altere testes do produto: o Test Engineer será convocado pelo controlador. '
            'Nunca executar Git write, nem reduzir critérios de aceitação, forjar medições ou acessar segredos. '
            f'DIAGNÓSTICO: {json.dumps(diagnosis,ensure_ascii=False)[:5500]} '
            f'BLOQUEIO: {json.dumps(summary,ensure_ascii=False,default=str)[:7500]} '
            f'HEAD ATUAL {current_head}, worktree dirty={bool(dirty)}. '
            f'Forneça commit_plan exato com [#{tid}], sem force-push/amend. '
            'Se não puder produzir mudança legítima, responda BLOCKED e explique o requisito faltante.')
        # Persist intent *before* invoking an agent that may edit the worktree.
        self.record(tid,'BLOCKER_REPAIR_START',stage='BLOCKER_REPAIRING',
                    blocker_source_head=current_head)
        try:
            report=self._agent('blocker-repair-specialist',prompt,work,'developer.json',write=True)
        except (ExternalFailure,TransientFailure):
            # If edits happened before CLI transport failed, preserve them and let
            # the following cycle inspect the tree instead of discarding edits.
            self.record(tid,'BLOCKER_AGENT_INTERRUPTED',stage='BLOCKED',
                        blocker_interrupted=True)
            raise
        self.record(tid,'BLOCKER_REPAIR_REPORT',blocker_repair_report=report)
        if report.get('status')!='DONE':
            self.record(tid,'BLOCKER_REPAIR_NOT_DONE',stage='NEEDS_USER',
                        blocked_reason=str(report.get('summary','Agente não solucionou'))[:400])
            return {'task':tid,'state':'NEEDS_USER','reason':'Especialista não resolveu'}
        changed=worktree_changes(work)[1]
        if not changed:
            self.record(tid,'BLOCKER_NO_CHANGE',stage='NEEDS_USER',
                        blocked_reason='Especialista declarou DONE sem alterar arquivos')
            return {'task':tid,'state':'NEEDS_USER','reason':'Nenhuma correção verificável'}
        self._apply_developer_plan(work,tid,report)
        actual=git(work,'rev-parse','HEAD')
        if actual==current_head:
            raise PolicyFailure('Correção não avançou o HEAD; sem progresso verificável')
        # Existing PR stays open; no extra PR is created. Next normal execution
        # redoes Test Engineer, local gates, review and CI for the new head.
        self.record(tid,'BLOCKER_REPAIRED',stage='RECOVERED',head=actual,
                    blocker_previous_head=current_head,review_head=None,ci_head=None)
        return {'task':tid,'state':'RECOVERED','head':actual}

    def run_one(self,task,max_repairs=2):
        """Bounded automatic base reconciliation for independent parallel PRs."""
        for attempt in range(3):
            try:return self._run_task(task,max_repairs)
            except BaseAdvanced:
                if attempt>=2:raise PolicyFailure('Base avançou repetidamente; escalar em vez de looping')
                self.synchronize_base(task)
        raise PolicyFailure('Sincronização não convergiu')

    def _saved_review(self,saved,pr,head,base):
        if not (pr and head and base and saved.get('pr')==pr):
            return None
        for row in reversed(saved.get('review_history') or []):
            if (row.get('pr',saved['pr']),row.get('head'),row.get('base'))==(pr,head,base):
                for role in ('strategic','technical'):
                    self._validate_review(row[role],head,base,role)
                return row
        if saved.get('review_head') == head and saved.get('base') == base:
            pair = {role: saved.get(role) for role in ('strategic','technical')}
            if all(isinstance(report,dict) and (report.get('head_sha'),report.get('base_sha'))==(head,base)
                   for report in pair.values()):
                for role, report in pair.items():
                    self._validate_review(report,head,base,role)
                return {'pr':pr,'head':head,'base':base,'round':int(saved.get('review_rounds_total') or 1),**pair}
        return None

    def _finish_task_merge(self, tid, branch, saved, remote):
        pr, head, base = (saved[key] for key in ('pr','head','base'))
        intent = saved.get('merge_intent') or {}
        approval = self._saved_review(saved,pr,head,base)
        identity = {'pr':pr, 'head':head, 'base':base}
        proof = intent.get('ci')
        if (not approval or disposition(approval['strategic'],approval['technical']).action!='ACCEPT'
            or not isinstance(proof,dict)
            or any(intent.get(key)!=value or proof.get(key)!=value for key,value in identity.items())):
            raise PolicyFailure('Merge sem intenção e provas vinculadas ao PR, HEAD e BASE')
        if remote.get('state')=='MERGED':
            self.scm.confirm_merge(remote,branch,head,base)
        elif remote.get('state')=='OPEN' and saved['stage']=='MERGE_INTENT':
            try:
                remote=self.scm.merge(pr,branch,head,base)
            except MergeProtectionRequired as exc:
                self.record(tid,'MERGE_PROTECTION_REQUIRED',stage='NEEDS_USER',blocked_reason=str(exc))
                raise
        else:
            raise PolicyFailure('Estado remoto diverge da intenção de merge')
        self.record(tid,'MERGE_CONFIRMED',stage='MERGED',merge=remote)
        self.scm.issue_close(saved['issue'])
        self.record(tid,'DONE',stage='DONE')
        return {'task':tid,'state':'DONE','pr':pr,'head':head}

    def _run_task(self,task,max_repairs=2):
        validate_task(task)
        tid=task['id'];saved=self.entry(tid)
        if saved.get('stage')=='DONE':return {'task':tid,'state':'DONE','cached':True}
        if saved.get('stage')=='NEEDS_USER':
            raise PolicyFailure(saved.get('blocked_reason') or 'Tarefa aguarda decisão explícita')
        checkpoint=self._saved_review(saved, saved.get('pr'), saved.get('head'), saved.get('base'))
        if checkpoint:
            verdict=disposition(checkpoint['strategic'],checkpoint['technical'])
            if verdict.action=='NEEDS_USER':
                self.record(tid,'REVIEW_HUMAN_GATE',stage='NEEDS_USER',blocked_reason=verdict.reason)
                raise PolicyFailure(verdict.reason)
        for dep in task['dependencies']:
            if self.entry(dep).get('stage')!='DONE':
                raise PolicyFailure(f'Dependência {dep} não concluída')
        branch=saved.get('branch') or self.branch(task)
        # Retrying a completed external merge is forbidden unless prior review/CI are attested.
        if saved.get('pr'):
            remote=self.scm.pr(saved['pr'])
            if remote.get('state')=='MERGED' or saved.get('stage') in ('MERGE_INTENT','MERGED'):
                return self._finish_task_merge(tid,branch,saved,remote)
        with self.lock:
            git(self.repo,'fetch','origin','develop',timeout=240)
        issue=saved.get('issue') or self.scm.issue(task)
        testing=saved.get('stage') in TEST_STAGES
        self.record(tid,'ISSUE_CONFIRMED',issue=issue,branch=branch,stage=saved['stage'] if testing else 'ISSUE')
        work=self.worktree(task,branch)
        base=git(work,'merge-base','HEAD','origin/develop')
        original=saved.get('base')
        if original and original!=base:
            raise PolicyFailure('A base da tarefa mudou; replanejar sem rebase automático')
        self.record(tid,'BASE_PINNED',stage=saved['stage'] if testing else 'WORKTREE',base=base)
        commits=git(work,'rev-list','--count',f'{base}..HEAD')
        staged,dirty=worktree_changes(work)
        if staged:
            raise PolicyFailure('Índice staged inesperado: preservar para diagnóstico, nunca reset')
        if dirty and testing:
            if (task.get('type')=='runner_fix' or commits=='0'
                or not all(map(self._product_test_path,dirty))
                or saved.get('test_source_head')!=git(work,'rev-parse','HEAD')):
                raise PolicyFailure('Diff de testes interrompido diverge do papel ou do HEAD; preservar para diagnóstico')
            self._delegate_product_tests(task,work,base)
            _,dirty=worktree_changes(work)
            if dirty:
                raise PolicyFailure('Diff de testes interrompido sem plano verificável; preservar para diagnóstico')
        if dirty and not (checkpoint and checkpoint['head']==git(work,'rev-parse','HEAD')):
            # After Codex timeout the model may have edited files but its final
            # JSON/commit plan was lost. Resume on exactly this worktree.
            self.record(tid,'DEV_DIRTY_RECOVERY',stage='DEV_RECOVERY',dirty_count=len(dirty))
            report=self._agent('developer-recovery',self._prompt('DEVELOPER RECOVERY',task,work,base,
                'Arquivos editados preservados após interrupção. Inspecione o diff atual, '
                'não repita trabalho, conclua apenas lacunas e forneça commit_plan '
                'EXATO para todas as alterações. Não execute Git write.'),work,'developer.json',write=True)
            if report.get('status')!='DONE':raise PolicyFailure('Recuperação da edição não concluída')
            self._apply_developer_plan(work,tid,report)
            self.record(tid,'DEV_RECOVERED',stage='DEV_EDITED')
            commits=git(work,'rev-list','--count',f'{base}..HEAD')
        if commits=='0':
            assert_clean(work)
            prompt=self._prompt('DEVELOPER',task,work,base,
                'Implemente a Issue com alterações de arquivos e devolva um commit_plan exato. O controlador executará os commits (Git é read-only no sandbox). Agente INDEPENDENT TEST ENGINEER será convocado depois para autoria dos testes do produto; concentre-se no código de produto e documentação necessária. '
                f"Cada commit: tipo(escopo): imperativo em português [#{tid}]. "
                'Máximo oito commits, ideal 2–4, até 600 linhas alteradas por commit. '
                'Proibido criar PR/Issue, force-push, remover testes falhando ou reescrever contratos para passar CI.')
            self.record(tid,'DEVELOPER_START',stage='DEVELOPING',role='developer')
            dev=self._agent('developer',prompt,work,'developer.json',write=True)
            if dev.get('status')!='DONE':
                self.record(tid,'DEVELOPER_BLOCKED',stage='BLOCKED',report=dev)
                raise PolicyFailure('Developer relatou bloqueio real')
            # The agent writes source, the controller executes its scoped commit plan.
            if not worktree_changes(work)[1]:
                raise PolicyFailure('Developer declarou DONE, mas não produziu mudanças verificáveis')
            self._apply_developer_plan(work,tid,dev)
            self.record(tid,'DEVELOPER_FINISHED',stage='DEV_EDITED')
        if dirty and checkpoint and checkpoint['head']==git(work,'rev-parse','HEAD'):
            if disposition(checkpoint['strategic'],checkpoint['technical']).action!='REPAIR':
                raise PolicyFailure('Alterações locais após aprovação; preservar worktree')
            head=checkpoint['head']
            verified=saved.get('local_gates')
        else:
            self._delegate_product_tests(task,work,base)
            audit,verified=self._verify(task,work,base)
            head=audit['head']
        self.record(tid,'LOCAL_GATES',stage='VERIFIED',head=head,local_gates=verified)
        # Idempotent remote publication. Never force-push.
        git(work,'push','-u','origin',branch,timeout=240)
        pr=self.entry(tid).get('pr') or self.scm.ensure_pr(task,branch,issue)
        self.scm.snapshot(pr,branch,head,base)
        self.record(tid,'PR_CONFIRMED',stage='PR',pr=pr,head=head)
        # The per-PR budget is durable: neither restart nor specialist recovery
        # may silently reset the number of review rounds. Three *full* rounds
        # include at most two developer correction cycles.
        while True:
            previous=self.entry(tid)
            reviewed=int(previous.get('review_rounds_total') or 0)
            ci_proof=self.scm.checks(pr,head,base,branch)
            checkpoint=self._saved_review(previous,pr,head,base)
            if checkpoint:
                strategy=checkpoint['strategic']
                technical=checkpoint['technical']
                attempt=checkpoint['round']-1
                verdict=disposition(strategy,technical)
            else:
                if reviewed>=MAX_REVIEW_ROUNDS:
                    reason='Três rodadas completas de review sem convergência'
                    self.record(tid,'REVIEW_BUDGET_EXHAUSTED',stage='NEEDS_USER',blocked_reason=reason)
                    raise PolicyFailure(reason)
                attempt=reviewed
                key = {'pr': pr, 'head': head, 'base': base, 'round': attempt+1}
                partial = previous.get('review_pending') or {}
                if any(partial.get(field) != value for field, value in key.items()):
                    partial = key

                def invoke(role):
                    self.record(tid, role.upper()+'_REVIEW_START', stage='REVIEWING_'+role.upper(),
                                review_head=head, review_attempt=attempt)
                    focus = (
                        'Avalie direção, oportunidade marginal, trade-offs, métricas e alternativas.'
                        if role == 'strategic' else
                        'Inspecione casos negativos, concorrência, segurança, oráculos e integração real.'
                    )
                    return self._agent(role+'-reviewer', self._prompt(role.upper()+' REVIEWER', task, work, base,
                        f'PR #{pr} HEAD {head}. {focus} Examine diff e evidências reais. '
                        'Indique achados corrigíveis ou a obrigação não delegável. Não altere arquivos.'),
                        work, role+'.json', write=False)

                collect_reviews(partial, invoke,
                    lambda report, role: self._validate_review(report, head, base, role),
                    lambda row: self.record(tid, 'REVIEW_ROLE_SAVED', review_pending=dict(row)))
                strategy, technical = partial['strategic'], partial['technical']
                history=list(previous.get('review_history') or [])
                history.append(partial)
                verdict=disposition(strategy,technical)
                self.record(tid,'REVIEW',stage='REVIEW',review_head=head,
                            strategic=strategy,technical=technical,review_attempt=attempt,
                            review_rounds_total=attempt+1,review_history=history,review_pending=None,
                            review_disposition=verdict.action,review_pr=pr,review_base=base)
            self.scm.snapshot(pr,branch,head,base)
            self.scm.comment(pr,head,base,'STRATEGIC_REVIEW',strategy)
            self.scm.comment(pr,head,base,'TECHNICAL_REVIEW',technical)
            if verdict.action=='ACCEPT':
                break
            if verdict.action=='NEEDS_USER':
                self.record(tid,'REVIEW_HUMAN_GATE',stage='NEEDS_USER',blocked_reason=verdict.reason)
                raise PolicyFailure('Revisão exige autorização ou diagnóstico: '+verdict.reason)
            if attempt+1>=MAX_REVIEW_ROUNDS:
                self.record(tid,'REVIEW_EXHAUSTED',stage='NEEDS_USER',
                            blocked_reason='Três revisões completas sem aprovação; examinar histórico',
                            final_feedback=review_feedback(strategy,technical))
                self.scm.comment(pr,head,base,'REVIEW_ESCALATION',
                                 {'decision':'BLOCK','findings':strategy.get('findings',[])+technical.get('findings',[])})
                raise PolicyFailure('Três rodadas de revisão sem aprovação; consultar histórico da tarefa')
            prompt=self._prompt('DEVELOPER REPAIR',task,work,base,
                'A revisão anterior NÃO foi aprovada. Corrija a implementação em vez de repetir o parecer. '
                'Use TODA a fundamentação técnica e estratégica a seguir, mantenha a North Star e preserve testes. '
                'Não altere contratos/arquivos do runner; regressões de produto cabem ao Test Engineer independente. '
                'Se o revisor estiver exigindo objetivo conflitante com decisão confirmada, documente o conflito, '
                'não altere a North Star para forçar aprovação. '
                f'Rodada {attempt+1}/{MAX_REVIEW_ROUNDS}; HEAD {head}; '
                f'PARECERES: {json.dumps(review_feedback(strategy,technical),ensure_ascii=False)}. '
                f'Plano de commits novos com [#{tid}], nunca amend/force push.')
            self.record(tid,'REPAIR_START',stage='REPAIRING',repair_source_head=head)
            report=self._agent('developer-repair',prompt,work,'developer.json',write=True)
            if report.get('status')!='DONE':
                self.record(tid,'REPAIR_NOT_DONE',stage='BLOCKED',report=report,
                            reason='Implementador não concluiu correção; acionar especialista independente')
                raise PolicyFailure('Reparo não concluído; especialista investigará o bloqueio no ciclo seguinte')
            if not worktree_changes(work)[1]:
                self.record(tid,'REPAIR_NO_DIFF',stage='BLOCKED',report=report,
                            reason='Implementador não produziu diferença verificável')
                raise PolicyFailure('Reparo sem modificações: escalar diagnóstico sem novo review idêntico')
            self._apply_developer_plan(work,tid,report)
            self._delegate_product_tests(task,work,base)
            audit,verified=self._verify(task,work,base)
            if audit['head']==head:
                raise PolicyFailure('Reparo não avançou HEAD')
            head=audit['head']
            git(work,'push','origin',branch,timeout=240)
            self.record(tid,'REPAIR_PUSH',stage='PR',head=head,local_gates=verified,
                        review_head=None,ci_head=None)
        self.scm.snapshot(pr,branch,head,base)
        self.record(tid,'CI_PASS',stage='CI',ci_head=head,ci_proof=ci_proof)
        final=self._agent('integrator',self._prompt('INDEPENDENT INTEGRATOR',task,work,base,
            f'PR #{pr} HEAD {head}, BASE {base}. Analise resultados técnicos, estratégicos e CI. '
            'Se dúvida, BLOCK. Não faça merge diretamente; GitHub APPROVE nativo não é possível com mesmo usuário. '
            f'PARECER ESTRATÉGICO: {json.dumps(strategy,ensure_ascii=False)}\n'
            f'PARECER TÉCNICO: {json.dumps(technical,ensure_ascii=False)}\n'
            f'EVIDÊNCIA DE CI: {json.dumps(ci_proof,ensure_ascii=False)[:2500]}'),
            work,'integrator.json',write=False)
        if final.get('head_sha')!=head or final.get('base_sha')!=base or final.get('decision')!='MERGE':
            self.record(tid,'INTEGRATOR_BLOCK',stage='BLOCKED',integrator=final)
            raise PolicyFailure('Integrator recusou merge')
        current=self.entry(tid)
        approval=self._saved_review(current,pr,head,base)
        if (not approval or disposition(approval['strategic'],approval['technical']).action!='ACCEPT'
            or current.get('ci_head')!=head):
            raise PolicyFailure('Provas de revisão e CI não correspondem ao HEAD final')
        self.scm.snapshot(pr,branch,head,base)
        self.record(tid,'MERGE_INTENT',stage='MERGE_INTENT',integrator=final,
                    merge_intent={'pr':pr,'head':head,'base':base,'ci':ci_proof})
        return self._finish_task_merge(tid,branch,self.entry(tid),self.scm.pr(pr))


def validate_task(t):
    if not isinstance(t,dict) or not re.fullmatch(r'\d+(?:\.\d+){1,3}',str(t.get('id',''))):
        raise ValueError('ID EAP inválido')
    for k in ('title','goal','acceptance','dependencies'):
        if k not in t:raise ValueError('Tarefa sem '+k)
    if not all(isinstance(t[k],str) and t[k].strip() for k in ('title','goal')):
        raise ValueError('Tarefa com título/objetivo vazio')
    if t.get('type','feat') not in ('feat','fix','refactor','test','docs','chore','perf','ci','runner_fix'):
        raise ValueError('Tipo de tarefa inválido')
    if any(not isinstance(dep,str) for dep in t['dependencies']):
        raise ValueError('Dependência não textual')
    if not isinstance(t['acceptance'],list) or not t['acceptance'] or any(not isinstance(s,str) or not s.strip() for s in t['acceptance']):
        raise ValueError('Critérios inválidos')
    if not isinstance(t['dependencies'],list):raise ValueError('Dependências inválidas')

def read_tasks(path):
    data=json.loads(Path(path).read_text(encoding='utf-8'))
    tasks=data['tasks']
    ids=set()
    for t in tasks:
        validate_task(t)
        if t['id'] in ids:raise ValueError('Tarefa duplicada')
        ids.add(t['id'])
    for t in tasks:
        if any(d not in ids for d in t['dependencies']):raise ValueError('Dependência desconhecida')
    visiting=set();done=set();byid={t['id']:t for t in tasks}
    def visit(k):
        if k in visiting:raise ValueError('Dependência cíclica')
        if k in done:return
        visiting.add(k)
        for d in byid[k]['dependencies']:visit(d)
        visiting.remove(k);done.add(k)
    for k in ids:visit(k)
    return tasks

def exit_code(error):
    if isinstance(error,TransientFailure):return 75
    if isinstance(error,PolicyFailure):return 2
    if isinstance(error,ExternalFailure):return 76
    return 70

def main(argv=None):
    p=argparse.ArgumentParser(prog='gojob-runner')
    p.add_argument('--repo',default='.')
    p.add_argument('--tasks',help='Backlog JSON; default docs/BACKLOG.json in product repo')
    p.add_argument('--state-root')
    p.add_argument('--max-parallel',type=int,default=2)
    p.add_argument('--task-id')
    p.add_argument('--doctor',action='store_true')
    p.add_argument('--status',action='store_true')
    p.add_argument('--once',action='store_true',help='one eligible batch only')
    args=p.parse_args(argv)
    r=Runner(args.repo,args.state_root)
    if args.status:
        print(json.dumps({'version':VERSION,'state':r.state},ensure_ascii=False,indent=2));return 0
    if args.doctor:
        health={'version':VERSION,'model':MODEL,'effort':EFFORT,'repo':str(r.repo)}
        try:r.scm.preflight();health['github']='READY'
        except Exception as e:health['github']='BLOCKED: '+str(e)
        print(json.dumps(health,ensure_ascii=False,indent=2));return 0 if health['github']=='READY' else 76
    if not 1<=args.max_parallel<=3:raise ValueError('max-parallel 1..3')
    tasks=read_tasks(args.tasks or r.repo/'docs/BACKLOG.json')
    if args.task_id and args.task_id not in {t['id'] for t in tasks}:
        print('[TASK_UNKNOWN] EAP ausente no backlog: '+args.task_id,file=sys.stderr)
        return 2
    try:r.scm.preflight()
    except (PolicyFailure,ExternalFailure) as e:
        print('[PREFLIGHT] '+str(e),file=sys.stderr);return 76
    # Single active controller owns the task queue; per-task worktrees can run concurrently.
    lockfile=r.root/'runner.lock'
    with lockfile.open('a') as lf:
        try:fcntl.flock(lf.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print('[RUNNER_BUSY] another controller is active',file=sys.stderr);return 75
        code=0
        # Avoid one forever-BLOCKED task starving unrelated tasks. One bounded
        # specialist recovery per persisted blocker, never an endless retry.
        for cycle in range(len(tasks)+3):
            ready=[t for t in tasks if r.entry(t['id']).get('stage') not in
                ('DONE','BLOCKED','NEEDS_USER') and all(r.entry(dep).get('stage')=='DONE' for dep in t['dependencies'])]
            recoverable=[t for t in tasks if r.entry(t['id']).get('stage')=='BLOCKED'
                and int(r.entry(t['id']).get('blocker_recovery_attempts') or 0)<2
                and all(r.entry(dep).get('stage')=='DONE' for dep in t['dependencies'])]
            eligible=ready+recoverable
            if args.task_id:eligible=[t for t in eligible if t['id']==args.task_id]
            if not eligible:break
            selected=eligible[:args.max_parallel]
            def execute(task):
                if r.entry(task['id']).get('stage')=='BLOCKED':
                    outcome=r._blocked_resolution(task)
                    if outcome['state']=='NEEDS_USER':return outcome
                return r.run_one(task)
            with ThreadPoolExecutor(max_workers=len(selected)) as pool:
                futures={pool.submit(execute,t):t for t in selected}
                for future in as_completed(futures):
                    task=futures[future]
                    try:
                        result=future.result()
                        print(json.dumps(result,ensure_ascii=False),flush=True)
                    except Exception as e:
                        ec=exit_code(e);code=max(code,ec)
                        prev=r.entry(task['id'])
                        if ec==2 and prev.get('stage') in TEST_STAGES:
                            r.record(task['id'],'TEST_RECOVERY_RECONCILIATION',stage='NEEDS_USER',
                                     blocked_reason=str(e)[:500],blocked_from_stage=prev['stage'])
                        elif ec==2 and prev.get('stage')!='NEEDS_USER':
                            r.record(task['id'],'BLOCKED',stage='BLOCKED',
                                     reason=str(e)[:500],blocked_from_stage=prev.get('stage'))
                        r.journal.event('ERROR',task['id'],{'code':ec,'kind':type(e).__name__,
                            'message':str(e)[:350]})
                        print(f'[RUNNER_ERROR:{ec}] {task["id"]}: {type(e).__name__}: {e}',file=sys.stderr)
            if args.once or args.task_id:break
            if code in (70,75,76):break
        # Any task exhausting automatic recovery is an explicit unresolved
        # dependency; do not make it disappear behind a zero exit status.
        for task in tasks:
            record=r.entry(task['id'])
            if record.get('stage')=='BLOCKED' and int(record.get('blocker_recovery_attempts') or 0)>=2:
                r.record(task['id'],'BLOCKER_ATTEMPTS_EXHAUSTED',stage='NEEDS_USER',
                         blocked_reason=record.get('reason','Sem avanço após duas tentativas'))
        summary={stage:sum(1 for v in r.state['tasks'].values() if v.get('stage')==stage)
                 for stage in ('DONE','BLOCKED','NEEDS_USER')}
        print('[GOJOB_QUEUE] '+json.dumps(summary,ensure_ascii=False),flush=True)
        # A quiet supervisor run is not evidence that blocked tasks succeeded.
        if summary['NEEDS_USER'] or summary['BLOCKED']:
            attention={tid:{'stage':v.get('stage'),'reason':v.get('blocked_reason') or v.get('reason') or 'Consultar journal',
                            'pr':v.get('pr'),'review_rounds':v.get('review_rounds_total',0)}
                       for tid,v in r.state['tasks'].items() if v.get('stage') in ('NEEDS_USER','BLOCKED')}
            print('[GOJOB_ATTENTION] '+json.dumps(attention,ensure_ascii=False)[:2500],file=sys.stderr)
            # A documented human gate is not a Python/systemd process crash.
            # The supervisor should continue monitoring other independent work.
            if code not in (70,75,76):return 0
        return code if code!=2 else 0

if __name__=='__main__':sys.exit(main())
