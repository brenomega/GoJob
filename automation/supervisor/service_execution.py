"""Local proof written by the generation that actually completed a systemd invocation."""
import os
import re
import time
from pathlib import Path

from release_control import atomic_json
from release_package import verify_package


def invocation_id(value):
    return value if isinstance(value, str) and re.fullmatch(r'[0-9a-f]{32}', value) and int(value, 16) else None


def monotonic_us():
    return time.monotonic_ns() // 1000


def boot_id():
    return Path('/proc/sys/kernel/random/boot_id').read_text().strip()


def matches_execution(record, status):
    observed = invocation_id(status.get('InvocationID'))
    saved = invocation_id(record.get('invocation_id'))
    if not saved or (observed and observed != saved):
        return False
    try:
        # An inactive oneshot may have cleared InvocationID. Its last main PID
        # and monotonic execution interval still identify the process on this boot.
        return (record['boot_id'] == boot_id() and record['pid'] == int(status['ExecMainPID'])
                and int(status['ExecMainStartTimestampMonotonic']) <= record['observed_at']
                <= int(status['ExecMainExitTimestampMonotonic']))
    except (KeyError, TypeError, ValueError):
        return False


def report_completion(home, role, source, *, runner_sha=None):
    identity = invocation_id(os.environ.get('INVOCATION_ID'))
    if not identity or not (source/'manifest.json').is_file():
        return
    commit = verify_package(source)['commit']
    receipt = {
        'commit': commit, 'invocation_id': identity, 'pid': os.getpid(),
        'boot_id': boot_id(), 'observed_at': monotonic_us(),
    }
    if role == 'gojob-runner-supervisor':
        # Package identity cannot attest the runner loaded from develop.
        if not isinstance(runner_sha, str) or not re.fullmatch(r'[0-9a-f]{40}', runner_sha):
            raise RuntimeError('Recibo sem SHA do runner realmente executado')
        receipt['runner_sha'] = runner_sha
    atomic_json(Path(home)/'service-executions'/f'{role}.json', receipt)
