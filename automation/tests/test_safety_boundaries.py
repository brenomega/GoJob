"""Policy guards must be independent of the agent and guard product CI/governance."""
import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from gojob_runner.runner import Runner,validate_task,main
from gojob_runner.tools import GitHub,PolicyFailure

class SafetyBoundaries(unittest.TestCase):
    def test_product_task_cannot_modify_ci_or_product_charter(self):
        with tempfile.TemporaryDirectory() as tmp:
            runner=Runner(tmp,state_root=Path(tmp)/'state',codex=object(),scm=object())
            t={'id':'1.2.1','title':'Implemente filtro','goal':'Filtrar','type':'feat',
               'acceptance':['Rejeitar falso positivo'],'dependencies':[]}
            with patch('gojob_runner.runner.assert_clean'),\
                 patch('gojob_runner.runner.commit_audit',return_value={'head':'head','commits':['a']}),\
                 patch('gojob_runner.runner.git',return_value='.github/workflows/ci.yml'):
                with self.assertRaisesRegex(PolicyFailure,'governança/CI'):
                    runner._verify(t,tmp,'base')
    def test_task_types_are_allowlisted(self):
        bad={'id':'1.1.1','title':'Good','goal':'Good','type':'../worktree',
             'acceptance':['testable'],'dependencies':[]}
        with self.assertRaisesRegex(ValueError,'Tipo'):validate_task(bad)
    def test_github_identity_requires_original_user(self):
        with tempfile.TemporaryDirectory() as tmp:
            gh=GitHub(tmp)
            def mock_git(repo,*args,**kwargs):
                if args[:2]==('remote','get-url'):return 'https://github.com/brenomega/GoJob.git'
                return 'true'
            with patch('gojob_runner.tools.git',side_effect=mock_git),\
                 patch('gojob_runner.tools.assert_clean'),\
                 patch.object(gh,'gh',side_effect=['logged in','another-user']):
                with self.assertRaisesRegex(PolicyFailure,'Identidade GitHub inesperada'):gh.preflight()
    def test_unknown_task_is_not_reported_as_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            fake=Runner(tmp,state_root=Path(tmp)/'state',codex=object(),scm=object())
            with patch('gojob_runner.runner.Runner',return_value=fake),\
                 patch('gojob_runner.runner.read_tasks',return_value=[
                    {'id':'0.1.1','title':'A','goal':'B','acceptance':['C'],'dependencies':[]} ]):
                self.assertEqual(main(['--repo',tmp,'--task-id','9.9.9']),2)

if __name__=='__main__':unittest.main()

# The test is deliberately attached to the same class without requiring a mock of Git internals.
def _test_charter_immutable_even_for_runner_task(self):
    with tempfile.TemporaryDirectory() as tmp:
        runner=Runner(tmp,state_root=Path(tmp)/'state',codex=object(),scm=object())
        t={'id':'0.9.0','title':'Corrija runner','goal':'Repare','type':'runner_fix',
           'acceptance':['Conserve segurança'],'dependencies':[]}
        with patch('gojob_runner.runner.assert_clean'),\
             patch('gojob_runner.runner.commit_audit',return_value={'head':'head','commits':['a']}),\
             patch('gojob_runner.runner.git',return_value='docs/SECURITY_POLICY.md'):
            with self.assertRaisesRegex(PolicyFailure,'autorização humana'):
                runner._verify(t,tmp,'base')
SafetyBoundaries.test_charter_immutable_even_for_runner_task=_test_charter_immutable_even_for_runner_task
