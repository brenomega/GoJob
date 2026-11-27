import subprocess,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import stable_supervisor as s
import recovery_dispatch as dispatch

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
              patch.object(s,'repair',return_value={'pr':5,'head':'abc','merge':'known'}) as repair,
              patch.object(dispatch,'promote_approved_supervisor',return_value={'status':'DEPLOYED'}),
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


    def test_queue_respects_zero_and_one_repair_in_entire_cycle(self):
        ok=SimpleNamespace(returncode=0,stdout='',stderr='')
        diagnosis={'decision':'REPAIR_RUNNER','cause':'defect','plan':'fix','evidence':['trace']}
        unresolved={'1.1':{'stage':'BLOCKED','reason':'first'},
                    '1.2':{'stage':'BLOCKED','reason':'second'}}
        for limit in (0,1):
            with self.subTest(limit=limit):
                args=self.args()
                args[-1]=str(limit)
                with (patch.object(s,'resume_runner',return_value=(ok,'sha')),
                      patch.object(s,'inspect_unresolved',return_value=unresolved),
                      patch.object(s,'supervisor_triage',return_value=(diagnosis,{})),
                      patch.object(s,'repair',side_effect=RuntimeError('test interruption')) as repair,
                      patch.object(s,'maybe_evolve',return_value={'status':'NO_METRICS'}),
                      patch.object(s,'proactive_cycle',return_value={'status':'COOLDOWN'}) as proactive,
                      patch.object(s,'notify',return_value=False)):
                    self.assertEqual(s.main(args),0)
                    self.assertEqual(repair.call_count,limit)
                    self.assertFalse(proactive.call_args.kwargs['allow_repair'])

if __name__=='__main__':unittest.main()
