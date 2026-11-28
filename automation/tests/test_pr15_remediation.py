"""Regression checks for GoJob PR #15 targeted recovery."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock
from types import SimpleNamespace

from service_execution import report_completion
from install_supervisor import execution_proof
from upgrade_runner import authorize_protected_merge, resume_protected_merge


class ExecutionIdentity(unittest.TestCase):
    def test_supervisor_writes_actual_runner_sha(self):
        with tempfile.TemporaryDirectory() as td:
            src = Path(td)/'source'; src.mkdir()
            (src/'manifest.json').write_text('{}')
            with patch.dict(os.environ, {'INVOCATION_ID': 'a'*32}), \
                 patch('service_execution.verify_package',return_value={'commit':'b'*40}), \
                 patch('service_execution.boot_id',return_value='test-boot'):
                report_completion(td,'gojob-runner-supervisor',src,runner_sha='c'*40)
            observed=json.loads((Path(td)/'service-executions/gojob-runner-supervisor.json').read_text())
            self.assertEqual(observed['commit'],'b'*40)
            self.assertEqual(observed['runner_sha'],'c'*40)

    def test_missing_runner_sha_never_certifies(self):
        with tempfile.TemporaryDirectory() as td:
            src=Path(td)/'source';src.mkdir()
            (src/'manifest.json').write_text('{}')
            with patch.dict(os.environ, {'INVOCATION_ID':'a'*32}), \
                 patch('service_execution.verify_package',return_value={'commit':'b'*40}):
                with self.assertRaisesRegex(RuntimeError,'SHA'):
                    report_completion(td,'gojob-runner-supervisor',src)

    def test_right_package_wrong_runner_sha_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            store=Path(td)/'service-executions';store.mkdir()
            receipt={'commit':'a'*40,'runner_sha':'b'*40,'invocation_id':'c'*32}
            dest=store/'gojob-runner-supervisor.json';dest.write_text(json.dumps(receipt))
            with patch('install_supervisor.show_service',return_value={
                'Result':'success','ExecMainStartTimestampMonotonic':'500'}), \
                 patch('install_supervisor.matches_execution',return_value=True):
                self.assertIsNone(execution_proof('gojob-runner-supervisor',Path(td),'a'*40,100))
                receipt['runner_sha']='a'*40;dest.write_text(json.dumps(receipt))
                self.assertEqual(execution_proof('gojob-runner-supervisor',Path(td),'a'*40,100),'c'*32)


class LocalGateRecovery(unittest.TestCase):
    def test_committed_failing_gate_runs_recovery_instead_of_replaying_forever(self):
        import upgrade_runner as upgrade
        from gojob_runner.tools import ExternalFailure
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'gate-recovery.json'
            row={'base':'b'*40,'head':'a'*40,'round':2,'pr':15,
                 'strategic':{'decision':'REVISE','findings':['Broken local gate']},
                 'technical':{'decision':'REVISE','findings':['Broken local gate']}}
            scope={'allowed_prefixes':('automation/',),'allowed_files':set(),'max_commits':2}
            with patch('upgrade_runner.gates',side_effect=[ExternalFailure('test failed'),None,None]), \
                 patch('upgrade_runner.git',return_value='2'), \
                 patch('upgrade_runner.recover_implementer',return_value={'status':'DONE','commit_plan':[{'message':'fix','paths':['automation/test.py']}]}), \
                 patch('upgrade_runner.repair_intent',return_value={'source':'a'*40}), \
                 patch('upgrade_runner.resume_commits',return_value='c'*40):
                self.assertEqual(upgrade.repair_committed_gate_failure(Path(td),'a'*40,row,
                        MagicMock(),path,scope),'c'*40)
            state=json.loads(path.read_text())
            self.assertEqual(state['gate_repair_attempts'],1)
            self.assertIn('gate_repair_intent',state)


class ProtectedMergeIntent(unittest.TestCase):
    def test_authorization_is_persisted_before_any_remote_merge(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'release-worktrees';root.mkdir()
            head,base='a'*40,'b'*40
            journal=root/'4.1.0-review-cycle.json'
            journal.write_text(json.dumps({'version':'4.1.0', 'rounds':[{}],
                'merge_intent':{'head':head,'base':base,'pr':15,'ci':{'head':head}}}))
            ledger=MagicMock();ledger.home=Path(td)
            api=MagicMock()
            with patch('upgrade_runner.GitHub',return_value=api), \
                 patch('upgrade_runner.validate_reviews',return_value=SimpleNamespace(action='ACCEPT')):
                authorized=authorize_protected_merge(Path(td),ledger,head)
            self.assertEqual(authorized['head'],head)
            self.assertEqual(json.loads(journal.read_text())['merge_authorization']['pr'],15)
            api.require_merge_protection.assert_called_once_with({'head':head})
            api.merge.assert_not_called()

    def test_resume_refuses_unrecorded_authorization(self):
        with tempfile.TemporaryDirectory() as td:
            root=Path(td)/'release-worktrees';root.mkdir()
            head,base='a'*40,'b'*40
            journal=root/'4.1.0-review-cycle.json'
            journal.write_text(json.dumps({'version':'4.1.0',
                'merge_intent':{'head':head,'base':base,'pr':15,'ci':{}},'rounds':[]}))
            ledger=MagicMock();ledger.home=Path(td)
            with self.assertRaisesRegex(ValueError,'Autorização durável'):
                resume_protected_merge(Path(td),ledger,head)
