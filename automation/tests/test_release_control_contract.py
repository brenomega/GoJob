"""Portable causal tests of the separate release control plane.

This suite is intentionally copied into automation/tests and executed in CI.
"""
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'supervisor'))
from release_control import ReleaseLedger, atomic_json, environment_class, host_only_limitation

class ReleaseControlContract(unittest.TestCase):
    def test_sandbox_dbus_refusal_is_not_a_live_host_failure(self):
        self.assertTrue(host_only_limitation('systemctl --user: Operation not permitted'))
        self.assertEqual(environment_class({'status':'BLOCKED',
            'summary':'The service could not be inspected: user bus Operation not permitted'}),
            'AGENT_SANDBOX_HOST_VERIFICATION')
        self.assertEqual(environment_class({'status':'BLOCKED',
            'summary':'Cannot satisfy explicit user consent'}),'IMPLEMENTATION_BLOCKED')

    def test_private_ledger_and_atomic_latest_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            ledger=ReleaseLedger('9.9.9',tmp)
            ledger.emit('REVIEWING','review_started',head='testsha')
            ledger.emit('REPAIRING','review_rejected',head='testsha',reason='fixable')
            data=ledger.status()
            self.assertEqual(data['state'],'REPAIRING')
            self.assertEqual(data['event'],'review_rejected')
            lines=[json.loads(x) for x in ledger.events.read_text().splitlines()]
            self.assertEqual([x['state'] for x in lines],['REVIEWING','REPAIRING'])
            self.assertEqual(ledger.events.stat().st_mode & 0o077,0)
            self.assertEqual(ledger.status_path.stat().st_mode & 0o077,0)

    def test_release_lock_has_single_writer_and_recovers_after_crash(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=ReleaseLedger('9.9.9',tmp)
            b=ReleaseLedger('9.9.9',tmp)
            with a.exclusive() as first:
                self.assertTrue(first)
                with b.exclusive() as second:self.assertFalse(second)
            with b.exclusive() as third:self.assertTrue(third)

    def test_failed_agent_never_becomes_installed_by_incident_capture(self):
        with tempfile.TemporaryDirectory() as tmp:
            repo=Path(tmp)/'project';repo.mkdir()
            subprocess.run(['git','init','-q'],cwd=repo,check=True)
            ledger=ReleaseLedger('9.9.9',Path(tmp)/'state')
            _,folder=ledger.dossier(repo,'RECOVERY','incomplete developer',
                 feedback={'technical':{'findings':['failed review']}},
                 last_report={'status':'BLOCKED','summary':'not done'})
            self.assertEqual(ledger.status()['state'],'REPAIRING')
            context=json.loads((folder/'dossier.json').read_text())
            self.assertIn('failed review',json.dumps(context['feedback']))
            self.assertEqual((folder/'dossier.json').stat().st_mode & 0o077,0)

if __name__=='__main__':unittest.main()
