import subprocess,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import stable_supervisor as s

class Recovery(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.home=Path(self.tmp.name)
        self.repo=self.home/'repo';self.repo.mkdir()
        subprocess.run(['git','init','-q'],cwd=self.repo,check=True)
    def tearDown(self):self.tmp.cleanup()
    def args(self):
        return ['--repo',str(self.repo),'--home',str(self.home/'home'),'--max-repairs','1']
    def test_policy_block_does_not_launch_runner_repair(self):
        failed=SimpleNamespace(returncode=2,stdout='',stderr='PolicyFailure: revoked approval')
        with (patch.object(s,'resume_runner',return_value=(failed,'sha')),
              patch.object(s,'repair') as repair,
              patch.object(s,'notify',return_value=False),
              patch.object(s,'supervisor_triage',return_value=({'decision':'POLICY_STOP','cause':'revoked','evidence':['veto'],'plan':'human'},{})),
              patch.object(s,'proactive_cycle',return_value={'status':'COOLDOWN'})):
            self.assertEqual(s.main(self.args()),2)
            repair.assert_not_called()
            self.assertTrue(any((self.home/'home'/'incidents').glob('*/incident.json')))
    def test_crash_routes_to_independent_repair_then_resumes(self):
        failed=SimpleNamespace(returncode=70,stdout='',stderr='KeyError: lost checkpoint')
        ok=SimpleNamespace(returncode=0,stdout='task DONE',stderr='')
        with (patch.object(s,'resume_runner',side_effect=[(failed,'sha'),(ok,'sha2')]),
              patch.object(s,'repair',return_value={'pr':5,'head':'abc'}) as repair,
              patch.object(s,'notify',return_value=False),
              patch.object(s,'supervisor_triage',return_value=({'decision':'REPAIR_RUNNER','cause':'crash','evidence':['trace'],'plan':'fix'},{})),
              patch.object(s,'proactive_cycle',return_value={'status':'COOLDOWN'})):
            self.assertEqual(s.main(self.args()),0)
            repair.assert_called_once()
            self.assertTrue(any((self.home/'home'/'incidents').glob('*/repair.json')))
    def test_transient_rate_limit_retries_without_code_edit(self):
        limited=SimpleNamespace(returncode=75,stdout='',stderr='429 rate limit')
        ok=SimpleNamespace(returncode=0,stdout='task DONE',stderr='')
        with (patch.object(s,'resume_runner',side_effect=[(limited,'sha'),(ok,'sha2')]),
              patch.object(s,'repair') as repair,
              patch.object(s.time,'sleep'),patch.object(s,'notify',return_value=False),
              patch.object(s,'proactive_cycle',return_value={'status':'COOLDOWN'})):
            self.assertEqual(s.main(self.args()),0)
            repair.assert_not_called()
    def test_failed_repair_does_not_mutate_original_dirty_files(self):
        (self.repo/'candidate.db').write_text('KEEP')
        failed=SimpleNamespace(returncode=70,stdout='',stderr='ValueError: unhandled')
        with (patch.object(s,'resume_runner',return_value=(failed,'sha')),
              patch.object(s,'repair',side_effect=RuntimeError('broken repair')),
              patch.object(s,'notify',return_value=False),
              patch.object(s,'supervisor_triage',return_value=({'decision':'REPAIR_RUNNER','cause':'crash','evidence':['trace'],'plan':'fix'},{}))):
            self.assertEqual(s.main(self.args()),70)
            self.assertEqual((self.repo/'candidate.db').read_text(),'KEEP')
            self.assertTrue(any((self.home/'home'/'incidents').glob('*/repair.failed')))

if __name__=='__main__':unittest.main()
