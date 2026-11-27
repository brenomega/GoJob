"""Product evolution is independent from the runner's successful task completion."""
import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import stable_supervisor as s

class EvolutionTrigger(unittest.TestCase):
    def test_no_metrics_does_not_use_codex(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp)/'repo';repo.mkdir()
            with patch.object(s,'installed_runner') as chk:
                self.assertEqual(s.maybe_evolve(repo,Path(tmp)/'home')['status'],'NO_METRICS')
                chk.assert_not_called()
    def test_evolution_failure_does_not_invalidate_completed_task(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp)/'repo';repo.mkdir()
            result=SimpleNamespace(returncode=0,stdout='task DONE',stderr='')
            with patch.object(s,'resume_runner',return_value=(result,'sha')),\
                 patch.object(s,'maybe_evolve',side_effect=RuntimeError('Codex quota')),\
                 patch.object(s,'process_incident',return_value={'status':'ENVIRONMENT'}) as incident,\
                 patch.object(s,'proactive_cycle',return_value={'status':'COOLDOWN'}),\
                 patch.object(s,'repair') as repair:
                self.assertEqual(s.main(['--repo',str(repo),'--home',str(Path(tmp)/'home')]),0)
                repair.assert_not_called()
                incident.assert_called_once()

if __name__=='__main__':unittest.main()
