"""Independent-review and remote-head regression tests."""
import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from gojob_runner.runner import Runner,read_tasks
from gojob_runner.tools import GitHub,PolicyFailure,TransientFailure

class FakeCodex:
    pass

class Reviews(unittest.TestCase):
    def test_strategic_reviewer_rejects_stale_sha(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Runner(tmp,state_root=Path(tmp)/'state',codex=FakeCodex(),scm=object())
            evidence={'head_sha':'old','base_sha':'base','rationale':'Uma solução fora do objetivo',
                      'findings':[],'opportunity_cost':'custo','alternative':'alternativa','north_star_impact':'regressão'}
            with self.assertRaisesRegex(PolicyFailure,'SHA'):r._validate_review(evidence,'new','base','strategic')
    def test_strategic_reviewer_requires_objective_tradeoff(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Runner(tmp,state_root=Path(tmp)/'state',codex=FakeCodex(),scm=object())
            evidence={'head_sha':'head','base_sha':'base','rationale':'Parece bom','findings':[],
                      'opportunity_cost':'','alternative':'','north_star_impact':''}
            with self.assertRaisesRegex(PolicyFailure,'superficial'):r._validate_review(evidence,'head','base','strategic')
    def test_technical_reviewer_requires_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            r=Runner(tmp,state_root=Path(tmp)/'state',codex=FakeCodex(),scm=object())
            with self.assertRaisesRegex(PolicyFailure,'evidências'):
                r._validate_review({'head_sha':'head','base_sha':'base','rationale':'Revisado','findings':[]},'head','base','technical')
    def test_snapshot_rejects_base_advance(self):
        gh=GitHub('/tmp')
        sample={'number':18,'headRefName':'feat/1.2.1-radar','baseRefName':'develop','state':'OPEN','isDraft':False,
                'baseRefOid':'new-base','headRefOid':'head'}
        with patch.object(gh,'pr',return_value=sample),patch('gojob_runner.tools.git',return_value='head\trefs/heads/feat/1.2.1-radar'):
            with self.assertRaisesRegex(PolicyFailure,'Base develop avançou'):
                gh.snapshot(18,'feat/1.2.1-radar','head','old-base',attempts=1,sleep=0)
    def test_snapshot_rejects_stale_head_without_rewriting_history(self):
        gh=GitHub('/tmp')
        sample={'number':18,'headRefName':'feat/1.2.1-radar','baseRefName':'develop','state':'OPEN','isDraft':False,
                'baseRefOid':'base','headRefOid':'old-head'}
        with patch.object(gh,'pr',return_value=sample),patch('gojob_runner.tools.git',return_value='head\trefs/heads/feat/1.2.1-radar'):
            with self.assertRaises(TransientFailure):gh.snapshot(18,'feat/1.2.1-radar','head','base',attempts=1,sleep=0)
    def test_snapshot_accepts_exact_remote_identity(self):
        gh=GitHub('/tmp')
        sample={'number':18,'headRefName':'feat/1.2.1-radar','baseRefName':'develop','state':'OPEN','isDraft':False,
                'baseRefOid':'base','headRefOid':'head'}
        with patch.object(gh,'pr',return_value=sample),patch('gojob_runner.tools.git',return_value='head\trefs/heads/feat/1.2.1-radar'):
            self.assertEqual(gh.snapshot(18,'feat/1.2.1-radar','head','base',attempts=1)['headRefOid'],'head')
    def test_ci_never_accepts_zero_checks(self):
        gh=GitHub('/tmp')
        with patch.object(gh,'snapshot',return_value={}),patch('gojob_runner.tools.git',return_value='trusted-workflow'),\
             patch.object(gh,'gh',return_value='{"workflow_runs":[]}'),\
             patch('gojob_runner.tools.time.monotonic',side_effect=[0.0,0.2,2.0]),\
             patch('gojob_runner.tools.time.sleep'):
            with self.assertRaises(TransientFailure):
                gh.checks(18,'head','base','feat/1.1-test',timeout=1)
    def test_merge_requires_exact_previously_approved_head_at_github(self):
        gh=GitHub('/tmp')
        with patch.object(gh,'snapshot',return_value={}),\
             patch.object(gh,'checks',return_value={'app_id':1}),\
             patch.object(gh,'_api',return_value={'enforce_admins':{'enabled':True},
                 'required_status_checks':{'strict':True,'checks':[{'context':'verify','app_id':1}]}}),\
             patch.object(gh,'confirm_merge'),\
             patch.object(gh,'gh',return_value='') as cli,\
             patch.object(gh,'pr',return_value={'state':'MERGED','mergeCommit':{'oid':'merged'}}):
            gh.merge(7,'docs/0.1.1-prova','head123','base456')
        cli.assert_called_with('pr','merge','7','--merge','--match-head-commit','head123')

    def test_ci_requires_workflow_attempt_job_and_exact_pr_head_and_base(self):
        from copy import deepcopy
        from gojob_runner.merge_evidence import VerifiedCIFailure, verify_ci
        run={'id':19,'run_attempt':2,'check_suite_id':123,'event':'pull_request','head_sha':'head','name':'gojob-verify',
             'path':'.github/workflows/ci.yml@refs/pull/18/merge','status':'completed','conclusion':'success',
             'pull_requests':[{'number':18,'base':{'ref':'develop','sha':'base'},'head':{'sha':'head'}}]}
        job={'id':31,'run_id':19,'name':'verify','status':'completed','conclusion':'success',
             'check_run_url':'https://api.github.com/repos/owner/repo/check-runs/31'}
        check={'id':31,'name':'verify','head_sha':'head','status':'completed','conclusion':'success',
               'check_suite':{'id':123},'app':{'id':15368,'slug':'github-actions'}}
        events={'actions/workflows/ci.yml/runs?event=pull_request&head_sha=head&per_page=100':{'workflow_runs':[run]},
                'actions/runs/19/attempts/2/jobs':{'jobs':[job]},'check-runs/31':check}
        proof=verify_ci(events.__getitem__,18,'head','base')
        self.assertEqual((proof['base'],proof['run_attempt'],proof['job_id']),('base',2,31))
        self.assertIsNone(verify_ci(events.__getitem__,19,'head','base'))
        self.assertIsNone(verify_ci(events.__getitem__,18,'head','concurrent-base'))
        snapshot=deepcopy(events)
        failed=deepcopy(events)
        failed_run=next(iter(failed.values()))['workflow_runs'][0]
        failed_job=failed['actions/runs/19/attempts/2/jobs']['jobs'][0]
        failed_check=failed['check-runs/31']
        for entry in (failed_run,failed_job,failed_check):
            entry['conclusion']='failure'
        failed_job['steps']=[{'name':'Baseline and runner tests','conclusion':'failure'}]
        with self.assertRaises(VerifiedCIFailure) as result:
            verify_ci(failed.__getitem__,18,'head','base')
        self.assertEqual(result.exception.evidence,{**proof,'failed_steps':['Baseline and runner tests']})
        failed_check['app']['slug']='untrusted-check'
        with self.assertRaises(ValueError) as result:
            verify_ci(failed.__getitem__,18,'head','base')
        self.assertNotIsInstance(result.exception,VerifiedCIFailure)
        run['path']='.github/workflows/unrelated.yml'
        self.assertIsNone(verify_ci(events.__getitem__,18,'head','base'))
        job=snapshot['actions/runs/19/attempts/2/jobs']['jobs'][0]
        job['name']='arbitrary green status'
        with self.assertRaisesRegex(ValueError,'job verify'):
            verify_ci(snapshot.__getitem__,18,'head','base')
        job['name']='verify'
        snapshot['check-runs/31']['app']['slug']='another-app'
        with self.assertRaisesRegex(ValueError,'origem'):
            verify_ci(snapshot.__getitem__,18,'head','base')

    def test_merge_without_strict_server_protection_never_calls_github_merge(self):
        from gojob_runner.tools import MergeProtectionRequired
        gh=GitHub('/tmp')
        settings={'enforce_admins':{'enabled':False},
                  'required_status_checks':{'strict':True,'checks':[{'context':'verify','app_id':1}]}}
        with (patch.object(gh,'checks',return_value={'app_id':1}),
              patch.object(gh,'_api',return_value=settings),
              patch.object(gh,'gh') as cli):
            with self.assertRaises(MergeProtectionRequired):
                gh.merge(7,'docs/0.1.1-prova','head','base')
            cli.assert_not_called()


    def test_task_graph_rejects_cycle(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            tasks=[{'id':'1.1','title':'a','goal':'x','acceptance':['x'],'dependencies':['1.2']},
                   {'id':'1.2','title':'b','goal':'y','acceptance':['y'],'dependencies':['1.1']}]
            f=Path(tmp)/'tasks.json';f.write_text(json.dumps({'tasks':tasks}))
            with self.assertRaisesRegex(ValueError,'cíclica'):read_tasks(f)

if __name__=='__main__':unittest.main()
