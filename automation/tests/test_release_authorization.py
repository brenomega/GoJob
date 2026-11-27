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
from gojob_runner.tools import GitHub
from release_control import ReleaseLedger, atomic_json


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
            self.assertEqual(release_worker.once(self.root, ledger=self.ledger,
                                                resume_merge_protection='a'*40)['status'], 'NEEDS_USER')
            upgrade.assert_called_once()
        self.assertIn('autorizar o rollout', self.ledger.status()['details']['cause'])

    def test_explicit_merge_resume_requires_exact_head_and_current_server_protection(self):
        head, base, merge = 'a'*40, 'b'*40, 'c'*40
        proof = {'pr': 15, 'head': head, 'base': base, 'app_id': 15368}
        common = {'head_sha': head, 'base_sha': base, 'findings': [],
                  'rationale': 'Parecer independente vinculado ao snapshot'}
        rounds = [{'head': head, 'base': base, 'pr': 15,
                   'strategic': dict(common, decision='PASS', opportunity_cost='tempo',
                                     alternative='reparo', north_star_impact='continuidade'),
                   'technical': dict(common, decision='ACCEPT', real_evidence=['Git'], tests_assessed=['CI'])}]
        intent = {'head': head, 'base': base, 'pr': 15, 'issue': 14, 'ci': proof}
        path = self.root/'release-worktrees'/f'{release_worker.VERSION}-review-cycle.json'
        atomic_json(path, {'version': release_worker.VERSION, 'rounds': rounds, 'merge_intent': intent})
        self.ledger.emit('NEEDS_USER', 'merge_protection_required')
        hosting = GitHub(self.root)
        settings = {'enforce_admins': {'enabled': False},
                    'required_status_checks': {'strict': True, 'checks': [{'context': 'verify', 'app_id': 15368}]}}
        remote = {'state': 'OPEN'}

        def merge_on_host(*args):
            self.assertTrue(settings['enforce_admins']['enabled'])
            remote.update(state='MERGED', mergeCommit={'oid': merge})

        with (patch.object(upgrade_runner, 'GitHub', return_value=hosting),
              patch.object(hosting, 'verify_identity'), patch.object(hosting, 'snapshot'),
              patch.object(hosting, 'checks', return_value=proof) as ci,
              patch.object(hosting, '_api', return_value=settings),
              patch.object(hosting, 'pr', return_value=remote),
              patch.object(hosting, 'gh', side_effect=merge_on_host) as write,
              patch.object(hosting, 'issue_close'), patch.object(hosting, 'confirm_merge', return_value=merge),
              patch.object(upgrade_runner, 'git', return_value=release_worker.VERSION),
              patch('quickstart.enable_automation', return_value={'host_verified': True, 'commit': merge,
                    'executions': {'gojob-runner-rescue': '1'*32, 'gojob-runner-supervisor': '2'*32}})):
            self.assertEqual(release_worker.once(self.root, ledger=self.ledger)['status'], 'NEEDS_USER')
            self.assertEqual(release_worker.once(self.root, ledger=self.ledger,
                                                resume_merge_protection=base)['status'], 'NEEDS_USER')
            ci.assert_not_called()
            self.assertEqual(release_worker.once(self.root, ledger=self.ledger,
                                                resume_merge_protection=head)['status'], 'NEEDS_USER')
            write.assert_not_called()
            self.assertEqual(self.ledger.status()['event'], 'merge_protection_required')
            settings['enforce_admins']['enabled'] = True
            self.assertEqual(release_worker.once(self.root, ledger=self.ledger,
                                                resume_merge_protection=head)['status'], 'INSTALLED')
        write.assert_called_once_with('pr', 'merge', '15', '--merge', '--match-head-commit', head)
        self.assertEqual(ci.call_count, 2)
        current = json.loads(path.read_text())
        self.assertEqual(current['rounds'], rounds)
        self.assertEqual(current['merge_intent'], intent)

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
