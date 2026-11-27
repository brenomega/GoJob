#!/usr/bin/env python3
"""Host-owned, independently scheduled continuation of a runner release.

This service is installed BEFORE attempting an upgrade. If a Developer,
Reviewer, network call or the installation itself fails, the installer returns
to the user but the user-systemd timer invokes the same crash-resumable release
controller again. The old development supervisor is not required for recovery.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import time
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from release_control import ReleaseLedger, atomic_json, scrub
from install_supervisor import systemd_codex_runtime, _systemd_value

ROOT=Path(__file__).resolve().parent
VERSION=(ROOT/'RUNNER_VERSION').read_text().strip()


def install_release_worker(repo,*,enable=True):
    repo=Path(repo).expanduser().resolve()
    if not (repo/'.git').exists():raise RuntimeError('release worker requires existing Git checkout')
    home=Path.home()/'.local/share/gojob-runner'
    stable=home/'release-packages'/VERSION
    # Once activated, make the source immutable-by-convention. Repeated install
    # of a version never mutates a package while an already running worker may
    # be importing it. Each new version gets a separate directory.
    if not stable.exists():
        stable.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        temp=Path(tempfile.mkdtemp(prefix='.release-package-',dir=stable.parent))
        try:
            shutil.copytree(ROOT,temp,dirs_exist_ok=True,ignore=shutil.ignore_patterns('__pycache__','*.pyc'))
            os.replace(temp,stable)
        finally:
            if temp.exists():shutil.rmtree(temp)
    if not (stable/'upgrade_runner.py').is_file():
        raise RuntimeError('Release worker package missing controller')
    runtime,_=systemd_codex_runtime()
    units=Path.home()/'.config/systemd/user'
    units.mkdir(parents=True,exist_ok=True)
    script=stable/'release_worker.py'
    args=[sys.executable,str(script),'--repo',str(repo)]
    service=units/'gojob-runner-release.service'
    timer=units/'gojob-runner-release.timer'
    dropin=units/'gojob-runner-release.service.d/90-gojob-runtime.conf'
    dropin.parent.mkdir(parents=True,exist_ok=True)
    service_text=(
        '[Unit]\nDescription=GoJob independent runner release recovery controller\n'
        '[Service]\nType=oneshot\nExecStart='+ ' '.join(shlex.quote(x) for x in args)+'\n'
        'WorkingDirectory='+str(repo)+'\nTimeoutStartSec=7h\n')
    timer_text=(
        '[Unit]\nDescription=Resume incomplete GoJob runner release and repair\n'
        '[Timer]\nOnBootSec=3min\nOnCalendar=*:0/15\nPersistent=true\n'
        'Unit=gojob-runner-release.service\n[Install]\nWantedBy=timers.target\n')
    def write_atomic(path,content):
        fd,name=tempfile.mkstemp(dir=path.parent,prefix='.unit-')
        try:
            with os.fdopen(fd,'w',encoding='utf8') as out:
                out.write(content);out.flush();os.fsync(out.fileno())
            os.chmod(name,0o600)
            os.replace(name,path)
        finally:
            if os.path.exists(name):os.unlink(name)
    write_atomic(service,service_text)
    write_atomic(timer,timer_text)
    write_atomic(dropin,runtime)
    if enable:
        subprocess.run(['systemctl','--user','daemon-reload'],check=True)
        subprocess.run(['systemctl','--user','enable','--now','gojob-runner-release.timer'],check=True)
    return {'service':str(service),'timer':str(timer),'package':str(stable),'enabled':enable}


def once(repo):
    from upgrade_runner import upgrade
    ledger=ReleaseLedger(VERSION)
    previous=ledger.status()
    if previous.get('state')=='NEEDS_USER':
        return {'status':'NEEDS_USER','reason':'same-version release awaits explicit decision'}
    retry_file=ledger.root/'retry_schedule.json'
    try:retry=json.loads(retry_file.read_text())
    except (OSError,ValueError):retry={}
    if retry.get('next_due_at',0)>time.time():
        return {'status':'COOLDOWN','next_due_at':retry['next_due_at'],
                'attempts':retry.get('attempts',0)}
    try:
        result=upgrade(repo)
        if result.get('status')=='ALREADY_RUNNING':return result
        if result.get('status') in ('UPGRADED','ALREADY_CURRENT'):
            if previous.get('state')!='INSTALLED':
                from quickstart import enable_automation
                enable_automation(Path(repo),launch=True)
            ledger.emit('INSTALLED','release_cycle_finished',result=result)
            retry_file.unlink(missing_ok=True)
        return result
    except Exception as exc:
        message=scrub(str(exc))[-1300:]
        signature=hashlib.sha256(message.encode()).hexdigest()
        attempts=retry.get('attempts',0)+1 if retry.get('signature')==signature else 1
        transient=any(term in message.lower() for term in (
            'timeout','timed out','rate limit','429','503','temporarily','network',
            'connection reset','codex unavailable','quota'))
        state='NEEDS_USER' if ('NONDELEGABLE' in message or 'Três rodadas' in message
                                or (attempts>=3 and not transient)) else 'WAITING_EXTERNAL'
        # Bound consumption. Recoverable infrastructure outages are retried
        # after backoff, but never in a tight model-calling loop.
        delay=min(900*(2**min(attempts-1,5)),21600)
        atomic_json(retry_file,{'signature':signature,'attempts':attempts,
                    'next_due_at':time.time()+delay,'last_error':message,
                    'transient':transient})
        ledger.emit(state,'release_cycle_deferred',cause=message,
                    attempts=attempts,next_retry_seconds=delay)
        print('[GOJOB RELEASE RECOVERY] '+state+': '+message,flush=True)
        return {'status':state,'reason':message,'attempts':attempts,
                'next_retry_seconds':delay}


def main(argv=None):
    parser=argparse.ArgumentParser()
    parser.add_argument('--repo',required=True)
    options=parser.parse_args(argv)
    result=once(options.repo)
    print('[GOJOB RELEASE RECOVERY] '+str(result),flush=True)
    return 0

if __name__=='__main__':sys.exit(main())
