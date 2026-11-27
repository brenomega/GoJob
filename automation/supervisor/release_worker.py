#!/usr/bin/env python3
"""Independently scheduled, crash-resumable release controller for the host."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

if __name__ == '__main__':
    sys.path[:0] = map(str, Path(__file__).resolve().parents[:2])

from gojob_runner.merge_evidence import OID
from gojob_runner.tools import MergeProtectionRequired
from install_supervisor import install_watchdog, service, systemctl, unit_pair
from release_control import ReleaseLedger, atomic_json, scrub
from release_package import build_package, supervisor_dir

ROOT = Path(__file__).resolve().parents[2]
VERSION = (ROOT/'RUNNER_VERSION').read_text().strip()


def install_release_worker(repo, source_sha, *, enable=True, home=None, units=None):
    repo = Path(repo).expanduser().resolve()
    home = Path(home or Path.home()/'.local/share/gojob-runner').resolve()
    units = Path(units or Path.home()/'.config/systemd/user')
    package = build_package(repo, source_sha, home/'release-packages')
    script = supervisor_dir(package)/'release_worker.py'
    unit_pair(units, 'gojob-runner-release',
              service(repo, script, '--home', home, '--units', units, timeout='7h'), '*:0/15')
    # For an existing deployment keep its watchdog until the approved handoff.
    if not (units/'gojob-runner-rescue.service').exists():
        install_watchdog(repo, package, home, units)
    if enable:
        systemctl('daemon-reload')
        systemctl('enable', '--now', 'gojob-runner-release.timer')
    return {'package': str(package), 'commit': source_sha, 'enabled': enable}


def once(repo, *, ledger=None, units=None):
    from quickstart import enable_automation
    from upgrade_runner import upgrade

    ledger = ledger or ReleaseLedger(VERSION)
    with ledger.exclusive() as acquired:
        if not acquired:
            return {'status': 'ALREADY_RUNNING'}
        previous = ledger.status()
        if previous.get('state') == 'NEEDS_USER':
            return {'status': 'NEEDS_USER'}
        if previous.get('state') == 'INSTALLED' and OID.fullmatch(previous.get('details', {}).get('merge', '')):
            return {'status': 'INSTALLED'}
        retry_file = ledger.root/'retry_schedule.json'
        try:
            retry = json.loads(retry_file.read_text())
        except (OSError, ValueError):
            retry = {}
        if retry.get('next_due_at', 0) > time.time():
            return {'status': 'COOLDOWN', 'next_due_at': retry['next_due_at']}
        try:
            result = upgrade(repo, ledger=ledger)
            if result.get('status') == 'UPGRADED':
                activated = enable_automation(Path(repo), merge=result['merge'], home=ledger.home, units=units)
                if not activated.get('host_verified') or activated.get('commit') != result['merge']:
                    raise RuntimeError('Ativação sem validação efetiva do host e SHA integrado')
                ledger.emit('INSTALLED', 'release_cycle_finished', merge=result['merge'])
                retry_file.unlink(missing_ok=True)
            return result
        except MergeProtectionRequired as exc:
            ledger.emit('NEEDS_USER','merge_protection_required',cause=exc)
            return {'status': 'NEEDS_USER', 'reason': str(exc)}
        except Exception as exc:
            current = ledger.status()
            if current.get('state') == 'NEEDS_USER':
                return {'status': 'NEEDS_USER', 'reason': current.get('details', {})}
            message = scrub(exc)[-1300:]
            signature = hashlib.sha256(message.encode()).hexdigest()
            attempts = retry.get('attempts', 0)+1 if retry.get('signature') == signature else 1
            transient = any(word in message.lower() for word in (
                'timeout', 'rate limit', '429', '503', 'temporarily', 'network', 'connection reset', 'quota'))
            state = 'NEEDS_USER' if attempts >= 3 and not transient else 'WAITING_EXTERNAL'
            delay = min(900*2**min(attempts-1, 5), 21600)
            atomic_json(retry_file, {'signature': signature, 'attempts': attempts,
                                     'next_due_at': time.time()+delay, 'last_error': message})
            if not transient and state!='NEEDS_USER' and attempts==1 and current.get('event') not in (
                'no_authorized_code_repair', 'agent_transport_failed', 'triage_pending'
            ):
                from release_agent_recovery import diagnose_controller_failure

                worktree=ledger.home/'release-worktrees'/f'runner-{VERSION}'
                report=diagnose_controller_failure(repo, exc, ledger=ledger,
                                                   worktree=worktree if worktree.is_dir() else repo)
                if report and report.get('decision') in ('NEEDS_USER','POLICY_STOP'):
                    ledger.emit('NEEDS_USER','controller_authorization_pending',cause=report.get('cause',''))
                    return {'status':'NEEDS_USER','reason':scrub(report.get('cause',''))}
            ledger.emit(state, 'release_cycle_deferred', cause=message, attempts=attempts)
            return {'status': state, 'reason': message, 'attempts': attempts}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--home', type=Path)
    parser.add_argument('--units', type=Path)
    options = parser.parse_args(argv)
    result = once(options.repo, ledger=ReleaseLedger(VERSION, home=options.home), units=options.units)
    print('[GOJOB RELEASE RECOVERY] '+json.dumps(result, ensure_ascii=False), flush=True)


if __name__ == '__main__':
    main()
