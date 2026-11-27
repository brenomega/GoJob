#!/usr/bin/env python3
"""Host-only installation and verification of runner user-systemd units."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

if __name__ == '__main__':
    sys.path[:0] = map(str, Path(__file__).resolve().parents[:2])

from gojob_runner.merge_evidence import OID
from recovery_context import private_write
from release_control import atomic_json
from release_package import restore_previous, supervisor_dir, verify_package


def systemd_value(value):
    if any(c in value for c in ('\n', '\r', '\x00')):
        raise ValueError('Valor inválido para systemd')
    return '"' + value.replace('\\', '\\\\').replace('"', '\\"').replace('%', '%%') + '"'


def systemd_codex_runtime():
    launcher = shutil.which('codex')
    if not launcher:
        raise RuntimeError('Codex não localizado no PATH do host')
    values = {'PATH': os.environ['PATH'], 'GOJOB_CODEX_BIN': launcher}
    for key in ('CODEX_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME'):
        if key in os.environ:
            values[key] = os.environ[key]
    return '[Service]\n' + ''.join('Environment='+systemd_value(f'{key}={value}')+'\n'
                                    for key, value in values.items())


def service(repo, script, *args, on_failure=None, timeout='8h'):
    argv = [sys.executable, '-I', '-B', str(script), '--repo', str(repo), *map(str, args)]
    exec_start = ' '.join(systemd_value(item).replace('$', '$$') for item in argv)
    failure = f'OnFailure={on_failure}\n' if on_failure else ''
    # WorkingDirectory is parsed as a single literal path by systemd.
    directory = str(repo).replace('%', '%%').rstrip('/') + '/'
    return ('[Unit]\n' + failure + '[Service]\nType=oneshot\nExecStart='+exec_start
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


def verify_units(*names, run_services=True):
    for name in names:
        if systemctl('show', f'{name}.service', '--property=LoadState', '--value') != 'loaded':
            raise RuntimeError(f'Serviço {name} não carregado no host')
        if systemctl('is-enabled', f'{name}.timer') != 'enabled':
            raise RuntimeError(f'Timer {name} não habilitado no host')
        if systemctl('is-active', f'{name}.timer') != 'active':
            raise RuntimeError(f'Timer {name} não ativo no host')
        if run_services:
            systemctl('start', f'{name}.service')
            result = systemctl('show', f'{name}.service', '--property=Result', '--value')
            if result != 'success':
                raise RuntimeError(f'Serviço {name} sem execução bem-sucedida no host')


def install_watchdog(repo, package, home, units, *, activated=False):
    entry = 'watchdog_launcher.py' if activated else 'rescue_watchdog.py'
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
        result = promote_approved_supervisor(repo, home, expected_merge=merge)
        try:
            install_watchdog(repo, Path(result['package']), home, units, activated=True)
            unit_pair(units, supervisor,
                      service(repo, home/'stable/stable_supervisor.py', '--home', home,
                              '--once', '--max-repairs', 1, on_failure=f'{watchdog}.service'),
                      '*:0/30')
            if enable:
                systemctl('daemon-reload')
                if release_timer:
                    verify_units('gojob-runner-release', run_services=False)
                systemctl('enable', '--now', f'{watchdog}.timer', f'{supervisor}.timer')
                verify_units(watchdog, supervisor)
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
    return {**result, 'host_verified': enable}


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
