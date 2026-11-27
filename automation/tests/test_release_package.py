"""Real Git and Python entry points, with only external host services replaced."""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from threading import Barrier
from pathlib import Path
from unittest.mock import patch

import install_supervisor
import recovery_dispatch
import release_agent_recovery
import release_commits
import release_package
import release_worker
import rescue_watchdog
import service_execution
import upgrade_runner
import watchdog_launcher
from gojob_runner.tools import ExternalFailure, VerifiedCIFailure, git
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
        (self.repo/'RUNNER_VERSION').write_text(release_worker.VERSION+'\n')
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

    def host(self, *args, statuses, previous=None):
        action, unit = args[:2] if len(args) > 1 else (args[0], '')
        if '--property=LoadState' in args:
            return 'loaded'
        if '--property=ActiveState' in args:
            return 'ActiveState=inactive\nJob=0'
        if '--property=Result' in args:
            return statuses[unit]
        if action == 'start':
            identity = f'{len(statuses)+1:032x}'
            start = service_execution.monotonic_us()
            source = previous or (self.home/'stable').resolve().parents[1]
            with patch.dict('os.environ', {'INVOCATION_ID': identity}):
                service_execution.report_completion(self.home, unit.removesuffix('.service'), source)
            ended = service_execution.monotonic_us()
            statuses[unit] = (f'Result=success\nInvocationID=\nExecMainPID={service_execution.os.getpid()}\n'
                              f'ExecMainStartTimestampMonotonic={start}\nExecMainExitTimestampMonotonic={ended}')
        return {'is-enabled': 'enabled', 'is-active': 'active'}.get(action, '')

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
        worker = release_package.supervisor_dir(package)/'release_worker.py'
        status = subprocess.check_output([sys.executable, '-I', '-B', str(worker), '--repo',
                                          str(checkout), '--home', str(self.home), '--status'], cwd=checkout, text=True)
        self.assertIn('PREFLIGHT', status)
        self.assertFalse((self.units/'gojob-runner-rescue.timer').is_file())
        self.assertFalse((self.home/'stable').exists())
        self.assertNotIn('APPROVED_CORRECTION', (release_package.supervisor_dir(package)/'stable_supervisor.py').read_text())

        calls, statuses = [], {}
        old = recovery_dispatch.promote_approved_supervisor(self.repo, self.home, self.old)
        overlapped = False

        def host(*args):
            nonlocal overlapped
            calls.append(args)
            self.assertNotEqual(ledger.status()['state'], 'INSTALLED')
            if ('--property=ActiveState' in args and 'gojob-runner-supervisor.service' in args and not overlapped):
                self.assertEqual((self.home/'stable').resolve(), release_package.supervisor_dir(old['package']))
                overlapped = True
                return 'ActiveState=activating\nJob=14'
            return self.host(*args, statuses=statuses)

        with (patch.object(upgrade_runner, 'upgrade', return_value={'status': 'UPGRADED', 'merge': merge}),
              patch.object(install_supervisor, 'systemd_codex_runtime', return_value='[Service]\n'),
              patch.object(install_supervisor, 'systemctl', side_effect=host),
              patch.object(install_supervisor.time, 'sleep')):
            result = release_worker.once(self.repo, ledger=ledger, units=self.units)
        self.assertEqual(result['merge'], merge)
        self.assertEqual(ledger.status()['state'], 'INSTALLED')
        self.assertTrue(overlapped)
        self.assertFalse(any(call[0] in ('stop','restart') for call in calls))
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
        dispatch=script.with_name('recovery_dispatch.py')
        dispatch.write_text(dispatch.read_text()+'\nfrom fallback_marker import MARKER\n')
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
        launcher = active/'watchdog_launcher.py'
        with (patch.object(watchdog_launcher, '__file__', str(launcher)),
              patch.object(sys, 'argv', [str(launcher), '--repo', str(self.repo), '--home', str(self.home)]),
              patch.object(watchdog_launcher.os, 'execv', side_effect=SystemExit) as launched):
            with self.assertRaises(SystemExit):
                watchdog_launcher.main()
        self.assertIn(known, launched.call_args.args[1][3])
        fallback = rescue_watchdog.fallback_repair(self.repo, self.home, 'incident', {}, active)
        self.assertEqual(fallback, {'pr': 31})
        with self.assertRaisesRegex(ValueError, 'Manifesto'):
            release_package.verify_package(active.resolve().parents[1])

    def test_host_failure_restores_previous_generation_without_installing(self):
        old = recovery_dispatch.promote_approved_supervisor(self.repo, self.home, self.old)
        newer = self.change_supervisor('\nCANDIDATE = True\n')
        ledger = ReleaseLedger(release_worker.VERSION, self.home)

        statuses = {}

        def failing_host(*args):
            # The launcher fell back and exited successfully, but completed
            # with the previous generation's actual receipt.
            previous = Path(old['package']) if 'gojob-runner-rescue.service' in args else None
            return self.host(*args, statuses=statuses, previous=previous)

        with (patch.object(upgrade_runner, 'upgrade', return_value={'status': 'UPGRADED', 'merge': newer}),
              patch('release_agent_recovery.diagnose_controller_failure', return_value=None),
              patch.object(install_supervisor, 'systemd_codex_runtime', return_value='[Service]\n'),
              patch.object(install_supervisor, 'systemctl', side_effect=failing_host)):
            result = release_worker.once(self.repo, ledger=ledger, units=self.units)
        self.assertEqual(result['status'], 'WAITING_EXTERNAL')
        self.assertIn('gojob-runner-rescue sem prova de nova execução', result['reason'])
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

    def test_release_preparation_reconciles_partial_copy_and_preserves_divergence(self):
        (self.repo/'CHANGELOG_RUNNER.md').write_text('Release candidata\n')
        copied_later = 'automation/supervisor/release_marker.py'
        (self.repo/copied_later).write_text('CANDIDATE_RELEASE = True\n')
        source = self.integrate('release package in preparation')
        package = release_package.build_package(self.repo, source, self.home/'release-packages')
        ledger = ReleaseLedger(release_worker.VERSION, self.home)
        work = self.home/'release-worktrees'/f'runner-{release_worker.VERSION}'
        journal = work.parent/f'{release_worker.VERSION}-review-cycle.json'
        copy = release_commits.copy_package_file

        def interrupted(*args):
            intent = json.loads(journal.read_text())['preparation']
            self.assertEqual((intent['source'], intent['manifest']['commit']), (self.old, source))
            copy(*args)
            raise KeyboardInterrupt

        with (patch.object(upgrade_runner, 'ROOT', package),
              patch.object(upgrade_runner, 'GitHub') as hosting,
              patch.object(upgrade_runner, 'gates', side_effect=InterruptedError('reached local gates')),
              patch.object(release_commits, 'copy_package_file', side_effect=interrupted) as copier):
            hosting.return_value.get_pr.return_value = None
            with self.assertRaises(KeyboardInterrupt):
                upgrade_runner.upgrade_once(self.repo, ledger)
            copied = work/copier.call_args.args[2]
            self.assertFalse((work/copied_later).exists())
            content = copied.read_bytes()
            copied.write_text('alteração divergente a preservar\n')
            with self.assertRaisesRegex(ValueError, 'Conteúdo local diverge'):
                upgrade_runner.upgrade_once(self.repo, ledger)
            self.assertIn('divergente', copied.read_text())
            copied.write_bytes(content)
            copier.side_effect = copy
            with self.assertRaisesRegex(InterruptedError, 'reached local gates'):
                upgrade_runner.upgrade_once(self.repo, ledger)
        self.assertEqual(git(work, 'status', '--porcelain'), '')
        self.assertEqual(git(work, 'rev-list', '--count', f'{self.old}..HEAD'), '1')
        self.assertEqual((work/'CHANGELOG_RUNNER.md').read_text(), 'Release candidata\n')
        self.assertEqual((work/copied_later).read_text(), 'CANDIDATE_RELEASE = True\n')

    def test_release_reviews_corrected_supervisor_merges_and_resumes_before_issue_close(self):
        branch = upgrade_runner.BRANCH
        work = self.home/'release-worktrees'/f'runner-{release_worker.VERSION}'
        work.parent.mkdir()
        git(self.repo, 'worktree', 'add', '-b', branch, str(work), self.old)
        path = work/'automation/supervisor/stable_supervisor.py'
        companion = work/'automation/supervisor/rescue_watchdog.py'
        path.write_text(path.read_text()+'\nINITIAL_RELEASE = True\n')
        git(work, 'add', str(path.relative_to(work)))
        git(work, 'commit', '-m', 'fix(runner): publique candidato de teste [#0.11.0]')
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
                if head != initial:
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
                companion.write_text(companion.read_text()+'\nWATCHDOG_CORRECTION = True\n')
                return {'status': 'DONE', 'commit_plan': [{
                    'message': 'fix(runner): valide executável da geração [#0.11.0]',
                    'paths': ['automation/supervisor/stable_supervisor.py']}, {
                    'message': 'fix(runner): confirme a execução do watchdog [#0.11.0]',
                    'paths': ['automation/supervisor/rescue_watchdog.py']}]}
            common = {'head_sha': head, 'base_sha': self.old, 'blocking_kind': 'FIXABLE',
                      'rationale': 'Revisão independente com Git local',
                      'findings': ['Falta correção validada'] if head == initial else []}
            if schema.stem == 'strategic':
                return dict(common, decision='REVISE' if head == initial else 'PASS',
                            alternative='geração completa', opportunity_cost='tempo', north_star_impact='implantar')
            return dict(common, decision='ACCEPT', tests_assessed=['oráculos da release'], real_evidence=['diff'])

        statuses = {}
        interrupted = False

        def interrupt_second_commit(repo, *args, **kwargs):
            nonlocal interrupted
            if (not interrupted and 'commit' in args and Path(repo)==work
                and git(work, 'diff', '--cached', '--name-only')=='automation/supervisor/rescue_watchdog.py'):
                interrupted = True
                self.assertNotEqual(git(work, 'rev-parse', 'HEAD'), initial)
                self.assertEqual(git(work, 'diff', '--cached', '--name-only'),
                                 'automation/supervisor/rescue_watchdog.py')
                raise KeyboardInterrupt
            return git(repo, *args, **kwargs)

        with (patch.object(upgrade_runner, 'GitHub', Hosting),
              patch.object(upgrade_runner, 'call_codex', side_effect=agent),
              patch.object(upgrade_runner, 'gates'),
              patch('gojob_runner.tools.git', side_effect=interrupt_second_commit),
              patch.object(install_supervisor, 'systemd_codex_runtime', return_value='[Service]\n'),
              patch.object(install_supervisor, 'systemctl', side_effect=lambda *args: self.host(*args, statuses=statuses))):
            with self.assertRaises(KeyboardInterrupt):
                release_worker.once(self.repo, ledger=ledger, units=self.units)
            self.assertTrue(interrupted)
            self.assertEqual(git(work, 'rev-list', '--count', f'{initial}..HEAD'), '1')
            with self.assertRaises(KeyboardInterrupt):
                release_worker.once(self.repo, ledger=ledger, units=self.units)
            self.assertFalse((self.home/'stable').exists())
            self.assertNotIn('merge_confirmed', json.loads(state_path.read_text()))
            resumed = release_worker.once(self.repo, ledger=ledger, units=self.units)
        self.assertEqual(resumed['status'], 'INSTALLED')
        self.assertEqual(ledger.status()['state'], 'INSTALLED')
        self.assertEqual([event[0] for event in Hosting.call_log], ['ci', 'ci', 'ci', 'merge', 'close'])
        self.assertEqual(agent_calls[2], ('release-implementer', initial))
        self.assertCountEqual([label for label, _ in agent_calls[:2]],
                              ['release-reviewer-strategic', 'release-reviewer-technical'])
        self.assertCountEqual([label for label, _ in agent_calls[3:]],
                              ['release-reviewer-strategic', 'release-reviewer-technical'])
        self.assertIn('APPROVED_CORRECTION', (self.home/'stable/stable_supervisor.py').read_text())
        self.assertIn('WATCHDOG_CORRECTION', (self.home/'stable/rescue_watchdog.py').read_text())
        self.assertEqual(git(work, 'rev-list', '--count', f'{initial}..HEAD'), '2')

    def test_release_persists_parallel_report_and_gates_before_launching_reviewers(self):
        state = {'rounds': []}
        journal = self.home/'review-cycle.json'
        ledger = ReleaseLedger(release_worker.VERSION, self.home)
        parallel = Barrier(2)
        calls = []
        interrupted = True

        class Hosting:
            blocked = True

            def snapshot(self, *args):
                pass

            def checks(self, *args):
                if self.blocked:
                    raise RuntimeError('real CI failure')

            def comment(self, pr, head, base, role, report):
                persisted = json.loads(journal.read_text())
                self_round = persisted['rounds'][0]
                assert (self_round['pr'], self_round['head'], self_round['base']) == (pr, head, base)
                assert {'strategic', 'technical'}.issubset(self_round)

        hosting = Hosting()
        def review(repo, schema, *args, **kwargs):
            role = schema.stem
            calls.append(role)
            if interrupted:
                parallel.wait(timeout=3)
                if role == 'technical':
                    raise RuntimeError('lost reviewer transport')
            common = {'head_sha': self.old, 'base_sha': self.old, 'blocking_kind': 'FIXABLE',
                      'rationale': 'Revisão independente da release', 'findings': []}
            if role == 'strategic':
                return dict(common, decision='PASS', alternative='teste', opportunity_cost='tempo',
                            north_star_impact='continuidade')
            return dict(common, decision='ACCEPT', real_evidence=['Git fixture'], tests_assessed=['runner suite'])

        with patch.object(upgrade_runner, 'call_codex', side_effect=review):
            with self.assertRaisesRegex(RuntimeError, 'CI failure'):
                upgrade_runner.review_release(self.repo, hosting, 15, self.old, state, journal, ledger)
            self.assertEqual(calls, [])
            hosting.blocked = False
            with self.assertRaisesRegex(RuntimeError, 'lost reviewer'):
                upgrade_runner.review_release(self.repo, hosting, 15, self.old, state, journal, ledger)
            state = json.loads(journal.read_text())
            self.assertEqual(state['rounds'], [])
            self.assertIn('strategic', state['pending'])
            self.assertNotIn('technical', state['pending'])
            interrupted = False
            self.assertEqual(
                upgrade_runner.review_release(self.repo, hosting, 15, self.old, state, journal, ledger),
                self.old)
        self.assertEqual(calls.count('strategic'), 1)
        self.assertEqual(calls.count('technical'), 2)

    def test_verified_red_ci_calls_bounded_repair_before_fresh_review(self):
        branch = upgrade_runner.BRANCH
        work = self.home/'ci-worktree'
        git(self.repo, 'worktree', 'add', '-b', branch, str(work), self.old)
        candidate = 'automation/supervisor/ci_example.py'
        (work/candidate).write_text('RUNNER_CHECK = False\n')
        git(work, 'add', candidate)
        git(work, 'commit', '-m', 'fix(runner): publique correção inicial [#0.11.0]')
        initial = git(work, 'rev-parse', 'HEAD')
        git(work, 'push', '-u', 'origin', branch)
        evidence = {'pr': 15, 'head': initial, 'base': self.old, 'run_id': 6, 'run_attempt': 2, 'job_id': 38}
        ledger = ReleaseLedger(release_worker.VERSION, self.home)
        journal = self.home/'ci-review-cycle.json'
        state = {'rounds': []}
        calls = []

        class Hosting:
            def snapshot(_, pr, selected, head, base):
                self.assertEqual((pr, selected, base), (15, branch, self.old))
                self.assertEqual(git(work, 'ls-remote', 'origin', 'refs/heads/'+branch).split()[0], head)

            def checks(own, pr, head, base, selected):
                own.snapshot(pr, selected, head, base)
                calls.append(('ci', head))
                if head == initial:
                    raise VerifiedCIFailure(evidence)
                self.assertEqual(git(work, 'show', f'{head}:{candidate}'), 'RUNNER_CHECK = True')
                return {'pr': pr, 'head': head, 'base': base}

            def comment(self, *args):
                pass

        def diagnose_or_repair(repo, schema, prompt, write, timeout, *, label, ledger):
            self.assertEqual(json.loads(journal.read_text())['active_correction']['ci_failure'], evidence)
            self.assertEqual(git(repo, 'rev-parse', 'HEAD'), initial)
            if not write:
                calls.append(('triage', initial))
                self.assertIn('job verify vermelho', prompt)
                return {'decision': 'REPAIR_RUNNER', 'cause': 'asserção do runner encontrou False',
                        'evidence': ['teste local reproduziu o resultado do job 38'], 'plan': 'corrigir a condição'}
            calls.append(('repair', initial))
            if calls.count(('repair', initial)) == 1:
                (repo/candidate).write_text('RUNNER_CHECK = 0\n')
            else:
                self.assertIn('RUNNER_CHECK = 0', (repo/candidate).read_text())
                self.assertIn('asserção local ainda falha', prompt)
                (repo/candidate).write_text('RUNNER_CHECK = True\n')
            return {'status': 'DONE', 'commit_plan': [
                {'message': 'fix(runner): corrija a condição do teste [#0.11.0]', 'paths': [candidate]}]}

        def review(repo, schema, *_args, **_kwargs):
            head = git(repo, 'rev-parse', 'HEAD')
            self.assertNotEqual(head, initial)
            self.assertIn(('ci', head), calls)
            calls.append((schema.stem, head))
            common = {'head_sha': head, 'base_sha': self.old, 'findings': [],
                      'rationale': 'Correção confirmada no diff do runner'}
            if schema.stem == 'strategic':
                return dict(common, decision='PASS', alternative='reparo', opportunity_cost='tempo', north_star_impact='continuidade')
            return dict(common, decision='ACCEPT', real_evidence=['diff'], tests_assessed=['CI'])

        def gates(repo):
            if (repo/candidate).read_text() != 'RUNNER_CHECK = True\n':
                raise ExternalFailure('asserção local ainda falha')

        with (patch.object(release_agent_recovery, 'call_codex', side_effect=diagnose_or_repair),
              patch.object(upgrade_runner, 'call_codex', side_effect=review),
              patch.object(upgrade_runner, 'gates', side_effect=gates)):
            with self.assertRaisesRegex(ExternalFailure, 'asserção local ainda falha'):
                upgrade_runner.review_release(work, Hosting(), 15, self.old, state, journal, ledger)
            self.assertEqual(git(work, 'rev-parse', 'HEAD'), initial)
            state = json.loads(journal.read_text())
            corrected = upgrade_runner.review_release(work, Hosting(), 15, self.old, state, journal, ledger)
        self.assertEqual(calls[:5], [('ci', initial), ('triage', initial), ('repair', initial),
                                    ('repair', initial), ('ci', corrected)])
        self.assertCountEqual(calls[5:], [('strategic', corrected), ('technical', corrected)])
        self.assertEqual(json.loads(journal.read_text())['corrections'][0]['ci_failure'], evidence)

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
