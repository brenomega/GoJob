"""Runner-only tests: recovery correctness, no product tests authored here."""
from __future__ import annotations
import tempfile,unittest,subprocess,shutil
from pathlib import Path
from unittest.mock import patch
from gojob_runner.runner import Runner
from gojob_runner.tools import git,PolicyFailure,TransientFailure


class SpecialistCodex:
    def __init__(self,triage='REPAIR',outcome='DONE'):
        self.triage=triage;self.outcome=outcome;self.calls=[]
    def run(self,role,prompt,repo,schema,write=False,timeout=2400):
        self.calls.append((role,write,prompt))
        if role=='blocker-diagnostician':
            return {'decision':self.triage,'cause':'Corrigir evidência insuficiente',
                'evidence':['Evidência original ausente'],
                'plan':'Um segundo agente deve registrar limites sem inventar prova',
                'missing_prerequisites':[]}
        if role=='blocker-repair-specialist':
            if self.outcome=='BLOCKED':
                return {'status':'BLOCKED','summary':'Não há fonte original disponível',
                        'changed':[],'tests':[],'limitations':['Fonte externa ausente'],'commit_plan':[]}
            file=Path(repo)/'docs'/'AUDIT_RESCUE.md'
            file.write_text('Limites e evidências realmente observadas, sem fingir acesso a ambiente externo.\n')
            return {'status':'DONE','summary':'Produzi análise auditável, sujeita a dois reviewers',
                    'changed':['docs/AUDIT_RESCUE.md'],'tests':[],
                    'limitations':['Validação live pendente'],
                    'commit_plan':[{'message':'docs(auditoria): delimite evidências não verificadas [#0.1.2]',
                                    'paths':['docs/AUDIT_RESCUE.md']}]}
        raise AssertionError('Unexpected agent '+role)


class FakeSCM:
    def __init__(self):self.called=[]
    def pr(self,number):
        self.called.append(('pr',number))
        return {'state':'OPEN'}
    def snapshot(self,*args):
        self.called.append(('snapshot',args))


class BlockedRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.remote=self.root/'remote.git';self.repo=self.root/'repo'
        self.repo.mkdir()
        subprocess.run(['git','init','--bare','-q',str(self.remote)],check=True)
        git(self.repo,'init','-q');git(self.repo,'config','user.name','Test Agent')
        git(self.repo,'config','user.email','agent@example.test')
        git(self.repo,'remote','add','origin',str(self.remote))
        source=Path(__file__).resolve().parents[1]
        if (source/'product_seed').is_dir():
            shutil.copytree(source/'product_seed',self.repo,dirs_exist_ok=True)
        else:
            # In the published scaffold this test lives in automation/tests;
            # constitution/quality gates are at the PRODUCT repo root.
            source=Path(__file__).resolve().parents[2]
            for dirname in ('docs','config'):
                shutil.copytree(source/dirname,self.repo/dirname)
            for filename in ('AGENTS.md','RUNNER_VERSION'):
                shutil.copy2(source/filename,self.repo/filename)
            (self.repo/'tests').mkdir(exist_ok=True)
            shutil.copy2(source/'tests/test_bootstrap.py',self.repo/'tests/test_bootstrap.py')
        git(self.repo,'add','.');git(self.repo,'commit','-m','genesis')
        git(self.repo,'branch','-M','develop');git(self.repo,'push','-u','origin','develop')
        self.base=git(self.repo,'rev-parse','HEAD')
        self.task={'id':'0.1.2','title':'Compare PPO Brave e executores existentes',
                   'type':'docs','goal':'Produzir pesquisa comprovada',
                   'acceptance':['Evidências verificáveis'],'dependencies':[]}
        self.codex=SpecialistCodex();self.runner=Runner(self.repo,self.root/'state',
                                                    codex=self.codex,scm=FakeSCM())
        self.branch=self.runner.branch(self.task)
        self.work=self.runner.worktree(self.task,self.branch)
        self.runner.record('0.1.2','INIT',stage='BLOCKED',base=self.base,
                           branch=self.branch,reason='Developer relatou bloqueio real',
                           report={'status':'BLOCKED','limitations':['Fonte privada não encontrada']})
    def tearDown(self):self.tmp.cleanup()

    def test_interrupted_test_diff_returns_only_to_test_engineer(self):
        path='tests/test_synthetic_contract.py'
        (self.work/'synthetic_product.py').write_text('FIXTURE_VALUE = 1\n')
        git(self.work,'add','synthetic_product.py')
        git(self.work,'commit','-m','feat(fixture): simule código da tarefa [#0.1.2]')
        head=git(self.work,'rev-parse','HEAD')
        (self.work/path).write_text('# teste iniciado pelo Test Engineer\n')
        self.runner.record('0.1.2','TEST_ENGINEER_START',stage='TEST_IN_PROGRESS',issue=4,test_source_head=head)
        calls=[]

        def test_agent(role, prompt, *_args, **kwargs):
            calls.append(role)
            self.assertEqual(role,'independent-test-engineer')
            self.assertTrue(kwargs['write'])
            self.assertEqual(self.runner.entry('0.1.2')['test_source_head'],head)
            self.assertIn('teste iniciado',(self.work/path).read_text())
            (self.work/path).write_text('# teste concluído pelo Test Engineer\n')
            return {'status':'DONE','commit_plan':[
                {'message':'test(fixture): conclua cenário sintético [#0.1.2]','paths':[path]}]}

        with patch.object(self.runner,'_agent',side_effect=test_agent), \
             patch.object(self.runner,'_verify',side_effect=InterruptedError('gates depois dos testes')):
            (self.work/'synthetic_product.py').write_text('FIXTURE_VALUE = 2\n')
            with self.assertRaisesRegex(PolicyFailure,'Diff de testes interrompido'):
                self.runner.run_one(self.task)
            self.assertEqual((self.work/'synthetic_product.py').read_text(),'FIXTURE_VALUE = 2\n')
            self.assertEqual(self.runner.entry('0.1.2')['stage'],'TEST_IN_PROGRESS')
            (self.work/'synthetic_product.py').write_text('FIXTURE_VALUE = 1\n')
            self.assertEqual(calls,[])
            with self.assertRaisesRegex(InterruptedError,'gates depois dos testes'):
                self.runner.run_one(self.task)
        self.assertEqual(calls,['independent-test-engineer'])
        completed=git(self.work,'rev-parse','HEAD')
        self.assertEqual(git(self.work,'rev-parse','HEAD^'),head)
        self.assertEqual(git(self.work,'diff','--name-only',head,completed),path)
        self.assertEqual(self.runner.entry('0.1.2')['stage'],'TESTED')
    def test_developer_blocked_delegates_to_different_session_with_context(self):
        report=self.runner._blocked_resolution(self.task)
        self.assertEqual(report['state'],'RECOVERED')
        entry=self.runner.entry('0.1.2')
        self.assertEqual(entry['stage'],'RECOVERED')
        self.assertEqual(entry['blocker_recovery_attempts'],1)
        self.assertNotEqual(report['head'],self.base)
        self.assertEqual([x[0] for x in self.codex.calls],
                         ['blocker-diagnostician','blocker-repair-specialist'])
        self.assertFalse(self.codex.calls[0][1])
        self.assertTrue(self.codex.calls[1][1])
        self.assertIn('Fonte privada não encontrada',self.codex.calls[0][2])
        self.assertEqual(git(self.work,'status','--porcelain'),'')
    def test_reviewer_revise_is_repaired_but_not_approved_or_merged(self):
        (self.work/'docs/AUDITORIA.md').write_text('Primeira pesquisa incompleta\n')
        git(self.work,'add','.');git(self.work,'commit','-m','docs(auditoria): registre estudo inicial [#0.1.2]')
        old=git(self.work,'rev-parse','HEAD')
        snapshot={'head_sha':old,'base_sha':self.base,'blocking_kind':'FIXABLE',
                  'rationale':'Revisão independente verificou lacunas concretas de cobertura'}
        self.runner.record('0.1.2','REVIEW_BLOCK',stage='BLOCKED',pr=5,
                           review_head=old,strategic={**snapshot,'decision':'REVISE','findings':['Falta prova de estágio de verão'],
                                      'opportunity_cost':'Tempo','alternative':'Inspeção','north_star_impact':'Cobertura'},
                           technical={**snapshot,'decision':'REVISE','findings':['Oráculo insuficiente'],
                                      'tests_assessed':['suite'],'real_evidence':['diff']},
                           findings={'strategic':['Falta prova de estágio de verão'],
                                     'technical':['Oráculo insuficiente']},
                           reason='Reparo não concluído')
        result=self.runner._blocked_resolution(self.task)
        self.assertEqual(result['state'],'RECOVERED')
        state=self.runner.entry('0.1.2')
        self.assertEqual(state['pr'],5)
        self.assertEqual(state['head'],result['head'])
        self.assertIsNone(state['review_head'])
        self.assertIsNone(state['ci_head'])
        self.assertNotEqual(result['head'],old)
        self.assertIn('Falta prova',self.codex.calls[1][2])
    def test_review_resume_persists_each_role_before_comments_and_integrates_at_limit(self):
        (self.work/'docs'/'candidate.md').write_text('Candidato ao último parecer.\n')
        git(self.work,'add','docs/candidate.md')
        git(self.work,'commit','-m','docs(evidencia): registre evidência concreta [#0.1.2]')
        head=git(self.work,'rev-parse','HEAD')
        self.runner.record('0.1.2','READY',stage='PR',issue=4,pr=5,head=head,review_rounds_total=2)
        calls=[]
        self_outer=self
        base=self.base

        class Hosting(FakeSCM):
            def checks(self,pr,commit,target,branch):
                calls.append('ci')
                return {'pr':pr,'head':commit,'base':target}

            def comment(self,*args):
                state=self_outer.runner.entry('0.1.2')
                assert state['review_rounds_total']==3
                assert state['review_history'][-1]['technical']['decision']=='ACCEPT'
                if calls.count('comment')==0:
                    calls.append('comment')
                    raise TransientFailure('comment transport interrupted')

            def merge(self,*args):
                calls.append('merge')
                return {'state':'MERGED'}

            def issue_close(self,issue):
                calls.append(('close',issue))

        def agent(role,*args,**kwargs):
            calls.append(role)
            snapshot={'head_sha':head,'base_sha':base,'blocking_kind':'FIXABLE',
                      'rationale':'Revisor auditou o diff concreto da tarefa', 'findings':[]}
            if role=='strategic-reviewer':
                return dict(snapshot,decision='PASS',alternative='leitura',
                            opportunity_cost='tempo',north_star_impact='cobertura')
            if role=='technical-reviewer':
                if calls.count(role)==1:
                    raise TransientFailure('reviewer interrupted')
                return dict(snapshot,decision='ACCEPT',real_evidence=['diff'],tests_assessed=['suite'])
            if role=='integrator':
                return dict(snapshot,decision='MERGE')
            raise AssertionError(role)

        self.runner.scm=Hosting()
        with (patch.object(self.runner,'_agent',side_effect=agent),
              patch.object(self.runner,'_delegate_product_tests'),
              patch.object(self.runner,'_verify',return_value=({'head':head},{}))):
            with self.assertRaisesRegex(TransientFailure,'reviewer interrupted'):
                self.runner.run_one(self.task)
            self.runner=Runner(self.repo,self.root/'state',codex=self.codex,scm=self.runner.scm)
            with (patch.object(self.runner,'_agent',side_effect=agent),
                  patch.object(self.runner,'_delegate_product_tests'),
                  patch.object(self.runner,'_verify',return_value=({'head':head},{}))):
                with self.assertRaisesRegex(TransientFailure,'comment transport'):
                    self.runner.run_one(self.task)
                self.assertEqual(self.runner.run_one(self.task)['state'],'DONE')
        self.assertEqual(calls[:3],['ci','strategic-reviewer','technical-reviewer'])
        self.assertEqual(calls.count('strategic-reviewer'),1)
        self.assertEqual(calls.count('technical-reviewer'),2)
        self.assertEqual(calls[-3:],['integrator','merge',('close',4)])

    def test_merged_task_with_mismatched_ci_intent_does_not_close_issue(self):
        head=self.base
        snapshot={'head_sha':head,'base_sha':self.base,'blocking_kind':'FIXABLE',
                  'rationale':'Revisão independente verificou o snapshot', 'findings':[]}
        pair={'pr':5,'head':head,'base':self.base,'round':3,
              'strategic':dict(snapshot,decision='PASS',opportunity_cost='tempo',
                               alternative='leitura',north_star_impact='cobertura'),
              'technical':dict(snapshot,decision='ACCEPT',real_evidence=['diff'],tests_assessed=['suite'])}
        intent={'pr':5,'head':head,'base':self.base,'ci':{'pr':5,'head':head,'base':'unrelated'}}
        self.runner.record('0.1.2','INTENT',stage='MERGE_INTENT',issue=4,pr=5,head=head,
                           review_history=[pair],merge_intent=intent,ci_head=head,review_head=head)
        with patch.object(self.runner,'scm') as scm:
            scm.pr.return_value={'state':'MERGED'}
            with self.assertRaisesRegex(PolicyFailure,'intenção e provas'):
                self.runner.run_one(self.task)
            scm.confirm_merge.assert_not_called()
            scm.issue_close.assert_not_called()

    def test_blocker_plan_cannot_claim_independent_test_changes(self):
        test=self.work/'tests'/'test_agent_owned.py'
        test.write_text('raise AssertionError("product test fixture")\n')
        head=git(self.work,'rev-parse','HEAD')
        report={'status':'DONE','commit_plan':[{
            'message':'fix(auditoria): preserve limites do reparo [#0.1.2]',
            'paths':['tests/test_agent_owned.py']}]}
        with self.assertRaisesRegex(PolicyFailure,'fora do papel Developer'):
            self.runner._apply_developer_plan(self.work,'0.1.2',report)
        self.assertTrue(test.is_file())
        self.assertEqual(git(self.work,'rev-parse','HEAD'),head)
        self.assertEqual(git(self.work,'diff','--cached','--name-only'),'')

    def test_strategic_stop_without_actionable_findings_requires_diagnosis(self):
        self.runner.record('0.1.2','STOP',stage='BLOCKED',pr=5,
                           review_head=self.base,strategic={'decision':'STOP'},
                           technical={'decision':'ACCEPT'})
        self.assertEqual(self.runner._blocked_resolution(self.task)['state'],'NEEDS_USER')
        self.assertFalse(self.codex.calls)
        self.assertEqual(self.runner.entry('0.1.2')['stage'],'NEEDS_USER')
    def test_policy_refusal_without_developer_report_does_not_retry(self):
        self.runner.record('0.1.2','POLICY',stage='BLOCKED',report=None,
                           reason='PolicyFailure: revoked approval')
        result=self.runner._blocked_resolution(self.task)
        self.assertEqual(result['state'],'NEEDS_USER')
        self.assertFalse(self.codex.calls)
    def test_integrator_block_after_passes_cannot_trigger_code_edit(self):
        self.runner.record('0.1.2','INTEGRATOR',stage='BLOCKED',pr=8,
                           review_head=self.base,strategic={'decision':'PASS'},
                           technical={'decision':'ACCEPT'},reason='Integrator recusou merge')
        result=self.runner._blocked_resolution(self.task)
        self.assertEqual(result['state'],'NEEDS_USER')
        self.assertFalse(self.codex.calls)
    def test_environment_unavailable_is_never_reinterpreted_as_success(self):
        self.codex.triage='NEEDS_USER'
        report=self.runner._blocked_resolution(self.task)
        self.assertEqual(report['state'],'NEEDS_USER')
        self.assertEqual(self.runner.entry('0.1.2')['stage'],'NEEDS_USER')
        self.assertEqual([x[0] for x in self.codex.calls],['blocker-diagnostician'])
        self.assertEqual(git(self.work,'rev-parse','HEAD'),self.base)
    def test_unfinished_repair_escalates_without_replaying(self):
        self.codex.outcome='BLOCKED'
        self.assertEqual(self.runner._blocked_resolution(self.task)['state'],'NEEDS_USER')
        self.assertEqual(self.runner.entry('0.1.2')['stage'],'NEEDS_USER')
        self.assertEqual(git(self.work,'status','--porcelain'),'')
    def test_recovery_budget_does_not_repeat_llm(self):
        self.runner.record('0.1.2','BUDGET',stage='BLOCKED',blocker_recovery_attempts=2)
        self.assertEqual(self.runner._blocked_resolution(self.task)['state'],'NEEDS_USER')
        self.assertFalse(self.codex.calls)

if __name__=='__main__':unittest.main()
