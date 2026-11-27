"""Authorization, reservations and privacy at the release handoff."""
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import release_agent_recovery as recovery
import release_worker
import stable_supervisor
import upgrade_runner
from gojob_runner.review_policy import disposition
from release_control import ReleaseLedger


class ReleaseAuthorizationTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.ledger = ReleaseLedger(release_worker.VERSION, self.root)

    def recover(self):
        return recovery.recover_implementer(self.root, 'base', 'head', {},
                                           {'summary': 'fixed code still requires correction'},
                                           schema=self.root/'schema.json', ledger=self.ledger,
                                           eap='0.10.0', round_number=2)

    def test_review_veto_wins_even_on_revise_or_inconsistent_approval(self):
        for decision in ('REVISE', 'PASS', 'STOP'):
            veto = {'decision': decision, 'blocking_kind': 'HUMAN_DECISION',
                    'findings': ['O operador deve autorizar integração'], 'rationale': 'Autorização pendente'}
            technical = {'decision': 'ACCEPT', 'blocking_kind': 'FIXABLE'}
            with self.subTest(decision=decision):
                result = disposition(veto, technical)
                self.assertEqual(result.action, 'NEEDS_USER')
                self.assertIn('autorizar integração', result.reason)
                self.assertEqual(stable_supervisor.repair_review_decision(veto, technical), 'NEEDS_USER')

    def test_only_concrete_runner_repair_opens_write_session(self):
        for decision in ('ENVIRONMENT', 'RETRY_TRANSIENT', 'NO_ACTION', 'REPAIR_RUNNER'):
            with self.subTest(decision=decision):
                report = {'decision': decision, 'cause': 'sandbox', 'plan': 'observe', 'evidence': []}
                with patch.object(recovery, 'call_codex', return_value=report) as codex:
                    with self.assertRaisesRegex(RuntimeError, 'authorize'):
                        self.recover()
                self.assertEqual([call.args[3] for call in codex.call_args_list], [False])

    def test_recovery_consumes_attempt_before_agent_and_across_restarts(self):
        report = {'decision': 'REPAIR_RUNNER', 'cause': 'import fails',
                  'plan': 'repair package imports', 'evidence': ['clean checkout raises ModuleNotFoundError']}
        for expected in (1, 2):
            with patch.object(recovery, 'call_codex', side_effect=[report, KeyboardInterrupt]):
                with self.assertRaises(KeyboardInterrupt):
                    self.recover()
            reserved = next((self.ledger.root/'recovery-attempts').glob('*.json'))
            self.assertEqual(json.loads(reserved.read_text())['attempts'], expected)
            self.ledger = ReleaseLedger(release_worker.VERSION, self.root)
        with patch.object(recovery, 'call_codex') as codex:
            with self.assertRaisesRegex(RuntimeError, 'attempt limit'):
                self.recover()
            codex.assert_not_called()
        self.assertEqual(self.ledger.status()['state'], 'NEEDS_USER')

    def test_worker_cannot_overwrite_veto_after_upgrade_raises(self):
        def vetos(*args, **kwargs):
            self.ledger.emit('NEEDS_USER', 'authorization_pending', cause='A titular deve autorizar o rollout')
            raise RuntimeError('transient timeout')

        with patch.object(upgrade_runner, 'upgrade', side_effect=vetos) as upgrade:
            self.assertEqual(release_worker.once(self.root, ledger=self.ledger)['status'], 'NEEDS_USER')
            self.assertEqual(release_worker.once(self.root, ledger=self.ledger)['status'], 'NEEDS_USER')
            upgrade.assert_called_once()
        self.assertIn('autorizar o rollout', self.ledger.status()['details']['cause'])

    def test_release_context_filters_reports_logs_and_agent_environment(self):
        with patch.dict(os.environ, {'GOJOB_RUNNER_TELEGRAM_BOT_TOKEN': 'synthetic-bot-value',
                                    'LD_PRELOAD': '/synthetic.so', 'OPENAI_API_KEY': 'synthetic-openai',
                                    'CODEX_HOME': '/synthetic-runtime'}, clear=True):
            self.assertEqual(recovery._env(), {'CODEX_HOME': '/synthetic-runtime'})

        private = ('synthetic-account@example.test', '123.234.345-00', 'Opaque Synthetic Candidate',
                   'opaque-synthetic-bearer', 'unlabeled-original-transcript')
        self.ledger.record_agent_io('developer', 1, private[-1], private[-1])
        compact, folder = self.ledger.dossier(
            self.root, 'RECOVERY', 'missing import',
            feedback={'reviews': {'technical': {'findings': [
                f'Import broken for {private[0]} CPF: {private[1]}',
                {'candidate_name': private[2], 'evidence': f'Authorization: Bearer {private[3]}'},
            ]}}},
            last_report={'status': 'BLOCKED', 'summary': 'correct the package', 'candidate_name': private[2]})
        persisted = (folder/'dossier.json').read_text()
        for value in private:
            self.assertNotIn(value, persisted)
            self.assertNotIn(value, json.dumps(compact))
        self.assertIn('Import broken', persisted)
        self.assertIn('correct the package', persisted)
