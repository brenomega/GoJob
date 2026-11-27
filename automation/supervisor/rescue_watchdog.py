#!/usr/bin/env python3
"""Independent recovery watchdog. Does not import stable_supervisor or product runner.

Runs in a separate systemd user unit on failure and every 10 minutes. It
collects evidence when supervisor fails before its own exception handler.
A verified older supervisor is used only as a fallback repair engine, never
for rewriting product state or bypassing policy.
"""
from __future__ import annotations
import argparse,fcntl,glob,hashlib,importlib.util,json,os,subprocess,sys,time
from pathlib import Path
from schema_protocol import prepared_schema
from recovery_context import redact
from recovery_dispatch import read_json,triage_event,repair_allowed,record_repair_attempt,save_json,promote_approved_supervisor

MODEL='gpt-6.2-sol'

def agent(repo,prompt,schema,write):
    import tempfile
    with tempfile.TemporaryDirectory(prefix='gojob-rescue-') as td:
        target=Path(td)/'response.json'
        cli_schema=prepared_schema(schema,td)
        env=os.environ.copy()
        for name in ('GH_TOKEN','GITHUB_TOKEN','OPENAI_API_KEY','CODEX_API_KEY','TELEGRAM_BOT_TOKEN','GOJOB_VAULT_KEY'):
            env.pop(name,None)
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
            '-p','ActiveState','-p','SubState','-p','Result','-p','ExecMainStatus','-p','MainPID'],
            capture_output=True,text=True,timeout=8)
        if p.returncode:return {'unavailable':redact(p.stderr)[:200]}
        return dict(line.split('=',1) for line in p.stdout.splitlines() if '=' in line)
    except Exception as e:return {'unavailable':type(e).__name__}

def fallback_repair(repo,home,inc,info,stable_folder):
    # Source chosen only from verified previous local installations; not a
    # spontaneous unchecked download or the mutable product checkout.
    candidates=sorted(Path(stable_folder).glob('stable_supervisor.previous.*.py'),
                      key=lambda p:p.stat().st_mtime,reverse=True)
    current=Path(stable_folder)/'stable_supervisor.py'
    if current.exists():candidates.append(current)
    errors=[]
    for candidate in candidates:
        module=None
        try:
            spec=importlib.util.spec_from_file_location('gojob_last_good_'+hashlib.sha256(str(candidate).encode()).hexdigest()[:12],candidate)
            module=importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            if not callable(getattr(module,'repair',None)):
                raise RuntimeError('Repair API absent')
            repair_info=dict(info)
            # Older last-known-good supervisors do not know the v3.4 dossier
            # format. Pass its private context through the incident payload,
            # not through the public GitHub Issue body (which uses error only).
            if '.previous.' in candidate.name:
                dossier=Path(home)/'incidents'/inc/'context.json'
                if dossier.is_file():
                    repair_info['handoff_context']=redact(dossier.read_text(encoding='utf-8',errors='replace'))[:60000]
                    repair_info['handoff_sha256']=hashlib.sha256(dossier.read_bytes()).hexdigest()
            return module.repair(repo,home,inc,repair_info)
        except Exception as e:
            errors.append(candidate.name+': '+type(e).__name__+': '+redact(e)[:250])
            # An exception during an external write could have created a PR;
            # never blindly repeat with another candidate after side effects.
            if callable(getattr(module,'repair',None)):break
    raise RuntimeError('Fallback repair engines unavailable: '+'; '.join(errors[-3:]))

def check(repo,home,*,force=False,now=None):
    repo=Path(repo).resolve();home=Path(home).resolve()
    now=time.time() if now is None else now
    status=service_status()
    active=status.get('ActiveState')
    result=status.get('Result')
    session=read_json(home/'supervisor_session.json',{})
    # An idle, successful one-shot is normal. Neither active Codex jobs nor
    # a disabled timer are grounds to mutate Git history.
    failed=(active=='failed' or (active not in ('active','activating')
                               and result not in (None,'','success')))
    stale=(session.get('phase')=='RUNNING' and now-session.get('started_at',now)>9*3600 and active not in ('active','activating'))
    if not (failed or stale or force):return {'status':'HEALTHY_OR_IDLE','service':status}
    info_text='Supervisor unexpected stop: '+json.dumps({'status':status,'session':session},ensure_ascii=False)
    fingerprint=hashlib.sha256((str(repo)+'|watchdog|'+info_text).encode()).hexdigest()[:14]
    folder=home/'incidents'/fingerprint
    prior=read_json(folder/'incident.json',{})
    info={'id':fingerprint,'repo':str(repo),'kind':'watchdog_supervisor_crash',
          'error':redact(info_text)[:2100],'time':now,'first_seen':prior.get('first_seen',now),
          'occurrences':int(prior.get('occurrences',0))+1}
    save_json(folder/'incident.json',info)
    diagnosis,evidence=triage_event(repo,home,folder,info,agent_func=agent,
                                     source='independent_watchdog',exit_code=status.get('ExecMainStatus'))
    outcome={'status':diagnosis.get('decision'),'incident':fingerprint}
    if diagnosis.get('decision')=='REPAIR_RUNNER' and repair_allowed(folder):
        try:
            old=read_json(folder/'repair.json',None)
            approved=old if old is not None else fallback_repair(repo,home,fingerprint,info,home/'stable')
            save_json(folder/'repair.json',approved)
            outcome['repair']=approved
            outcome['deployment']=promote_approved_supervisor(repo,home,expected_merge=approved.get('merge'))
            outcome['status']='REPAIRED'
            record_repair_attempt(folder,'SUCCEEDED')
        except Exception as exc:
            record_repair_attempt(folder,'FAILED',str(exc))
            outcome['status']='REPAIR_PENDING_OR_FAILED'
            outcome['error']=redact(exc)[:500]
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
    return 0

if __name__=='__main__':sys.exit(main())
