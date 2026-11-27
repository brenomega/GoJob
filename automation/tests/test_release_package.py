"""Real Git and Python entry points, with only external host services replaced."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import install_supervisor
import recovery_dispatch
import release_package
import release_worker
import rescue_watchdog
import upgrade_runner
from gojob_runner.tools import git
from release_control import ReleaseLedger


class ReleasePackageTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.repo, self.home, self.units = (root/name for name in ('repo', 'home', 'units'))
        self.repo.mkdir()
        self.home.mkdir()
        remote = root/'remote.git'
        git(self.repo, 'init', '--bare', str(remote))
        git(self.repo, 'init', '-b', 'develop')
        git(self.repo, 'config', 'user.name', 'Synthetic Runner')
        git(self.repo, 'config', 'user.email', 'synthetic@example.test')
        git(self.repo, 'remote', 'add', 'origin', str(remote))
        (self.repo/'README.md').write_text('synthetic repository\n')
        self.commit('initial base')
        self.source_count = 0
        source = Path(__file__).resolve().parents[1]
        for name in ('gojob_runner', 'supervisor'):
            shutil.copytree(source/name, self.repo/'automation'/name,
                            ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
        (self.repo/'RUNNER_VERSION').write_text('4.0.0\n')
        self.old = self.integrate('complete candidate')
        git(self.repo, 'push', '-u', 'origin', 'develop')

    def commit(self, description):
        git(self.repo, 'add', '.')
        git(self.repo, 'commit', '-m', description)
        return git(self.repo, 'rev-parse', 'HEAD')

    def integrate(self, description):
        self.source_count += 1
        branch = f'candidate-{self.source_count}'
        git(self.repo, 'switch', '-c', branch)
        self.commit(description)
        git(self.repo, 'switch', 'develop')
        git(self.repo, 'merge', '--no-ff', '-m', 'merge '+description, branch)
        return git(self.repo, 'rev-parse', 'HEAD')

    def change_supervisor(self, content):
        path = self.repo/'automation/supervisor/stable_supervisor.py'
        path.write_text(path.read_text()+content)
        sha = self.integrate('change supervisor entry point')
        git(self.repo, 'push', 'origin', 'develop')
        return sha

    def test_unit_escapes_arguments_and_keeps_working_directory_literal(self):
        repo = self.repo.parent/'repo with % signs'
        unit = install_supervisor.service(repo, '/tmp/a $tool.py')
        self.assertIn(f'WorkingDirectory={str(repo).replace("%", "%%")}/\n', unit)
        self.assertIn('"/tmp/a $$tool.py"', unit)

    def test_clean_checkout_builds_worker_and_activates_corrected_sha_only_after_host_checks(self):
        checkout = self.repo.parent/'checkout'
        git(self.repo, 'clone', '--branch', 'develop', str(self.repo), str(checkout))
        for entry in release_package.ENTRY_POINTS:
            subprocess.run([sys.executable, '-I', '-B', str(checkout/'automation/supervisor'/entry), '--help'],
                           cwd=checkout, check=True, capture_output=True)
        merge = self.change_supervisor('\nAPPROVED_CORRECTION = True\n')
        ledger = ReleaseLedger(release_worker.VERSION, self.home)
        with patch.object(install_supervisor, 'systemd_codex_runtime', return_value='[Service]\n'):
            boot = release_worker.install_release_worker(self.repo, self.old, enable=False,
                                                         home=self.home, units=self.units)
        package = Path(boot['package'])
        self.assertEqual(release_package.verify_package(package)['commit'], self.old)
        unit_text = (self.units/'gojob-runner-release.service').read_text()
        self.assertIn(str(package), unit_text)
        self.assertTrue((self.units/'gojob-runner-rescue.timer').is_file())
        self.assertFalse((self.home/'stable').exists())
        self.assertNotIn('APPROVED_CORRECTION', (release_package.supervisor_dir(package)/'stable_supervisor.py').read_text())

        calls = []
        def host(*args):
            calls.append(args)
            self.assertNotEqual(ledger.status()['state'], 'INSTALLED')
            if '--property=LoadState' in args:
                return 'loaded'
            return {'show': 'success', 'is-enabled': 'enabled', 'is-active': 'active'}.get(args[0], '')

        with (patch.object(upgrade_runner, 'upgrade', return_value={'status': 'UPGRADED', 'merge': merge}),
              patch.object(install_supervisor, 'systemd_codex_runtime', return_value='[Service]\n'),
              patch.object(install_supervisor, 'systemctl', side_effect=host)):
            result = release_worker.once(self.repo, ledger=ledger, units=self.units)
        self.assertEqual(result['merge'], merge)
        self.assertEqual(ledger.status()['state'], 'INSTALLED')
        self.assertIn('APPROVED_CORRECTION', (self.home/'stable/stable_supervisor.py').read_text())
        unit = (self.units/'gojob-runner-rescue.service').read_text()
        self.assertIn(merge, unit)
        self.assertIn('watchdog_launcher.py', unit)
        for kind in ('supervisor', 'rescue'):
            self.assertIn(('is-active', f'gojob-runner-{kind}.timer'), calls)
            self.assertIn(('start', f'gojob-runner-{kind}.service'), calls)

    def test_promotion_pins_all_bytes_and_uses_its_own_fallback_when_shared_import_breaks(self):
        script = self.repo/'automation/supervisor/stable_supervisor.py'
        script.write_text(script.read_text()+(
            '\nfrom fallback_marker import MARKER\n'
            'def repair(repo, home, inc, info):\n    return {"pr": MARKER}\n'))
        marker = script.with_name('fallback_marker.py')
        marker.write_text('MARKER = 31\n')
        known = self.integrate('previous complete generation')
        git(self.repo, 'push', 'origin', 'develop')
        recovery_dispatch.promote_approved_supervisor(self.repo, self.home, known)
        marker.write_text('MARKER = 32\n')
        approved = self.integrate('approved new generation')
        marker.write_text('MARKER = 999\n')
        self.commit('develop advanced during the promotion')
        git(self.repo, 'push', 'origin', 'develop')
        active = self.home/'stable'
        with patch.object(release_package, 'replace_link', side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                recovery_dispatch.promote_approved_supervisor(self.repo, self.home, approved)
        self.assertIn(known, str(active.resolve()))
        recovery_dispatch.promote_approved_supervisor(self.repo, self.home, approved)
        self.assertIn(approved, str(active.resolve()))
        self.assertEqual((active/'fallback_marker.py').read_text(), 'MARKER = 32\n')
        self.assertIn(known, str((self.home/'stable.previous').resolve()))
        (active/'fallback_marker.py').write_text('broken python here')
        fallback = rescue_watchdog.fallback_repair(self.repo, self.home, 'incident', {}, active)
        self.assertEqual(fallback, {'pr': 31})
        with self.assertRaisesRegex(ValueError, 'Manifesto'):
            release_package.verify_package(active.resolve().parents[1])

    def test_host_failure_restores_previous_generation_without_installing(self):
        old = recovery_dispatch.promote_approved_supervisor(self.repo, self.home, self.old)
        newer = self.change_supervisor('\nCANDIDATE = True\n')
        ledger = ReleaseLedger(release_worker.VERSION, self.home)

        def failing_host(*args):
            if args[0] == 'start' and args[1] == 'gojob-runner-supervisor.service':
                raise RuntimeError('synthetic host activation failure')
            if '--property=LoadState' in args:
                return 'loaded'
            return {'show': 'success', 'is-enabled': 'enabled', 'is-active': 'active'}.get(args[0], '')

        with (patch.object(upgrade_runner, 'upgrade', return_value={'status': 'UPGRADED', 'merge': newer}),
              patch('release_agent_recovery.diagnose_controller_failure', return_value=None),
              patch.object(install_supervisor, 'systemd_codex_runtime', return_value='[Service]\n'),
              patch.object(install_supervisor, 'systemctl', side_effect=failing_host)):
            self.assertEqual(release_worker.once(self.repo, ledger=ledger, units=self.units)['status'],
                             'WAITING_EXTERNAL')
        self.assertNotEqual(ledger.status()['state'], 'INSTALLED')
        self.assertEqual((self.home/'stable').resolve(), release_package.supervisor_dir(old['package']))
        self.assertFalse((self.units/'gojob-runner-supervisor.service').exists())

    def test_legacy_directory_moves_as_one_generation(self):
        active = self.home/'stable'
        active.mkdir()
        (active/'stable_supervisor.py').write_text('legacy = True\n')
        (active/'recovery_dispatch.py').write_text('legacy_dependency = True\n')
        result = recovery_dispatch.promote_approved_supervisor(self.repo, self.home, self.old)
        self.assertEqual(active.resolve(), release_package.supervisor_dir(result['package']))
        release_package.restore_previous(self.home)
        self.assertEqual((active/'stable_supervisor.py').read_text(), 'legacy = True\n')
        self.assertEqual((active/'recovery_dispatch.py').read_text(), 'legacy_dependency = True\n')

    def test_release_reviews_corrected_supervisor_merges_and_resumes_before_issue_close(self):
        branch = upgrade_runner.BRANCH
        work = self.home/'release-worktrees'/f'runner-{release_worker.VERSION}'
        work.parent.mkdir()
        git(self.repo, 'worktree', 'add', '-b', branch, str(work), self.old)
        path = work/'automation/supervisor/stable_supervisor.py'
        path.write_text(path.read_text()+'\nINITIAL_RELEASE = True\n')
        git(work, 'add', str(path.relative_to(work)))
        git(work, 'commit', '-m', 'fix(runner): publique candidato de teste [#0.10.0]')
        initial = git(work, 'rev-parse', 'HEAD')
        git(work, 'push', '-u', 'origin', branch)
        ledger = ReleaseLedger(release_worker.VERSION, self.home)
        state_path = work.parent/f'{release_worker.VERSION}-review-cycle.json'
        owner = self

        class Hosting:
            remote = {'state': 'OPEN'}
            call_log = []

            def __init__(self, repo):
                self.repo = Path(repo)

            def verify_identity(self):
                owner.assertEqual(git(self.repo, 'remote', 'get-url', 'origin'), str(owner.repo.parent/'remote.git'))

            def get_pr(self, selected):
                owner.assertEqual(selected, branch)
                return {'number': 13, 'state': self.remote['state']}

            def get_issue(self, title):
                return {'number': 12}

            def pr(self, pr):
                return self.remote

            def snapshot(self, pr, selected, head, base):
                owner.assertEqual((pr, selected, base), (13, branch, owner.old))
                owner.assertEqual(git(owner.repo, 'rev-parse', 'origin/'+branch), head)

            def comment(self, *args):
                pass

            def checks(self, pr, head, base, selected):
                self.snapshot(pr, selected, head, base)
                owner.assertNotEqual(head, initial)
                owner.assertIn('APPROVED_CORRECTION', git(owner.repo, 'show', f'{head}:automation/supervisor/stable_supervisor.py'))
                self.call_log.append(('ci', head))
                return {'pr': pr, 'head': head, 'base': base}

            def merge(self, pr, selected, head, base):
                owner.assertEqual(json.loads(state_path.read_text())['merge_intent']['head'], head)
                self.call_log.append(('merge', head))
                git(owner.repo, 'merge', '--no-ff', '-m', 'integrate reviewed release', head)
                git(owner.repo, 'push', 'origin', 'develop')
                self.remote.update(state='MERGED', headRefOid=head, headRefName=selected,
                                   baseRefName='develop', mergeCommit={'oid': git(owner.repo, 'rev-parse', 'HEAD')})
                raise KeyboardInterrupt

            def confirm_merge(self, remote, selected, head, base):
                from gojob_runner.merge_evidence import reconcile_merge

                return reconcile_merge(lambda *args: git(self.repo, *args), remote,
                                       head=head, base=base, branch=selected)

            def issue_close(self, issue):
                owner.assertEqual(json.loads(state_path.read_text())['merge_confirmed'],
                                  self.remote['mergeCommit']['oid'])
                self.call_log.append(('close', issue))

        agent_calls = []
        def agent(repo, schema, prompt, write, timeout, *, label, ledger):
            head = git(repo, 'rev-parse', 'HEAD')
            agent_calls.append((label, head))
            if write:
                self.assertEqual(head, initial)
                self.assertIn('Falta correção validada', prompt)
                path.write_text(path.read_text()+'\nAPPROVED_CORRECTION = True\n')
                return {'status': 'DONE', 'commit_plan': [{
                    'message': 'fix(runner): valide executável da geração [#0.10.0]',
                    'paths': ['automation/supervisor/stable_supervisor.py']}]}
            common = {'head_sha': head, 'base_sha': self.old, 'blocking_kind': 'FIXABLE',
                      'rationale': 'Revisão independente com Git local',
                      'findings': ['Falta correção validada'] if head == initial else []}
            if schema.stem == 'strategic':
                return dict(common, decision='REVISE' if head == initial else 'PASS',
                            alternative='geração completa', opportunity_cost='tempo', north_star_impact='implantar')
            return dict(common, decision='ACCEPT', tests_assessed=['oráculos da release'], real_evidence=['diff'])

        def host(*args):
            if '--property=LoadState' in args:
                return 'loaded'
            return {'show': 'success', 'is-enabled': 'enabled', 'is-active': 'active'}.get(args[0], '')

        with (patch.object(upgrade_runner, 'GitHub', Hosting),
              patch.object(upgrade_runner, 'call_codex', side_effect=agent),
              patch.object(upgrade_runner, 'gates'),
              patch.object(install_supervisor, 'systemd_codex_runtime', return_value='[Service]\n'),
              patch.object(install_supervisor, 'systemctl', side_effect=host)):
            with self.assertRaises(KeyboardInterrupt):
                release_worker.once(self.repo, ledger=ledger, units=self.units)
            self.assertFalse((self.home/'stable').exists())
            self.assertNotIn('merge_confirmed', json.loads(state_path.read_text()))
            resumed = release_worker.once(self.repo, ledger=ledger, units=self.units)
        self.assertEqual(resumed['status'], 'UPGRADED')
        self.assertEqual(ledger.status()['state'], 'INSTALLED')
        self.assertEqual([event[0] for event in Hosting.call_log], ['ci', 'merge', 'close'])
        self.assertEqual([label for label, _ in agent_calls],
                         ['release-reviewer-strategic', 'release-reviewer-technical',
                          'release-implementer', 'release-reviewer-strategic', 'release-reviewer-technical'])
        self.assertIn('APPROVED_CORRECTION', (self.home/'stable/stable_supervisor.py').read_text())

    def test_autorepair_reconciles_merge_before_touching_advanced_base_or_closing_issue(self):
        import stable_supervisor

        inc = 'fixture'
        branch = 'fix/0.9.0-runner-'+inc
        git(self.repo, 'switch', '-c', branch, self.old)
        path = self.repo/'automation/supervisor/stable_supervisor.py'
        path.write_text(path.read_text()+'\nREPAIRED = True\n')
        head = self.commit('reviewed repair commit')
        git(self.repo, 'switch', 'develop')
        git(self.repo, 'merge', '--no-ff', '-m', 'integrate repair', head)
        oid = git(self.repo, 'rev-parse', 'HEAD')
        (self.repo/'README.md').write_text('develop advances independently\n')
        self.commit('unrelated base advance')
        git(self.repo, 'push', 'origin', 'develop')
        intent = {'issue': 3, 'pr': 5, 'head': head, 'base': self.old}
        pair = {'head_sha': head, 'base_sha': self.old, 'blocking_kind': 'FIXABLE'}
        row = {'head': head, 'base': self.old,
               'strategic': dict(pair, decision='PASS'),
               'technical': dict(pair, decision='ACCEPT')}
        history = {'base': self.old, 'incident': inc, 'pr': 5, 'rounds': [row],
                   'merge_intent': {**intent, 'ci': intent}}
        journal = self.home/'repairs'/f'{inc}-review-history.json'
        stable_supervisor.atomic_json(journal, history)
        remote = {'state': 'MERGED', 'headRefOid': head, 'headRefName': branch,
                  'baseRefName': 'develop', 'mergeCommit': {'oid': oid}}
        closed = []
        def close(issue):
            self.assertEqual(json.loads(journal.read_text())['merge_confirmed'], oid)
            if not closed:
                closed.append(issue)
                raise KeyboardInterrupt
            closed.append(issue)
        with patch.object(stable_supervisor, 'GitHub') as scm:
            scm.return_value.pr.return_value = remote
            scm.return_value.issue_close.side_effect = close
            with self.assertRaises(KeyboardInterrupt):
                stable_supervisor.repair(self.repo, self.home, inc, {})
            result = stable_supervisor.repair(self.repo, self.home, inc, {})
            scm.return_value.merge.assert_not_called()
        self.assertEqual(result['merge'], oid)
        self.assertEqual(closed, [3, 3])
