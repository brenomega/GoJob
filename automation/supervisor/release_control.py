"""Independent, stdlib-only release control plane.

Release installation must not depend on the not-yet-installed supervisor. The
ledger is private and append-only, and the next invocation can see exactly
which operation failed. The controller never approves an agent's own changes.
"""
from __future__ import annotations
import fcntl
import hashlib
import json
import os
import re
import subprocess
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path

STATES = frozenset({'PREFLIGHT','PREPARING','REVIEWING','REPAIRING',
                    'VERIFYING','MERGING','WAITING_EXTERNAL','NEEDS_USER',
                    'INSTALLED','FAILED_INTERNAL'})
HOST_ONLY = ('systemctl --user','dbus','d-bus','user bus',
             'operation not permitted','failed to connect to bus')


def scrub(text):
    value=str(text)
    value=re.sub(r'(?i)(?:gh[pousr]_|gsk_|sk-)[a-z0-9_-]{14,}', '[REDACTED]',value)
    value=re.sub(r'(?im)((?:token|password|secret|api[_-]?key|authorization|cookie)\s*[:=]\s*)[^\s,}]+',r'\1[REDACTED]',value)
    return value


def atomic_json(target, data):
    target=Path(target);target.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd,name=tempfile.mkstemp(prefix='.gojob-',dir=target.parent)
    try:
        with os.fdopen(fd,'w',encoding='utf-8') as out:
            os.fchmod(out.fileno(),0o600)
            json.dump(data,out,ensure_ascii=False,indent=2,sort_keys=True,default=str)
            out.write('\n');out.flush();os.fsync(out.fileno())
        os.replace(name,target)
    finally:
        if os.path.exists(name):os.unlink(name)


def checked_read(path,limit=12000):
    path=Path(path)
    if not path.is_file() or path.is_symlink():return {'unavailable':str(path)}
    try:
        return {'sha256':hashlib.sha256(path.read_bytes()).hexdigest(),
                'excerpt':scrub(path.read_text(encoding='utf8',errors='replace')[:limit]),
                'bytes':path.stat().st_size}
    except (OSError,UnicodeError) as exc:return {'unavailable':type(exc).__name__}


def safe_git(repo,*args):
    try:
        run=subprocess.run(['git',*args],cwd=repo,capture_output=True,text=True,timeout=14,
                           env={**os.environ,'GIT_OPTIONAL_LOCKS':'0'})
        return {'code':run.returncode,'out':scrub(run.stdout[-2800:]),'err':scrub(run.stderr[-700:])}
    except (OSError,subprocess.TimeoutExpired) as exc:return {'error':type(exc).__name__}


class ReleaseLedger:
    def __init__(self,version,home=None):
        self.version=version
        self.home=Path(home or Path.home()/'.local/share/gojob-runner')
        self.root=self.home/'release-control'/version
        self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.events=self.root/'events.jsonl'
        self.status_path=self.root/'status.json'

    def status(self):
        try:return json.loads(self.status_path.read_text())
        except (OSError,ValueError):return {'state':'PREFLIGHT','version':self.version}

    def emit(self,state,event,**info):
        if state not in STATES:raise ValueError('unknown release state '+state)
        payload={'timestamp':time.time(),'version':self.version,'state':state,'event':event,
                 'details':{str(k):scrub(v)[:3000] for k,v in info.items()}}
        fd=os.open(self.events,os.O_CREAT|os.O_WRONLY|os.O_APPEND,0o600)
        try:
            with os.fdopen(fd,'w',encoding='utf8') as out:
                out.write(json.dumps(payload,ensure_ascii=False,sort_keys=True)+'\n')
                out.flush();os.fsync(out.fileno())
        except Exception:raise
        atomic_json(self.status_path,payload)
        return payload

    def record_agent_io(self,role,returncode,stdout='',stderr='',report=None):
        """Private bounded transcript fragments for independent fault analysis.

        Never publish these files in Git; never include environment variables
        or raw command-line arguments which might expose local information.
        """
        import uuid
        folder=self.root/'agent-logs'
        filename=f'{int(time.time()*1000)}-{uuid.uuid4().hex[:10]}.json'
        location=folder/filename
        atomic_json(location,{'timestamp':time.time(),'role':scrub(role)[:100],
            'returncode':returncode,'stdout_tail':scrub(stdout)[-12000:],
            'stderr_tail':scrub(stderr)[-12000:],
            'report_summary':scrub((report or {}).get('summary',''))[:2000]})
        return location

    @contextmanager
    def exclusive(self,nonblock=True):
        lock=self.root/'release.lock'
        descriptor=os.open(lock,os.O_CREAT|os.O_RDWR,0o600)
        try:
            flags=fcntl.LOCK_EX|(fcntl.LOCK_NB if nonblock else 0)
            try:fcntl.flock(descriptor,flags)
            except BlockingIOError:
                yield False
                return
            yield True
        finally:
            try:fcntl.flock(descriptor,fcntl.LOCK_UN)
            finally:os.close(descriptor)

    def dossier(self,repo,stage,error,*,worktree=None,feedback=None,last_report=None,attempt=1):
        repo=Path(repo)
        worktree=Path(worktree) if worktree else repo
        documents={}
        for name in ('AGENTS.md','docs/PRODUCT_NORTH_STAR.md','docs/DECISION_LEDGER.md',
                     'docs/ENGINEERING_POLICY.md','docs/STRATEGIC_REVIEW.md',
                     'docs/TESTING_POLICY.md','docs/RUNNER_OPERATING_CONTRACT.md'):
            documents[name]=checked_read(worktree/name,14000)
        candidate=Path(__file__).resolve().parent
        documents['release/RECOVERY_PLAYBOOK.md']=checked_read(candidate/'docs/RECOVERY_PLAYBOOK.md',16000)
        documents['release/ARCHITECTURE.md']=checked_read(candidate/'docs/RELEASE_ARCHITECTURE.md',18000)
        records=sorted((self.root/'agent-logs').glob('*.json'),key=lambda f:f.name)[-5:]
        agent_logs={f.name:checked_read(f,12500) for f in records}
        try:
            event_lines=self.events.read_text(encoding='utf8').splitlines()[-12:]
        except OSError:
            event_lines=[]
        packet={'version':self.version,'stage':stage,'error':scrub(error)[:5000],
                'attempt':attempt,'recorded_at':time.time(),
                'environment_boundary':{'agent':'Codex sandbox/workspace-write/read-only',
                    'systemd':'real systemd validation belongs to installer host; sandbox DBus is NOT a production verdict',
                    'git_mutations':'only controller, never a Codex agent'},
                'worktree':str(worktree),'git':{
                    'head':safe_git(worktree,'rev-parse','HEAD'),
                    'branch':safe_git(worktree,'branch','--show-current'),
                    'status':safe_git(worktree,'status','--porcelain=v1','-uall'),
                    'diff_stat':safe_git(worktree,'diff','--stat'),
                    'recent_commits':safe_git(worktree,'log','-6','--format=%h %s')},
                'feedback':feedback or {},'developer_report':last_report or {},
                'agent_logs':agent_logs,'recent_events':event_lines,'documents':documents,'previous_status':self.status()}
        encoded=json.dumps(packet,ensure_ascii=False,default=str)
        sha=hashlib.sha256(encoded.encode()).hexdigest()
        location=self.root/'incidents'/f'{int(time.time())}-{sha[:12]}'
        atomic_json(location/'dossier.json',packet)
        self.emit('REPAIRING','incident_captured',stage=stage,context_sha256=sha,
                  context_path=location/'dossier.json',error=error)
        # Small enough to embed into the Codex prompt, but private full dossier remains on disk.
        compact={'version':self.version,'stage':stage,'error':packet['error'],
                 'environment_boundary':packet['environment_boundary'],
                 'git':packet['git'],'feedback':packet['feedback'],
                 'developer_report':packet['developer_report'],
                 'agent_logs':{k:v.get('excerpt','')[:8500] for k,v in agent_logs.items()},
                 'recent_events':event_lines,
                 'documents':{k:{'sha256':v.get('sha256'),'excerpt':v.get('excerpt','')[:6200]}
                              for k,v in documents.items()},
                 'dossier_path':str(location/'dossier.json'),'sha256':sha}
        return compact,location


def host_only_limitation(summary):
    s=str(summary).lower()
    return any(word in s for word in HOST_ONLY)


def environment_class(report):
    """Informative classifier; not authorization to bypass a reviewer or CI."""
    if report.get('status')=='DONE':return 'READY_FOR_HOST_GATES'
    text=' '.join(str(report.get(k,'')) for k in ('summary','limitations','tests'))
    if host_only_limitation(text):return 'AGENT_SANDBOX_HOST_VERIFICATION'
    return 'IMPLEMENTATION_BLOCKED'
