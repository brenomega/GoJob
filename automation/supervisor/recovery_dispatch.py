"""Independent incident triage, escalation, and proactive runner risk reviews.

This module does not import the (potentially broken) development runner. It never
approves changes, merges PRs, alters candidate records, or bypasses user policy.
"""
from __future__ import annotations
import hashlib,json,os,time
from pathlib import Path
try:
    from .recovery_context import gather,prompt_context,private_write,redact
except ImportError:
    from recovery_context import gather,prompt_context,private_write,redact

TRIAGE_SCHEMA={
 'type':'object','additionalProperties':False,
 'properties':{
  'decision':{'type':'string','enum':['REPAIR_RUNNER','RETRY_TRANSIENT','TASK_RECOVERY','NEEDS_USER','ENVIRONMENT','POLICY_STOP','NO_ACTION']},
  'cause':{'type':'string'},'evidence':{'type':'array','items':{'type':'string'}},
  'plan':{'type':'string'},'confidence':{'type':'string','enum':['high','medium','low']},
  'risk_if_wrong':{'type':'string'}},
 'required':['decision','cause','evidence','plan','confidence','risk_if_wrong']}
PROACTIVE_SCHEMA={
 'type':'object','additionalProperties':False,
 'properties':{
  'decision':{'type':'string','enum':['NO_ACTION','REPAIR_RUNNER','FOLLOW_UP']},
  'summary':{'type':'string'},'evidence':{'type':'array','items':{'type':'string'}},
  'hypothesis':{'type':'string'},'reproduction':{'type':'string'},
  'expected_failure':{'type':'string'},
  'priority':{'type':'string','enum':['critical','high','medium','low']},
  'proposed_tests':{'type':'array','items':{'type':'string'}}},
 'required':['decision','summary','evidence','hypothesis','reproduction','expected_failure','priority','proposed_tests']}

MAX_TRIAGE_ATTEMPTS=3
MAX_REPAIR_ATTEMPTS=2

def read_json(path,default):
    try:return json.loads(Path(path).read_text())
    except (OSError,ValueError):return default

def save_json(path,payload):
    private_write(path,json.dumps(payload,ensure_ascii=False,indent=2,sort_keys=True))

def schema_file(home,name,schema):
    dest=Path(home)/'internal-schemas'/name
    serialized=json.dumps(schema,ensure_ascii=False,sort_keys=True,indent=2)
    if not dest.exists() or dest.read_text()!=serialized:private_write(dest,serialized)
    return dest

def immutable_human_stop(kind,error):
    # A model is not allowed to reclassify a revoked authorization as repairable.
    s=(str(kind)+' '+str(error)).lower()
    return any(x in s for x in ('revoked approval','consent revoked','unauthorized application',
        'no candidate consent','forbidden action','requires explicit human approval'))

def triage_event(repo,home,folder,info,*,agent_func,source='supervisor',runner_sha=None,
                 exit_code=None,force=False):
    """Always try independent diagnosis before treating an incident as terminal.

    On quota/outage evidence is saved and retried in later systemd cycles. A
    deterministic human veto cannot be bypassed by model output.
    """
    folder=Path(folder);home=Path(home);now=time.time()
    evidence,digest=gather(home,repo,info.get('kind','unknown'),info.get('error',''),folder,
                           runner_sha=runner_sha,exit_code=exit_code,source=source)
    status=read_json(folder/'triage_state.json',{})
    semantic=hashlib.sha256(json.dumps({'kind':info.get('kind'),'error':info.get('error'),
        'runner_sha':runner_sha,'task_stages':{k:(v.get('stage'),v.get('reason'),v.get('blocked_reason'),v.get('head'))
             for k,v in evidence.get('task_state',{}).items()},
        'git_head':evidence.get('git',{}).get('head'),
        'policies':{k:v.get('sha256') for k,v in evidence.get('documents',{}).items()}},
        sort_keys=True,default=str).encode()).hexdigest()
    same=status.get('semantic_fingerprint')==semantic
    if same and status.get('decision') and not force:return status['decision'],evidence
    # Content changes over time; avoid unlimited fresh calls when no substantive
    # new evidence is available. Retry on timer with backoff if model was down.
    if now<float(status.get('next_attempt_at',0)) and not force:
        return {'decision':'DEFERRED','cause':'cooldown pending','evidence':[]},evidence
    if int(status.get('attempts',0))>=MAX_TRIAGE_ATTEMPTS and not force and same:
        return {'decision':'ESCALATED','cause':'triage attempt limit','evidence':[]},evidence
    schema=schema_file(home,'incident_triage.json',TRIAGE_SCHEMA)
    prompt=('YOU ARE THE INDEPENDENT GOJOB RUNNER INCIDENT DIAGNOSTICIAN, GPT-6.2 SOL/HIGH. '
            'You are distinct from the implementer and the reviewer. FIRST determine '
            'causality and whether the runner itself malfunctioned; do not trust a '
            'PolicyFailure or ExternalFailure label as conclusive. Distinguish '
            'transient external limitations, correctable runner defects, task defects, '
            'and explicit human prohibitions. Preserve all Git, state and user decisions. '
            'A repairable issue must include a concrete reproduction and a minimal '
            'corrective plan; if details are absent say what is missing. '
            'NEVER propose bypassing human consent, CAPTCHA, tests or reviews. '
            'Do not write files or call GitHub; output only the requested JSON.\n'
            +prompt_context(evidence,digest))
    status={'context_sha256':digest,'semantic_fingerprint':semantic,
            'attempts':(int(status.get('attempts',0)) if same else 0)+1,
            'last_attempt_at':now,'status':'DIAGNOSING','source':source}
    save_json(folder/'triage_state.json',status)
    try:
        result=agent_func(Path(repo),prompt,schema,False)
        if not isinstance(result,dict) or result.get('decision') not in TRIAGE_SCHEMA['properties']['decision']['enum']:
            raise RuntimeError('Invalid diagnostic decision')
        if not isinstance(result.get('evidence'),list) or not result.get('cause') or not result.get('plan'):
            raise RuntimeError('Diagnostic omitted cause/evidence/plan')
        if immutable_human_stop(info.get('kind'),info.get('error')):
            result={**result,'decision':'POLICY_STOP','cause':'Nondelegable consent/approval veto: '+str(result.get('cause'))}
        status.update({'decision':result,'status':'DIAGNOSED','next_attempt_at':0})
        save_json(folder/'triage.json',result);save_json(folder/'triage_state.json',status)
        return result,evidence
    except Exception as e:
        status.update({'status':'DIAGNOSIS_UNAVAILABLE',
                       'error':redact(type(e).__name__+': '+str(e))[:1000],
                       'next_attempt_at':now+min(3600,120*2**(status['attempts']-1))})
        save_json(folder/'triage_state.json',status)
        return {'decision':'DEFERRED','cause':'diagnostic agent unavailable; evidence preserved',
                'evidence':[],'error':status['error']},evidence

def repair_allowed(folder,now=None):
    now=time.time() if now is None else now
    state=read_json(Path(folder)/'repair_attempts.json',{'attempts':0})
    return (int(state.get('attempts',0))<MAX_REPAIR_ATTEMPTS
            and now>=float(state.get('next_attempt_at',0)))

def record_repair_attempt(folder,status,error=None):
    path=Path(folder)/'repair_attempts.json'
    old=read_json(path,{'attempts':0,'history':[]})
    n=old['attempts']+1
    old['attempts']=n
    old.setdefault('history',[]).append({'time':time.time(),'status':status,'error':redact(error or '')[:800]})
    old['last_status']=status
    # Future cycles retry, but never perpetually and never in a tight loop.
    old['next_attempt_at']=0 if status=='SUCCEEDED' else time.time()+min(10800,600*2**(n-1))
    save_json(path,old)
    return old

def proactive_review(repo,home,*,agent_func,runner_sha,minimum_interval=86400):
    """Daily best-effort risk hunt, never turns speculation into an automatic merge."""
    home=Path(home);where=home/'proactive';where.mkdir(parents=True,exist_ok=True)
    last=read_json(where/'state.json',{})
    if time.time()-float(last.get('last_scan',0))<minimum_interval:
        return {'status':'COOLDOWN'}
    # Claim scan slot first so an unavailable CLI cannot cause a costly hot loop.
    save_json(where/'state.json',{'last_scan':time.time(),'runner_sha':runner_sha,'status':'STARTED'})
    identity=hashlib.sha256((str(Path(repo).resolve())+'|'+str(runner_sha)+'|daily').encode()).hexdigest()[:12]
    incident_folder=where/identity
    evidence,digest=gather(home,repo,'proactive_scan','Investigate latent runner defects before users notice them',
                           incident_folder,runner_sha=runner_sha,source='proactive')
    prompt=('PAPEL: INDEPENDENT PROACTIVE RUNNER AUDITOR. Inspect real code of the RUNNER, '
            'recent incidents and review histories; find a specific latent failure that '
            'would break task progression, contextual handoff, safe repair, release publication, '
            'or strategic review. Do not create a product feature, propose excessive tests, '
            'or infer a vulnerability without a reproduction. Return NO_ACTION when '
            'there is no concrete evidence. Do NOT make GitHub writes or modify files. '
            'Propose minimal runner-specific tests that would catch the hypothesized defect.\n'
            +prompt_context(evidence,digest))
    schema=schema_file(home,'proactive_runner_audit.json',PROACTIVE_SCHEMA)
    try:
        out=agent_func(Path(repo),prompt,schema,False)
        if not isinstance(out,dict) or out.get('decision') not in ('NO_ACTION','REPAIR_RUNNER','FOLLOW_UP'):
            raise RuntimeError('Invalid proactive report')
        if out.get('decision')=='REPAIR_RUNNER':
            if out.get('priority') not in ('critical','high') or not out.get('evidence') or not out.get('reproduction'):
                out['decision']='FOLLOW_UP'
        save_json(incident_folder/'proactive_report.json',out)
        save_json(where/'state.json',{'last_scan':time.time(),'runner_sha':runner_sha,
                                    'status':out['decision'],'report_dir':str(incident_folder)})
        return {'status':out['decision'],'report_dir':str(incident_folder),'report':out}
    except Exception as e:
        save_json(where/'state.json',{'last_scan':time.time()-minimum_interval+7200,'runner_sha':runner_sha,
                                    'status':'UNAVAILABLE','error':redact(e)[:500]})
        return {'status':'UNAVAILABLE','reason':redact(e)[:350]}

def promote_approved_supervisor(repo,home,expected_merge=None):
    """Deploy independently reviewed, merged supervisor code with a canary and rollback.

    Never deploys from an unmerged branch. The caller must have already verified
    PR review, CI and merge in the existing repair transaction. No code from
    the product checkout is executed for deployment verification.
    """
    import shutil,subprocess,sys,tempfile
    repo=Path(repo).resolve();home=Path(home).resolve()
    target=home/'stable'
    if not (target/'stable_supervisor.py').is_file():
        return {'status':'NO_INSTALLED_STABLE'}
    def git_show(ref):
        r=subprocess.run(['git','show','origin/develop:'+ref],cwd=repo,capture_output=True,timeout=25)
        if r.returncode:raise RuntimeError('Merged supervisor file missing: '+ref)
        return r.stdout
    p=subprocess.run(['git','fetch','origin','develop'],cwd=repo,text=True,capture_output=True,timeout=120)
    if p.returncode:raise RuntimeError('Cannot fetch merged supervisor for canary deployment')
    head=subprocess.run(['git','rev-parse','origin/develop'],cwd=repo,capture_output=True,text=True,timeout=20)
    if head.returncode:raise RuntimeError('Cannot confirm target commit')
    expected=(expected_merge.get('oid') if isinstance(expected_merge,dict) else expected_merge)
    if expected and head.stdout.strip()!=expected:
        raise RuntimeError('Develop moved after reviewed merge; preserve and revalidate SHA before deployment')
    paths={name:'automation/supervisor/'+name for name in
           ('stable_supervisor.py','recovery_context.py','recovery_dispatch.py','rescue_watchdog.py','schema_protocol.py')}
    source={name:git_show(path) for name,path in paths.items()}
    version=git_show('RUNNER_VERSION').decode().strip()
    with tempfile.TemporaryDirectory(prefix='gojob-supervisor-canary-',dir=home) as tmp:
        stage=Path(tmp)
        for name,content in source.items():
            (stage/name).write_bytes(content)
        for name in ('stable_supervisor.py','rescue_watchdog.py'):
            r=subprocess.run([sys.executable,'-B',str(stage/name),'--help'],cwd=stage,
                             text=True,capture_output=True,timeout=20)
            if r.returncode:raise RuntimeError('Canary supervisor failed: '+name+': '+r.stderr[-250:])
        backup=target/'last-known-good'
        backup.mkdir(parents=True,exist_ok=True,mode=0o700)
        originals={name:(target/name).read_bytes() if (target/name).exists() else None for name in source}
        if all(originals.get(name)==content for name,content in source.items()):
            return {'status':'ALREADY_CURRENT','version':version}
        # Make all backups first. No change to systemd units or Git metadata.
        for name,content in originals.items():
            if content is not None:private_write(backup/name,content.decode('utf-8'))
        installed=[]
        try:
            for name,content in source.items():
                private_write(target/name,content.decode('utf-8'))
                installed.append(name)
            result=subprocess.run([sys.executable,'-B',str(target/'stable_supervisor.py'),'--help'],
                                  cwd=target,text=True,capture_output=True,timeout=20)
            if result.returncode:raise RuntimeError('Installed supervisor canary failed')
        except Exception:
            for name in installed:
                previous=originals.get(name)
                if previous is None:(target/name).unlink(missing_ok=True)
                else:private_write(target/name,previous.decode('utf-8'))
            raise
        return {'status':'DEPLOYED','version':version,'files':list(source)}
