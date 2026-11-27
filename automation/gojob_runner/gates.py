"""Fail-closed, stdlib-only checks on the *runner*, not the job application."""
import json, re, sys, unittest
from pathlib import Path
from . import VERSION, MODEL, EFFORT
from .versioning import bump
from .schema_protocol import source_schema_errors

def check(root=None):
    root=Path(root or Path(__file__).resolve().parents[1])
    if not (root/'RUNNER_VERSION').exists() and (root.parent/'RUNNER_VERSION').exists():root=root.parent
    if MODEL != 'gpt-6.2-sol' or EFFORT != 'high':
        raise RuntimeError('Codex deve usar gpt-6.2-sol/high')
    if (root/'RUNNER_VERSION').read_text(encoding='utf-8').strip()!=VERSION:
        raise RuntimeError('RUNNER_VERSION divergente da implementação')
    if (root/'gojob_runner'/'__init__.py').exists():
        agent=(root/'gojob_runner'/'codex.py').read_text()
    else:
        agent=(root/'automation'/'gojob_runner'/'codex.py').read_text()
    if 'danger-full-access' in agent or '--yolo' in agent:
        raise RuntimeError('Política de sandbox insegura')
    if bump('3.0.0','bug')!='3.0.1' or bump('3.0.1','procedural')!='3.1.0' or bump('3.1.1','feature')!='4.0.0':
        raise RuntimeError('Versionamento alterado')
    schema=root/'gojob_runner'/'schemas'/'test_engineer.json'
    if not schema.exists():schema=root/'automation'/'gojob_runner'/'schemas'/'test_engineer.json'
    if not schema.exists():raise RuntimeError('Delegação do Test Engineer ausente')
    definition=json.loads(schema.read_text(encoding='utf-8'))
    if 'failure_modes' not in definition.get('required',[]):
        raise RuntimeError('Oráculos independentes do Test Engineer não exigidos')
    schema_folder=schema.parent
    all_schemas=list(schema_folder.glob('*.json'))
    if not all_schemas:raise RuntimeError('Schemas de agentes ausentes')
    for source in all_schemas:
        problems=source_schema_errors(source)
        if problems:raise RuntimeError('Contrato schema Codex inválido: '+'; '.join(problems[:3]))
    tasks=root/'product_seed'/'docs'/'BACKLOG.json'
    if tasks.exists():
        entries=json.loads(tasks.read_text(encoding='utf-8'))['tasks']
        if not entries or len({x['id'] for x in entries})!=len(entries):
            raise RuntimeError('Backlog inválido')
    return True

def main():
    check();print('Runner gates: OK')

if __name__=='__main__':main()
