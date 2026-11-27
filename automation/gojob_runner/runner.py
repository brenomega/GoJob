"""GoJob *development* runner v3.x — the Codex agents build the PRODUCT.

Never imports job-search/application code. Host controller alone performs GitHub writes,
Codex developers only edit a task worktree. Every review and CI binds exact base/head.
"""
from __future__ import annotations
import argparse, fcntl, hashlib, json, os, re, sys, threading, time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from . import VERSION, MODEL, EFFORT
from .codex import Codex
from .context import context, render
from .state import Journal, load, save_atomic
from .tools import (GitHub,git,command,assert_clean,commit_audit,verify_product,
                    PolicyFailure, BaseAdvanced, ExternalFailure,TransientFailure,apply_commit_plan,worktree_changes,
                    product_code_changes,is_product_test_path)

SCHEMAS=Path(__file__).resolve().parent/'schemas'

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
        if saved.get('test_source_head')==head and saved.get('test_head')==head:
            return
        staged,dirty=worktree_changes(work)
        if staged:raise PolicyFailure('Índice staged antes do Test Engineer; preservar para diagnóstico')
        if dirty and saved.get('stage') not in ('TEST_IN_PROGRESS','TEST_RECOVERY'):
            raise PolicyFailure('Arquivos sujos fora da etapa de testes; preservar para diagnóstico')
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
            f'HEAD do produto: {head}. Fontes alteradas: {sources}. '
            'Todo resultado de integração não testado deve aparecer como limitação, nunca PASS.')
        report=self._agent('independent-test-engineer',prompt,work,'test_engineer.json',write=True)
        if report.get('status')!='DONE':
            self.record(tid,'TEST_ENGINEER_BLOCK',stage='BLOCKED',test_report=report)
            raise PolicyFailure('Test Engineer bloqueou: revisão/especificação necessária')
        changed=worktree_changes(work)[1]
        forbidden=[name for name in changed if (not is_product_test_path(name) or
                   name=='tests/test_bootstrap.py' or name.startswith('automation/'))]
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

    def _validate_review(self,report,head,base,decision_key):
        if report.get('head_sha')!=head or report.get('base_sha')!=base:
            raise PolicyFailure('Parecer não corresponde ao SHA atual')
        if not isinstance(report.get('findings'),list) or not report.get('rationale'):
            raise PolicyFailure('Parecer sem fundamentação')
        if decision_key=='strategic' and not all(report.get(k) for k in ('opportunity_cost','alternative','north_star_impact')):
            raise PolicyFailure('Revisão estratégica superficial')
        if decision_key=='technical' and (not isinstance(report.get('real_evidence'),list)
            or not report.get('real_evidence') or not report.get('tests_assessed')):
            raise PolicyFailure('Revisão técnica sem avaliação de testes e evidências reais')
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
        git(work,'push','origin',branch,timeout=240)
        self.record(tid,'BASE_SYNCED',stage='PR',base=latest,head=new_head,
                    review_head=None,ci_head=None,strategic=None,technical=None,local_gates=verification)
        return {'old_head':old_head,'new_head':new_head,'new_base':latest}

    def run_one(self,task,max_repairs=2):
        """Bounded automatic base reconciliation for independent parallel PRs."""
        for attempt in range(3):
            try:return self._run_task(task,max_repairs)
            except BaseAdvanced:
                if attempt>=2:raise PolicyFailure('Base avançou repetidamente; escalar em vez de looping')
                self.synchronize_base(task)
        raise PolicyFailure('Sincronização não convergiu')

    def _run_task(self,task,max_repairs=2):
        validate_task(task)
        tid=task['id'];saved=self.entry(tid)
        if saved.get('stage')=='DONE':return {'task':tid,'state':'DONE','cached':True}
        for dep in task['dependencies']:
            if self.entry(dep).get('stage')!='DONE':
                raise PolicyFailure(f'Dependência {dep} não concluída')
        branch=saved.get('branch') or self.branch(task)
        # Retrying a completed external merge is forbidden unless prior review/CI are attested.
        if saved.get('pr'):
            remote=self.scm.pr(saved['pr'])
            if remote.get('state')=='MERGED':
                if not (saved.get('review_head')==saved.get('head') and saved.get('ci_head')==saved.get('head')):
                    raise PolicyFailure('Merge externo sem provas vinculadas ao HEAD; incidente, não certificar')
                self.scm.issue_close(saved['issue'])
                self.record(tid,'RECONCILED',stage='DONE',merge=remote.get('mergeCommit'))
                return {'task':tid,'state':'DONE','recovered':True}
        with self.lock:
            git(self.repo,'fetch','origin','develop',timeout=240)
        issue=saved.get('issue') or self.scm.issue(task)
        self.record(tid,'ISSUE_CONFIRMED',issue=issue,branch=branch,stage='ISSUE')
        work=self.worktree(task,branch)
        base=git(work,'merge-base','HEAD','origin/develop')
        original=saved.get('base')
        if original and original!=base:
            raise PolicyFailure('A base da tarefa mudou; replanejar sem rebase automático')
        self.record(tid,'BASE_PINNED',stage='WORKTREE',base=base)
        commits=git(work,'rev-list','--count',f'{base}..HEAD')
        staged,dirty=worktree_changes(work)
        if staged:
            raise PolicyFailure('Índice staged inesperado: preservar para diagnóstico, nunca reset')
        if dirty:
            # After Codex timeout the model may have edited files but its final
            # JSON/commit plan was lost. Resume on exactly this worktree.
            self.record(tid,'DEV_DIRTY_RECOVERY',stage='DEV_RECOVERY',dirty_count=len(dirty))
            report=self._agent('developer-recovery',self._prompt('DEVELOPER RECOVERY',task,work,base,
                'Arquivos editados preservados após interrupção. Inspecione o diff atual, '
                'não repita trabalho, conclua apenas lacunas e forneça commit_plan '
                'EXATO para todas as alterações. Não execute Git write.'),work,'developer.json',write=True)
            if report.get('status')!='DONE':raise PolicyFailure('Recuperação da edição não concluída')
            apply_commit_plan(work,tid,report)
            self.record(tid,'DEV_RECOVERED',stage='DEV_EDITED')
            commits=git(work,'rev-list','--count',f'{base}..HEAD')
        if commits=='0':
            assert_clean(work)
            prompt=self._prompt('DEVELOPER',task,work,base,
                'Implemente a Issue com alterações de arquivos e devolva um commit_plan exato. O controlador executará os commits (Git é read-only no sandbox). Agente INDEPENDENT TEST ENGINEER será convocado depois para autoria dos testes do produto; concentre-se no código de produto e documentação necessária. '
                f"Cada commit: tipo(escopo): imperativo em português [#{tid}]. "
                'Máximo oito commits, ideal 2–4, até 600 linhas alteradas por commit. '
                'Proibido criar PR/Issue, force-push, remover testes falhando ou reescrever contratos para passar CI.')
            dev=self._agent('developer',prompt,work,'developer.json',write=True)
            if dev.get('status')!='DONE':
                self.record(tid,'DEVELOPER_BLOCKED',stage='BLOCKED',report=dev)
                raise PolicyFailure('Developer relatou bloqueio real')
            # The agent writes source, the controller executes its scoped commit plan.
            if not worktree_changes(work)[1]:
                raise PolicyFailure('Developer declarou DONE, mas não produziu mudanças verificáveis')
            apply_commit_plan(work,tid,dev)
            self.record(tid,'DEVELOPER_FINISHED',stage='DEV_EDITED')
        self._delegate_product_tests(task,work,base)
        audit,verified=self._verify(task,work,base)
        head=audit['head']
        self.record(tid,'LOCAL_GATES',stage='VERIFIED',head=head,local_gates=verified)
        # Idempotent remote publication. Never force-push.
        git(work,'push','-u','origin',branch,timeout=240)
        pr=self.entry(tid).get('pr') or self.scm.ensure_pr(task,branch,issue)
        self.scm.snapshot(pr,branch,head,base)
        self.record(tid,'PR_CONFIRMED',stage='PR',pr=pr,head=head)
        last_findings=None
        for attempt in range(max_repairs+1):
            self.scm.snapshot(pr,branch,head,base)
            # Strategic reviewer never receives developer's rationale: context solely anchored to base+real diff.
            strategy=self._agent('strategic-reviewer',self._prompt('STRATEGIC REVIEWER',task,work,base,
                f'PR #{pr} HEAD {head}. Primeiro avalie direção, oportunidade marginal, trade-offs, métricas e alternativas. '
                'Um código correto para uma meta errada deve ser reprovado. Examine diff e arquivos reais; não altere arquivos.'),
                work,'strategic.json',write=False)
            technical=self._agent('technical-reviewer',self._prompt('TECHNICAL REVIEWER',task,work,base,
                f'PR #{pr} HEAD {head}. Inspecione implementação, casos negativos, concorrência, segurança, oráculos, '
                'testes existentes em todos níveis, integração real e possíveis testes de mutação. '
                'Não confunda mocks com evidência real; não altere arquivos.'),work,'technical.json',write=False)
            self._validate_review(strategy,head,base,'strategic')
            self._validate_review(technical,head,base,'technical')
            self.scm.snapshot(pr,branch,head,base)
            self.scm.comment(pr,head,base,'STRATEGIC_REVIEW',strategy)
            self.scm.comment(pr,head,base,'TECHNICAL_REVIEW',technical)
            self.record(tid,'REVIEW',stage='REVIEW',review_head=head,
                        strategic=strategy,technical=technical,review_attempt=attempt)
            if strategy['decision']=='PASS' and technical['decision']=='ACCEPT':break
            findings={'strategic':strategy.get('findings',[]),'technical':technical.get('findings',[])}
            if (strategy['decision']=='STOP' or technical['decision']=='BLOCK' or
                attempt>=max_repairs or findings==last_findings):
                self.record(tid,'REVIEW_BLOCK',stage='BLOCKED',findings=findings)
                raise PolicyFailure('Revisão bloqueou ou estagnou: escalar para agente de causa-raiz')
            last_findings=findings
            prompt=self._prompt('DEVELOPER REPAIR',task,work,base,
                'Repare SOMENTE os achados causais na implementação; testes de regressão cabem ao Test Engineer independente. '
                'Não altere contratos/arquivos do runner; não apague testes. '
                f'HEAD {head}; findings {json.dumps(findings,ensure_ascii=False)}. '
                f'Plano de commits novos com [#{tid}], nunca amend/force push.')
            report=self._agent('developer-repair',prompt,work,'developer.json',write=True)
            if report.get('status')!='DONE':raise PolicyFailure('Reparo não concluído')
            if worktree_changes(work)[1]:apply_commit_plan(work,tid,report)
            self._delegate_product_tests(task,work,base)
            audit,verified=self._verify(task,work,base)
            if audit['head']==head:raise PolicyFailure('Reparo não avançou HEAD')
            head=audit['head']
            git(work,'push','origin',branch,timeout=240)
            self.record(tid,'REPAIR_PUSH',stage='PR',head=head,local_gates=verified)
        self.scm.snapshot(pr,branch,head,base)
        ci_proof=self.scm.checks(pr,head,base,branch)
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
        if (current.get('review_head')!=head or current.get('strategic',{}).get('decision')!='PASS'
            or current.get('technical',{}).get('decision')!='ACCEPT' or current.get('ci_head')!=head):
            raise PolicyFailure('Provas de revisão e CI não correspondem ao HEAD final')
        self.scm.snapshot(pr,branch,head,base)
        self.record(tid,'MERGE_INTENT',stage='MERGE_INTENT',integrator=final)
        merged=self.scm.merge(pr,branch,head,base)
        self.record(tid,'MERGE_CONFIRMED',stage='MERGED',merge=merged)
        self.scm.issue_close(issue)
        self.record(tid,'DONE',stage='DONE',head=head)
        return {'task':tid,'state':'DONE','pr':pr,'head':head}


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
        for cycle in range(len(tasks)+1):
            eligible=[t for t in tasks if r.entry(t['id']).get('stage') not in ('DONE','BLOCKED')
                and all(r.entry(dep).get('stage')=='DONE' for dep in t['dependencies'])]
            if args.task_id:eligible=[t for t in eligible if t['id']==args.task_id]
            if not eligible:break
            selected=eligible[:args.max_parallel]
            with ThreadPoolExecutor(max_workers=len(selected)) as pool:
                futures={pool.submit(r.run_one,t):t for t in selected}
                for future in as_completed(futures):
                    task=futures[future]
                    try:print(json.dumps(future.result(),ensure_ascii=False))
                    except Exception as e:
                        ec=exit_code(e);code=max(code,ec)
                        if ec==2:r.record(task['id'],'BLOCKED',stage='BLOCKED',reason=str(e)[:500])
                        r.journal.event('ERROR',task['id'],{'code':ec,'kind':type(e).__name__,'message':str(e)[:350]})
                        print(f'[RUNNER_ERROR:{ec}] {task["id"]}: {type(e).__name__}: {e}',file=sys.stderr)
            if args.once or args.task_id:break
            if code in (70,75,76):break
        return code

if __name__=='__main__':sys.exit(main())
