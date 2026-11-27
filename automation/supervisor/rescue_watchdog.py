#!/usr/bin/env python3
"""Independent recovery watchdog. Does not import stable_supervisor or product runner.

Runs in a separate systemd user unit on failure and every 10 minutes. It
collects evidence when supervisor fails before its own exception handler.
A verified older supervisor is used only as a fallback repair engine, never
for rewriting product state or bypassing policy.
"""
from __future__ import annotations
import argparse,fcntl,json,os,re,subprocess,sys,time
from pathlib import Path

if __name__ == '__main__':
    sys.path[:0]=map(str,Path(__file__).resolve().parents[:2])

from gojob_runner.agent_privacy import agent_env
from schema_protocol import prepared_schema
from recovery_context import redact
from recovery_dispatch import read_json,triage_event,save_json,incident_key,run_incident_repair,resume_pending_repairs
from service_execution import matches_execution, report_completion

MODEL='gpt-6.2-sol'
SOURCE_ROOT=Path(__file__).resolve().parents[2]

def agent(repo,prompt,schema,write):
    import tempfile
    with tempfile.TemporaryDirectory(prefix='gojob-rescue-') as td:
        target=Path(td)/'response.json'
        cli_schema=prepared_schema(schema,td)
        env=agent_env()
        import shutil
        binary=os.environ.get('GOJOB_CODEX_BIN')
        if not binary or not os.path.isfile(binary) or not os.access(binary,os.X_OK):
            binary=shutil.which('codex') or 'codex'
        cmd=[binary,'exec','--ephemeral','-C',str(repo),'--model',MODEL,
             '--config','model_reasoning_effort=high','--sandbox','read-only',
             '--output-schema',str(cli_schema),'--output-last-message',str(target),prompt]
        p=subprocess.run(cmd,cwd=repo,env=env,capture_output=True,text=True,timeout=1800)
        if p.returncode:raise RuntimeError('Watchdog diagnosis unavailable: '+redact(p.stderr)[-500:])
        if not target.is_file():raise RuntimeError('No diagnostic report')
        return json.loads(target.read_text())

def service_status():
    try:
        p=subprocess.run(['systemctl','--user','show','gojob-runner-supervisor.service',
            '-p','ActiveState','-p','SubState','-p','Result','-p','ExecMainStatus','-p','ExecMainPID',
            '-p','InvocationID','-p','ExecMainStartTimestampMonotonic','-p','ExecMainExitTimestampMonotonic'],
            capture_output=True,text=True,timeout=8)
        if p.returncode:return {'unavailable':redact(p.stderr)[:200]}
        return dict(line.split('=',1) for line in p.stdout.splitlines() if '=' in line)
    except Exception as e:return {'unavailable':type(e).__name__}

def fallback_repair(repo,home,inc,info,stable_folder):
    # Load each candidate in a new process so its own sibling modules win over
    # modules already loaded by this watchdog. An import failure has no effects.
    previous=Path(home)/'stable.previous'
    candidates=[Path(stable_folder),previous,Path(__file__).resolve().parent]
    load_engine=(
        'import json, sys; from pathlib import Path; '
        'folder=Path(sys.argv[1]).resolve(); '
        'sys.path[:0]=[str(folder), str(folder.parent)]; '
        'from release_package import verify_package; verify_package(folder.parents[1]); '
        'import stable_supervisor as engine; '
        'assert engine.DURABLE_REPAIR_PROTOCOL == 1; '
    )
    errors=[]
    for directory in dict.fromkeys(path.resolve() for path in candidates if path.is_dir()):
        args=[sys.executable,'-I','-B','-c',load_engine,str(directory)]
        check=subprocess.run(args,capture_output=True,text=True,timeout=20)
        if check.returncode:
            errors.append(directory.name+': '+redact(check.stderr)[-200:])
            continue
        handler=load_engine+(
            'payload=json.load(sys.stdin); result=engine.repair(*payload); '
            'print("GOJOB_REPAIR_RESULT "+json.dumps(result));'
        )
        result=subprocess.run([*args[:4],handler,str(directory)],
                              input=json.dumps([str(repo),str(home),inc,info]),
                              capture_output=True,text=True,timeout=8*3600)
        # Do not invoke another generation after an attempt that could write.
        if result.returncode:
            raise RuntimeError('Reparo via geração anterior pendente: '+redact(result.stderr)[-450:])
        for line in reversed(result.stdout.splitlines()):
            if line.startswith('GOJOB_REPAIR_RESULT '):
                return json.loads(line.removeprefix('GOJOB_REPAIR_RESULT '))
        raise RuntimeError('Reparo iniciado sem resultado verificável')
    raise RuntimeError('Fallback repair engines unavailable: '+'; '.join(errors))


def check(repo,home,*,force=False,now=None):
    repo=Path(repo).resolve();home=Path(home).resolve()
    now=time.time() if now is None else now
    def repair(repo,home,ident,evidence):
        return fallback_repair(repo,home,ident,evidence,home/'stable')
    status=service_status()
    resumed=resume_pending_repairs(repo,home,repair)
    active=status.get('ActiveState')
    result=status.get('Result')
    session=read_json(home/'supervisor_session.json',{})
    # An idle, successful one-shot is normal. Neither active Codex jobs nor
    # a disabled timer are grounds to mutate Git history.
    failed=(active=='failed' or (active not in ('active','activating')
                               and result not in (None,'','success')))
    stale=(session.get('phase')=='RUNNING' and now-session.get('started_at',now)>9*3600 and active not in ('active','activating'))
    if not (failed or stale or force):return resumed or {'status':'HEALTHY_OR_IDLE','service':status}
    shared=session.get('incident_id')
    recorded=(session.get('repo')==str(repo) and re.fullmatch(r'[0-9a-f]{14}',shared or '')
              and matches_execution(session,status))
    failure = {'Result': result, 'ExecMainStatus': status.get('ExecMainStatus')}
    info_text='Supervisor unexpected stop: '+json.dumps(failure,sort_keys=True)
    fingerprint=shared if recorded else incident_key(repo,'watchdog_supervisor_crash',info_text)
    folder=home/'incidents'/fingerprint
    prior=read_json(folder/'incident.json',{})
    if recorded and prior.get('id')==fingerprint and prior.get('repo')==str(repo):
        info=prior
    else:
        fingerprint=incident_key(repo,'watchdog_supervisor_crash',info_text)
        folder=home/'incidents'/fingerprint
        prior=read_json(folder/'incident.json',{})
        info={'id':fingerprint,'repo':str(repo),'kind':'watchdog_supervisor_crash',
              'error':redact(info_text)[:2100],'time':now,'first_seen':prior.get('first_seen',now),
              'occurrences':int(prior.get('occurrences',0))+1}
        save_json(folder/'incident.json',info)
    diagnosis,evidence=triage_event(repo,home,folder,info,agent_func=agent,
                                     source='independent_watchdog',runner_sha=info.get('runner_sha'),
                                     exit_code=status.get('ExecMainStatus'))
    outcome=run_incident_repair(repo,home,folder,info,diagnosis,repair)
    outcome['incident']=fingerprint
    save_json(folder/'watchdog_action.json',outcome)
    print('[GOJOB RESCUE] '+json.dumps(outcome,ensure_ascii=False)[:1300],flush=True)
    return outcome

def main(argv=None):
    p=argparse.ArgumentParser()
    p.add_argument('--repo',required=True)
    p.add_argument('--home',default=str(Path.home()/'.local/share/gojob-runner'))
    p.add_argument('--force',action='store_true')
    a=p.parse_args(argv)
    home=Path(a.home).expanduser().resolve();home.mkdir(parents=True,exist_ok=True)
    with (home/'rescue_watchdog.lock').open('a') as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:return 0
        check(a.repo,home,force=a.force)
        report_completion(home,'gojob-runner-rescue',SOURCE_ROOT)
    return 0

if __name__=='__main__':sys.exit(main())
