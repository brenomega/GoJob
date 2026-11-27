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
import subprocess
import signal
import threading
import tempfile
import time
from contextlib import contextmanager
from pathlib import Path
from gojob_runner.agent_privacy import redact as scrub, report_context

STATES = frozenset({'PREFLIGHT','PREPARING','REVIEWING','REPAIRING',
                    'VERIFYING','MERGING','WAITING_EXTERNAL','NEEDS_USER',
                    'INSTALLED','FAILED_INTERNAL'})
HOST_ONLY = ('systemctl --user','dbus','d-bus','user bus',
             'operation not permitted','failed to connect to bus')


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


def run_codex_monitored(argv, *, cwd, env, timeout, ledger, role):
    """Execute an agent with an observable heartbeat and bounded private output.

    The release unit sets GOJOB_MONITOR_CODEX=1 in the controller.
    The agent gets only its allowed runtime environment. On timeout the
    controller terminates the isolated child group and preserves the worktree.
    """
    if os.environ.get('GOJOB_MONITOR_CODEX')!='1':
        return subprocess.run(argv,cwd=cwd,env=env,text=True,
                              capture_output=True,timeout=timeout)
    start=time.monotonic()
    with tempfile.TemporaryDirectory(prefix='.active-agent-',dir=ledger.root) as folder:
        root=Path(folder)
        out=root/'stdout'; err=root/'stderr'
        def tail(path):
            with path.open('rb') as data:
                data.seek(0,2)
                data.seek(max(0,data.tell()-24000))
                return data.read().decode('utf-8',errors='replace')
        with out.open('wb') as stdout,err.open('wb') as stderr:
            os.fchmod(stdout.fileno(),0o600)
            os.fchmod(stderr.fileno(),0o600)
            proc=subprocess.Popen(argv,cwd=cwd,env=env,stdout=stdout,
                                  stderr=stderr,start_new_session=True)
            ledger.emit('REPAIRING' if 'implementer' in role else 'REVIEWING',
                        'agent_process_started',role=role,pid=proc.pid)
            try:
                while True:
                    remaining=timeout-(time.monotonic()-start)
                    if remaining<=0:
                        raise subprocess.TimeoutExpired(argv,timeout)
                    try:
                        rc=proc.wait(timeout=min(20,remaining))
                        break
                    except subprocess.TimeoutExpired:
                        elapsed=time.monotonic()-start
                        if elapsed>=timeout:
                            raise subprocess.TimeoutExpired(argv,timeout)
                        ledger.emit('REPAIRING' if 'implementer' in role else 'REVIEWING',
                                    'agent_heartbeat',role=role,pid=proc.pid,
                                    elapsed_seconds=round(elapsed))
                        print(f'[GOJOB AGENT] {role}: ativo há {int(elapsed)}s',flush=True)
            except (BaseException,):
                try:os.killpg(proc.pid,signal.SIGTERM)
                except ProcessLookupError:pass
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:pass
                try:os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                proc.wait(timeout=5)
                ledger.emit('WAITING_EXTERNAL','agent_aborted',role=role,
                            elapsed_seconds=round(time.monotonic()-start))
                raise
        elapsed=round(time.monotonic()-start)
        ledger.emit('REPAIRING' if 'implementer' in role else 'REVIEWING',
                    'agent_process_finished',role=role,elapsed_seconds=elapsed,
                    returncode=rc)
        return subprocess.CompletedProcess(argv,rc,stdout=tail(out),stderr=tail(err))

class ReleaseLedger:
    def __init__(self,version,home=None):
        self.version=version
        self.home=Path(home or Path.home()/'.local/share/gojob-runner')
        self.root=self.home/'release-control'/version
        self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.events=self.root/'events.jsonl'
        self.status_path=self.root/'status.json'
        self._status_lock=threading.Lock()

    def status(self):
        try:
            status=json.loads(self.status_path.read_text())
        except FileNotFoundError:
            return {'state':'PREFLIGHT','version':self.version}
        if not isinstance(status,dict) or status.get('state') not in STATES:
            raise ValueError('Estado persistido de release inválido; preservar para reconciliação')
        return status

    def emit(self,state,event,**info):
        with self._status_lock:
            return self._emit(state,event,info)

    def _emit(self,state,event,info):
        if state not in STATES:raise ValueError('unknown release state '+state)
        current=self.status()
        if current.get('state')=='NEEDS_USER':
            return current
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
            'returncode':returncode,
            'report_summary':scrub((report or {}).get('summary',''))[:2000]})
        return location

    @contextmanager
    def exclusive(self,nonblock=True):
        lock=self.home/'release-control/release-global.lock'
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
                     'docs/TESTING_POLICY.md','docs/RUNNER_OPERATING_CONTRACT.md',
                     'docs/RELEASE_ARCHITECTURE.md','docs/RUNNER_THROUGHPUT.md'):
            documents[name]=checked_read(worktree/name,14000)
        records=sorted((self.root/'agent-logs').glob('*.json'),key=lambda f:f.name)[-5:]
        agent_logs={}
        for record in records:
            try:
                item=json.loads(record.read_text())
                agent_logs[record.name]={key: scrub(item[key]) for key in ('role','returncode') if key in item}
            except (OSError,ValueError):
                continue
        try:
            events=[json.loads(line) for line in self.events.read_text(encoding='utf8').splitlines()[-12:]]
            event_lines=[{key: item.get(key) for key in ('state','event','timestamp')} for item in events]
        except (OSError,ValueError):
            event_lines=[]
        packet={'version':self.version,'stage':stage,'error':scrub(error)[:5000],
                'attempt':attempt,'recorded_at':time.time(),
                'environment_boundary':{'agent':'Codex sandbox/workspace-write/read-only',
                    'systemd':'real systemd validation belongs to installer host; sandbox DBus is NOT a production verdict',
                    'git_mutations':'only controller, never a Codex agent'},
                'worktree':str(worktree),'git':{
                    'head':safe_git(worktree,'rev-parse','HEAD'),
                    'branch':safe_git(worktree,'branch','--show-current'),
                    'diff_stat':safe_git(worktree,'diff','--shortstat'),
                    'recent_commits':safe_git(worktree,'log','-6','--format=%H')},
                'feedback':report_context(feedback or {}),'developer_report':report_context(last_report or {}),
                'agent_logs':agent_logs,'recent_events':event_lines,'documents':documents,'previous_status':self.status().get('state')}
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
                 'agent_logs':agent_logs,
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
