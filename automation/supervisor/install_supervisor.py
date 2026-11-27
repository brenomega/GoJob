#!/usr/bin/env python3
"""Host-only installation and verification of runner user-systemd units."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

if __name__ == '__main__':
    sys.path[:0] = map(str, Path(__file__).resolve().parents[:2])

from gojob_runner.merge_evidence import OID
from gojob_runner.tools import TransientFailure
from recovery_context import private_write
from release_control import atomic_json
from release_package import restore_previous, supervisor_dir, verify_package
from service_execution import boot_id, matches_execution, monotonic_us


def systemd_value(value):
    if any(c in value for c in ('\n', '\r', '\x00')):
        raise ValueError('Valor inválido para systemd')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def systemd_codex_runtime():
    launcher = shutil.which(os.environ.get('GOJOB_CODEX_BIN') or 'codex')
    if not launcher:
        raise RuntimeError('Codex não localizado no PATH do host')
    values = {'PATH': os.environ['PATH'], 'GOJOB_CODEX_BIN': launcher}
    for key in ('CODEX_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME'):
        if key in os.environ:
            values[key] = os.environ[key]
    return '[Service]\n' + ''.join('Environment='+systemd_value(f'{key}={value}')+'\n'
                                    for key, value in values.items())


def service(repo, script, *args, on_failure=None, timeout='8h', monitor=False):
    argv = [sys.executable, '-I', '-B', str(script), '--repo', str(repo), *map(str, args)]
    exec_start = ' '.join(systemd_value(item).replace('$', '$$') for item in argv)
    failure = f'OnFailure={on_failure}\n' if on_failure else ''
    monitoring = 'Environment=GOJOB_MONITOR_CODEX=1\n' if monitor else ''
    # WorkingDirectory is parsed as a single literal path by systemd.
    directory = str(repo).replace('%', '%%').rstrip('/') + '/'
    return ('[Unit]\n' + failure + '[Service]\n' + monitoring + 'Type=oneshot\nExecStart='+exec_start
            + '\nWorkingDirectory='+directory+'\nTimeoutStartSec='+timeout+'\n')


def timer(name, interval):
    return ('[Timer]\nOnBootSec=3min\nOnCalendar='+interval+'\nPersistent=true\n'
            f'Unit={name}.service\n[Install]\nWantedBy=timers.target\n')


def unit_pair(units, name, service_text, interval):
    private_write(units/f'{name}.service', service_text)
    private_write(units/f'{name}.timer', timer(name, interval))
    private_write(units/f'{name}.service.d/90-gojob-runtime.conf', systemd_codex_runtime())


def systemctl(*args):
    return subprocess.run(['systemctl', '--user', *args], capture_output=True,
                          text=True, check=True, timeout=8*3600).stdout.strip()


def verify_units(*names):
    for name in names:
        if systemctl('show', f'{name}.service', '--property=LoadState', '--value') != 'loaded':
            raise RuntimeError(f'Serviço {name} não carregado no host')
        if systemctl('is-enabled', f'{name}.timer') != 'enabled':
            raise RuntimeError(f'Timer {name} não habilitado no host')
        if systemctl('is-active', f'{name}.timer') != 'active':
            raise RuntimeError(f'Timer {name} não ativo no host')


def show_service(name, *properties):
    result = systemctl('show', f'{name}.service', *[f'--property={prop}' for prop in properties])
    return dict(line.split('=', 1) for line in result.splitlines() if '=' in line)


def wait_for_idle(*names, timeout=15):
    deadline = time.monotonic() + timeout
    while True:
        states = [show_service(name, 'ActiveState', 'Job') for name in names]
        if all(state.get('ActiveState') in ('inactive', 'failed') and state.get('Job', '0') in ('', '0')
               for state in states):
            return
        if time.monotonic() >= deadline:
            raise TransientFailure('timeout aguardando a execução anterior terminar em {}'.format(', '.join(names)))
        time.sleep(1)


def execution_proof(name, home, merge, switched_at):
    receipt = home/'service-executions'/f'{name}.json'
    if not receipt.is_file():
        return None
    status = show_service(name, 'Result', 'InvocationID', 'ExecMainPID',
                          'ExecMainStartTimestampMonotonic', 'ExecMainExitTimestampMonotonic')
    observed = json.loads(receipt.read_text())
    if (status.get('Result') == 'success' and observed.get('commit') == merge
        and matches_execution(observed, status)
        and int(status['ExecMainStartTimestampMonotonic']) >= switched_at):
        return observed['invocation_id']
    return None


def verify_execution(name, home, merge, switched_at):
    wait_for_idle(name)
    execution = execution_proof(name, home, merge, switched_at)
    if execution is None:
        systemctl('start', f'{name}.service')
        execution = execution_proof(name, home, merge, switched_at)
    if execution is None:
        raise RuntimeError(f'Serviço {name} sem prova de nova execução do SHA {merge}')
    return execution


def install_watchdog(repo, package, home, units):
    entry = 'watchdog_launcher.py'
    unit_pair(units, 'gojob-runner-rescue',
              service(repo, supervisor_dir(package)/entry, '--home', home, timeout='2h'),
              '*:0/10')


def activate(repo, merge, *, home=None, units=None, enable=True, release_timer=True):
    from recovery_dispatch import promote_approved_supervisor, repair_lock

    if not OID.fullmatch(merge or ''):
        raise ValueError('Ativação exige o SHA completo do merge')
    home = Path(home or Path.home()/'.local/share/gojob-runner').resolve()
    units = Path(units or Path.home()/'.config/systemd/user').resolve()
    repo = Path(repo).resolve()
    watchdog = 'gojob-runner-rescue'
    supervisor = 'gojob-runner-supervisor'
    if enable:
        wait_for_idle(supervisor, watchdog)
    with repair_lock(home, repo) as acquired:
        if not acquired:
            raise RuntimeError('Reparo ou promoção em andamento')
        paths = [units/f'{name}.{kind}' for name in (supervisor, watchdog) for kind in ('service', 'timer')]
        paths += [units/f'{name}.service.d/90-gojob-runtime.conf' for name in (supervisor, watchdog)]
        checkpoint = home/'host-activation'/f'{merge}.json'
        if checkpoint.is_file():
            saved = json.loads(checkpoint.read_text())
            if set(saved['units']) != {str(path) for path in paths}:
                raise ValueError('Diretório systemd diverge da instalação pendente')
        else:
            saved = {'active': str((home/'stable').resolve()) if (home/'stable').is_dir() else None,
                     'units': {str(path): path.read_text() if path.is_file() else None for path in paths}}
            atomic_json(checkpoint, saved)
        backup = {Path(path): content for path, content in saved['units'].items()}
        def check_idle():
            if enable:
                wait_for_idle(supervisor, watchdog, timeout=0)

        result = promote_approved_supervisor(repo, home, expected_merge=merge, before_activate=check_idle)
        boot = boot_id()
        if result['status']=='DEPLOYED' or saved.get('boot_id') != boot or 'switched_at' not in saved:
            saved.update(boot_id=boot, switched_at=monotonic_us())
            atomic_json(checkpoint, saved)
        executions = {}
        try:
            install_watchdog(repo, Path(result['package']), home, units)
            unit_pair(units, supervisor,
                      service(repo, home/'stable/stable_supervisor.py', '--home', home,
                              '--once', '--max-repairs', 1, on_failure=f'{watchdog}.service'),
                      '*:0/30')
            if enable:
                systemctl('daemon-reload')
                if release_timer:
                    verify_units('gojob-runner-release')
                systemctl('enable', '--now', f'{watchdog}.timer', f'{supervisor}.timer')
                verify_units(watchdog, supervisor)
                for name in (watchdog, supervisor):
                    executions[name] = verify_execution(name, home, merge, saved['switched_at'])
                if (home/'stable').resolve() != supervisor_dir(result['package']):
                    raise RuntimeError('Geração ativa diverge do SHA integrado')
                verify_package(result['package'])
        except TransientFailure:
            # A timer or OnFailure job can race with the installation. Preserve
            # the checkpoint and let that invocation finish before trying again.
            raise
        except Exception:
            active = home/'stable'
            if saved['active'] != str(active.resolve()):
                if saved['active'] is None:
                    active.unlink(missing_ok=True)
                else:
                    restore_previous(home)
            for path, content in backup.items():
                if content is None:
                    path.unlink(missing_ok=True)
                else:
                    private_write(path, content)
            if enable:
                for name in (watchdog, supervisor):
                    if backup[units/f'{name}.timer'] is None:
                        systemctl('disable', '--now', f'{name}.timer')
                systemctl('daemon-reload')
            checkpoint.unlink(missing_ok=True)
            raise
        if enable:
            checkpoint.unlink(missing_ok=True)
    return {**result, 'host_verified': enable, 'executions': executions}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--merge', required=True)
    parser.add_argument('--enable', action='store_true')
    args = parser.parse_args()
    from release_worker import VERSION

    home = Path.home()/'.local/share/gojob-runner'
    journal = home/'release-worktrees'/f'{VERSION}-review-cycle.json'
    if not journal.is_file() or json.loads(journal.read_text()).get('merge_confirmed') != args.merge:
        parser.error('merge precisa estar confirmado no journal da release')
    print(json.dumps(activate(args.repo, args.merge, home=home, enable=args.enable, release_timer=False)))


if __name__ == '__main__':
    main()
