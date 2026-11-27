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
        with patch.object(gh,'snapshot',return_value={}),patch.object(gh,'gh',return_value='{"check_runs":[]}'),\
             patch('gojob_runner.tools.time.monotonic',side_effect=[0.0,0.2,2.0]),\
             patch('gojob_runner.tools.time.sleep'):
            with self.assertRaises(TransientFailure):
                gh.checks(18,'head','base','feat/1.1-test',timeout=1)
    def test_merge_requires_exact_previously_approved_head_at_github(self):
        gh=GitHub('/tmp')
        with patch.object(gh,'snapshot',return_value={}),\
             patch.object(gh,'checks',return_value=[{'name':'verify'}]),\
             patch.object(gh,'gh',return_value='') as cli,\
             patch.object(gh,'pr',return_value={'state':'MERGED','mergeCommit':{'oid':'merged'}}):
            gh.merge(7,'docs/0.1.1-prova','head123','base456')
        cli.assert_called_with('pr','merge','7','--merge','--match-head-commit','head123')

    def test_task_graph_rejects_cycle(self):
        import json
        with tempfile.TemporaryDirectory() as tmp:
            tasks=[{'id':'1.1','title':'a','goal':'x','acceptance':['x'],'dependencies':['1.2']},
                   {'id':'1.2','title':'b','goal':'y','acceptance':['y'],'dependencies':['1.1']}]
            f=Path(tmp)/'tasks.json';f.write_text(json.dumps({'tasks':tasks}))
            with self.assertRaisesRegex(ValueError,'cíclica'):read_tasks(f)

if __name__=='__main__':unittest.main()
