"""Runner-only tests: recovery correctness, no product tests authored here."""
from __future__ import annotations
import tempfile,unittest,subprocess,shutil
from pathlib import Path
from gojob_runner.runner import Runner
from gojob_runner.tools import git,PolicyFailure


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
        self.runner.record('0.1.2','REVIEW_BLOCK',stage='BLOCKED',pr=5,
                           review_head=old,strategic={'decision':'REVISE','findings':['Falta prova de estágio de verão']},
                           technical={'decision':'REVISE','findings':['Oráculo insuficiente']},
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
    def test_strategic_stop_never_auto_resumed(self):
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
