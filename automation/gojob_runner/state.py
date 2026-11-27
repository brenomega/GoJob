"""Atomic state and append-only journal. Journal payloads contain NO secrets."""
import json,os,tempfile,time,uuid,hashlib
from pathlib import Path

def save_atomic(path,payload):
    p=Path(path);p.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix=p.name+'.',dir=p.parent)
    try:
        with os.fdopen(fd,'w') as f:
            os.fchmod(f.fileno(),0o600)
            json.dump(payload,f,indent=2,sort_keys=True,ensure_ascii=False);f.flush();os.fsync(f.fileno())
        os.replace(tmp,p)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)

def load(path,default=None):
    p=Path(path)
    return json.loads(p.read_text()) if p.exists() else (default if default is not None else {})

class Journal:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,mode=0o700,exist_ok=True)
        self.path=self.root/'journal.jsonl'
    def event(self,name,task='',details=None):
        obj={'event_id':uuid.uuid4().hex,'time':time.time(),'event':name,'task':task,'details':details or {}}
        with self.path.open('a') as f:
            os.fchmod(f.fileno(),0o600)
            f.write(json.dumps(obj,ensure_ascii=False)+'\n');f.flush();os.fsync(f.fileno())
        return obj

def sha(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()
