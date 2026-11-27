"""Fresh, role-scoped Codex CLI sessions. No fallback model or hosted API charges."""
import json, os, subprocess, tempfile
from pathlib import Path
from . import MODEL, EFFORT
from .tools import ExternalFailure, TransientFailure, clean_env

class Codex:
    def __init__(self,binary='codex',model=MODEL,effort=EFFORT):
        if model!=MODEL or effort!=EFFORT:raise ValueError('Fixado gpt-6.2-sol/high')
        self.binary=binary;self.model=model;self.effort=effort
    def run(self,role,prompt,repo,schema,write=False,timeout=2400):
        repo=Path(repo).resolve();schema=Path(schema).resolve()
        if not schema.is_file():raise ExternalFailure('Schema de agente ausente: '+schema.name)
        with tempfile.TemporaryDirectory(prefix='gojob-codex-') as tmp:
            output=Path(tmp)/'last.json'
            command=[self.binary,'exec','--ephemeral','-C',str(repo),'--model',self.model,
               '--config',f'model_reasoning_effort={self.effort}',
               '--sandbox','workspace-write' if write else 'read-only',
               '--output-schema',str(schema),'--output-last-message',str(output),prompt]
            try:
                run=subprocess.run(command,cwd=repo,env=clean_env(),text=True,capture_output=True,timeout=timeout)
            except FileNotFoundError as e:raise ExternalFailure('Codex CLI ausente') from e
            except subprocess.TimeoutExpired as e:raise TransientFailure('Codex timeout; verificar alterações antes de retry') from e
            if run.returncode:
                tail=(run.stderr or run.stdout)[-800:]
                if any(x in tail.lower() for x in ('quota','usage limit','rate limit','429','temporar')):
                    raise TransientFailure('Codex indisponível/cota: '+tail)
                raise ExternalFailure('Codex falhou: '+tail)
            if not output.is_file():raise ExternalFailure('Codex não produziu resposta')
            try:answer=json.loads(output.read_text(encoding='utf-8'))
            except (UnicodeError,json.JSONDecodeError) as e:raise ExternalFailure('Resposta Codex não é JSON válido') from e
            if not isinstance(answer,dict):raise ExternalFailure('Resposta Codex inválida')
            return answer
