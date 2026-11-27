"""Fresh, role-scoped Codex CLI sessions. No fallback model or hosted API charges."""
import json, os, subprocess, tempfile, time, uuid, re
from pathlib import Path
from . import MODEL, EFFORT
from .tools import ExternalFailure, TransientFailure, clean_env

class Codex:
    def __init__(self,binary='codex',model=MODEL,effort=EFFORT):
        if model!=MODEL or effort!=EFFORT:raise ValueError('Fixado gpt-6.2-sol/high')
        self.binary=binary;self.model=model;self.effort=effort
    def run(self,role,prompt,repo,schema,write=False,timeout=2400):
        repo=Path(repo).resolve();schema=Path(schema).resolve()
        started=time.time()
        runid=uuid.uuid4().hex[:14]
        log_root=Path.home()/'.local/share/gojob-runner/codex-logs'
        def record(status,stdout='',stderr='',code=None):
            from .state import save_atomic
            try:
                log_root.mkdir(parents=True,exist_ok=True,mode=0o700)
                def red(txt):
                    txt=str(txt)
                    txt=re.sub(r'(?i)(token|secret|password|api[_-]?key)[=:]\s*\S+',r'\1=[REDACTED]',txt)
                    txt=re.sub(r'gh[pousr]_[A-Za-z0-9_-]{12,}', '[REDACTED]',txt)
                    return txt[-2500:]
                save_atomic(log_root/(runid+'.json'),{'id':runid,'role':role,'status':status,
                    'started':started,'finished':time.time(),'duration_sec':round(time.time()-started,2),
                    'repository':str(repo),'exit_code':code,
                    'stdout_tail':red(stdout),'stderr_tail':red(stderr)})
            except Exception:pass # telemetry must not invalidate product work

        if not schema.is_file():raise ExternalFailure('Schema de agente ausente: '+schema.name)
        from .schema_protocol import prepared_schema, SchemaProtocolError
        with tempfile.TemporaryDirectory(prefix='gojob-codex-') as tmp:
            output=Path(tmp)/'last.json'
            try:cli_schema=prepared_schema(schema,tmp)
            except SchemaProtocolError as e:
                record('INVALID_SCHEMA',stderr=str(e))
                raise ExternalFailure('Schema Codex irrecuperável: '+str(e)) from e
            if cli_schema!=schema:record('AUTO_REPAIRED_SCHEMA',stderr=schema.name)
            command=[self.binary,'exec','--ephemeral','-C',str(repo),'--model',self.model,
               '--config',f'model_reasoning_effort={self.effort}',
               '--sandbox','workspace-write' if write else 'read-only',
               '--output-schema',str(cli_schema),'--output-last-message',str(output),prompt]
            try:
                run=subprocess.run(command,cwd=repo,env=clean_env(),text=True,capture_output=True,timeout=timeout)
            except FileNotFoundError as e:
                record('MISSING_CODEX',stderr='Codex CLI not in service PATH')
                raise ExternalFailure('Codex CLI ausente') from e
            except subprocess.TimeoutExpired as e:
                record('TIMEOUT',stdout=e.stdout or '',stderr=e.stderr or '')
                raise TransientFailure('Codex timeout; verificar alterações antes de retry') from e
            record('FINISHED' if run.returncode==0 else 'FAILED',run.stdout,run.stderr,run.returncode)
            if run.returncode:
                tail=(run.stderr or run.stdout)[-800:]
                if any(x in tail.lower() for x in ('quota','usage limit','rate limit','429','temporar')):
                    raise TransientFailure('Codex indisponível/cota: '+tail)
                if 'invalid_json_schema' in tail or 'text.format.schema' in tail:
                    raise ExternalFailure('Protocolo JSON Codex rejeitado após normalização: '+tail)
                raise ExternalFailure('Codex falhou: '+tail)
            if not output.is_file():raise ExternalFailure('Codex não produziu resposta')
            try:answer=json.loads(output.read_text(encoding='utf-8'))
            except (UnicodeError,json.JSONDecodeError) as e:raise ExternalFailure('Resposta Codex não é JSON válido') from e
            if not isinstance(answer,dict):raise ExternalFailure('Resposta Codex inválida')
            return answer
