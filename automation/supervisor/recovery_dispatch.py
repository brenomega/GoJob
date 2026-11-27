"""Independent incident triage, escalation, and proactive runner risk reviews.

This module does not import the (potentially broken) development runner. It never
approves changes, merges PRs, alters candidate records, or bypasses user policy.
"""
from __future__ import annotations
import fcntl,hashlib,json,os,time
from contextlib import contextmanager
from pathlib import Path
from gojob_runner.agent_privacy import report_context
from gojob_runner.merge_evidence import OID
from release_package import activate_package, build_package, git_bytes
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
    persisted=status.get('decision') or {}
    if persisted.get('decision') in ('POLICY_STOP','NEEDS_USER'):
        return persisted,evidence
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
        result=report_context(result)
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

@contextmanager
def repair_lock(home,repo):
    identity=hashlib.sha256(str(Path(repo).resolve()).encode()).hexdigest()[:16]
    path=Path(home)/'repair-locks'/identity
    path.parent.mkdir(parents=True,mode=0o700,exist_ok=True)
    with path.open('a') as lock:
        try:
            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        yield True


def incident_key(repo, kind, error):
    import re
    stable=re.sub(r'(?:0x[0-9a-f]+|pid[ =:]+\d+|\d{4}-\d\d-\d\d[T ][0-9:.+Z-]+)',
                  '<runtime>',redact(error),flags=re.I)
    return hashlib.sha256(f'{Path(repo).resolve()}|{kind}|{stable}'.encode()).hexdigest()[:14]


def reserve_repair_attempt(folder):
    if not repair_allowed(folder):
        return False
    path=Path(folder)/'repair_attempts.json'
    old=read_json(path,{'attempts':0,'history':[]})
    old['attempts']+=1
    old.setdefault('history',[]).append({'time':time.time(),'status':'STARTED'})
    old['last_status']='STARTED'
    old['next_attempt_at']=time.time()+min(10800,600*2**(old['attempts']-1))
    save_json(path,old)
    return True


def record_repair_attempt(folder,status,error=None):
    path=Path(folder)/'repair_attempts.json'
    old=read_json(path,{'attempts':0,'history':[]})
    old.setdefault('history',[]).append({'time':time.time(),'status':status,'error':redact(error or '')[:800]})
    old['last_status']=status
    if status=='SUCCEEDED':
        old['next_attempt_at']=0
    save_json(path,old)
    return old


def repair_authorized(diagnosis):
    evidence=diagnosis.get('evidence')
    return (diagnosis.get('decision')=='REPAIR_RUNNER'
            and isinstance(evidence,list) and any(isinstance(item,str) and item.strip() for item in evidence)
            and all(isinstance(diagnosis.get(key),str) and diagnosis[key].strip() for key in ('cause','plan')))


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

def promote_approved_supervisor(repo,home,expected_merge):
    """Activate one complete, canary-checked generation from the approved merge."""
    expected=expected_merge.get('oid') if isinstance(expected_merge,dict) else expected_merge
    if not OID.fullmatch(expected or ''):
        raise ValueError('Promoção exige o SHA completo do merge aprovado')
    git_bytes(repo,'fetch','origin','develop')
    if len(git_bytes(repo,'show','-s','--format=%P',expected).split())!=2:
        raise ValueError('Promoção exige um merge commit de dois pais')
    if git_bytes(repo,'merge-base',expected,'origin/develop').decode().strip()!=expected:
        raise ValueError('Merge aprovado não está integrado em develop')
    active=Path(home)/'stable'
    if active.is_symlink():
        current=active.resolve().parent.parent.name
        if OID.fullmatch(current) and current!=expected:
            if git_bytes(repo,'merge-base',expected,current).decode().strip()==expected:
                raise ValueError('Geração ativa descende do merge solicitado; preservar instalação mais recente')
    package=build_package(repo,expected,Path(home)/'supervisor-generations')
    changed=activate_package(home,package)
    return {'status':'DEPLOYED' if changed else 'ALREADY_CURRENT',
            'version':(package/'RUNNER_VERSION').read_text().strip(),'commit':expected,
            'package':str(package)}


def run_incident_repair(repo, home, folder, info, diagnosis, repair, *, claim=None):
    """Reserve the shared budget before handing off to an agent or writing remotely."""
    if not repair_authorized(diagnosis):
        decision=diagnosis.get('decision')
        return {'status':'INSUFFICIENT_REPAIR_EVIDENCE' if decision=='REPAIR_RUNNER'
                else decision or 'UNCLASSIFIED'}
    folder=Path(folder)
    with repair_lock(home,repo) as acquired:
        if not acquired:
            return {'status':'REPAIR_BUSY','incident':info['id']}
        if (folder/'deployment.json').is_file():
            return {'status':'REPAIR_ALREADY_DEPLOYED','incident':info['id']}
        journal=Path(home)/'repairs'/(info['id']+'-review-history.json')
        pending=read_json(journal,{})
        if pending.get('needs_user'):
            return {'status':'NEEDS_USER','incident':info['id']}
        approval=read_json(folder/'repair.json',None)
        if approval:
            oid=approval.get('merge')
            oid=oid.get('oid') if isinstance(oid,dict) else oid
            if not oid or pending.get('merge_confirmed')!=oid:
                return {'status':'NEEDS_USER','incident':info['id'],
                        'reason':'Reconciliar aprovação anterior com journal e prova de merge'}
        resuming=bool(approval or pending.get('merge_confirmed'))
        if not resuming and not repair_allowed(folder):
            return {'status':'REPAIR_COOLDOWN_OR_EXHAUSTED','incident':info['id']}
        if claim is not None and not claim():
            return {'status':'DIAGNOSED_REPAIR_LIMIT_REACHED','incident':info['id']}
        if not resuming and not reserve_repair_attempt(folder):
            return {'status':'REPAIR_COOLDOWN_OR_EXHAUSTED','incident':info['id']}
        try:
            if approval is None:
                approval=repair(repo,home,info['id'],info)
                save_json(folder/'repair.json',approval)
            deployed=promote_approved_supervisor(repo,home,expected_merge=approval['merge'])
            save_json(folder/'deployment.json',deployed)
            record_repair_attempt(folder,'SUCCEEDED')
            (folder/'repair.failed').unlink(missing_ok=True)
            return {'status':'REPAIRED','result':approval,'deployment':deployed,'attempted':True}
        except Exception as exc:
            error=redact(type(exc).__name__+': '+str(exc))
            record_repair_attempt(folder,'FAILED',error)
            private_write(folder/'repair.failed',error)
            return {'status':'REPAIR_FAILED','error':error[:500],'incident':info['id'],'attempted':True}
