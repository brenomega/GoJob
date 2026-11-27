"""Reconcile interrupted release commits against immutable path and blob intent."""
from __future__ import annotations

import hashlib
import os
import tempfile
from pathlib import Path

from gojob_runner.tools import apply_commit_plan, git, validate_commit_plan, validate_path, worktree_changes
from release_package import git_bytes, verify_package


def tree_entries(repo, tree):
    entries = {}
    for record in git_bytes(repo, 'ls-tree', '-r', '-z', tree).split(b'\0'):
        if record:
            header, path = record.split(b'\t', 1)
            mode, _, oid = header.decode().split()
            entries[os.fsdecode(path)] = [mode, oid]
    return entries


def blob_entry(content, mode):
    oid = hashlib.sha1(f'blob {len(content)}\0'.encode() + content).hexdigest()
    return [mode, oid]


def regular_path(repo, path):
    validate_path(repo, path)
    full = repo/path
    if full.resolve() != full or (full.exists() and not full.is_file()):
        raise ValueError(f'Caminho de release não regular: {path}')
    return full


def file_entry(repo, path):
    full = regular_path(repo, path)
    if full.is_file():
        return blob_entry(full.read_bytes(), '100755' if full.stat().st_mode & 0o100 else '100644')
    return None


def repair_intent(repo, head, eap, report, **scope):
    staged, dirty = worktree_changes(repo)
    if staged or git(repo, 'rev-parse', 'HEAD') != head:
        raise ValueError('Índice ou HEAD mudou antes do plano de correção; preservar worktree')
    plan = validate_commit_plan(repo, eap, report, dirty, **scope)
    return {'source': head, 'plan': plan, 'entries': {path: file_entry(repo, path) for path in dirty}}


def package_intent(repo, base, package, eap):
    if any(worktree_changes(repo)) or git(repo, 'rev-parse', 'HEAD') != base:
        raise ValueError('Preparação sem intenção anterior exige worktree limpa na BASE; preservar')
    manifest = verify_package(package)
    previous = tree_entries(repo, base)
    entries = {}
    for path in sorted(manifest['files']):
        observed = previous.get(path)
        if observed and observed[0] not in ('100644', '100755'):
            raise ValueError(f'Caminho anterior da distribuição não regular: {path}')
        expected = blob_entry((package/path).read_bytes(), observed[0] if observed else '100644')
        if expected != observed:
            entries[path] = expected
    plan = [{'message': f'chore(runner): publique arquivos da release [#{eap}]', 'paths': list(entries)}]
    if entries:
        validate_commit_plan(repo, eap, {'status': 'DONE', 'commit_plan': plan}, entries)
    return {'source': base, 'plan': plan if entries else [], 'entries': entries, 'manifest': manifest}


def verify_prefix(repo, intent, head):
    parent = intent['source']
    commits = git(repo, 'rev-list', '--first-parent', '--reverse', f'{parent}..{head}').splitlines()
    plan = intent['plan']
    if len(commits) > len(plan):
        raise ValueError('Histórico excede o plano de release; preservar')
    for commit, piece in zip(commits, plan):
        paths = git(repo, 'diff', '--name-only', '--no-renames', '-z', parent, commit).split('\0')
        entries = tree_entries(repo, commit)
        if (git(repo, 'show', '-s', '--format=%P', commit) != parent
            or git(repo, 'show', '-s', '--format=%B', commit) != piece['message']
            or set(filter(None, paths)) != set(piece['paths'])
            or any(entries.get(path) != intent['entries'][path] for path in piece['paths'])):
            raise ValueError('Commit local diverge da intenção de release; preservar')
        parent = commit
    if parent != head:
        raise ValueError('HEAD local diverge da intenção de release; preservar')
    return plan[len(commits):]


def copy_package_file(repo, package, path, expected):
    target = regular_path(repo, path)
    content = (package/path).read_bytes()
    if blob_entry(content, expected[0]) != expected:
        raise ValueError(f'Arquivo de origem diverge da intenção de release: {path}')
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=repo.parent, prefix='.release-copy-', delete=False) as stream:
        temporary = Path(stream.name)
        try:
            os.fchmod(stream.fileno(), 0o755 if expected[0]=='100755' else 0o644)
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)


def verify_index(repo, paths, expected):
    index = {}
    records = git_bytes(repo, '--literal-pathspecs', 'ls-files', '--stage', '-z', '--', *paths)
    for record in records.split(b'\0'):
        if record:
            header, path = record.split(b'\t', 1)
            mode, oid, stage = header.decode().split()
            if stage != '0':
                raise ValueError('Conflito no índice da release; preservar')
            index[os.fsdecode(path)] = [mode, oid]
    if any(index.get(path) != expected[path] for path in paths):
        raise ValueError('Blob staged diverge da intenção de release; preservar')


def resume_commits(repo, eap, intent, *, package=None, **scope):
    repo = Path(repo).resolve()
    head = git(repo, 'rev-parse', 'HEAD')
    pending = verify_prefix(repo, intent, head)
    expected = {path: intent['entries'][path] for piece in pending for path in piece['paths']}
    staged, dirty = worktree_changes(repo)
    if not set(staged+dirty).issubset(expected):
        raise ValueError('Alterações fora da intenção de release; preservar worktree')
    if staged:
        if set(staged) != set(pending[0]['paths']):
            raise ValueError('Índice diverge do próximo commit da release; preservar')
        verify_index(repo, staged, expected)
    if package is not None:
        verify_package(package, intent['manifest'])
    previous = tree_entries(repo, head) if package is not None else {}
    missing = []
    for path, entry in expected.items():
        observed = file_entry(repo, path)
        if observed != entry:
            if package is None or path in staged or observed != previous.get(path):
                raise ValueError(f'Conteúdo local diverge da intenção de release; preservar: {path}')
            missing.append(path)
    for path in missing:
        copy_package_file(repo, package, path, expected[path])
    if pending:
        apply_commit_plan(repo, eap, {'status': 'DONE', 'commit_plan': pending}, verified_index=staged,
                          verify_staged=lambda paths: verify_index(repo, paths, expected), **scope)
        head = git(repo, 'rev-parse', 'HEAD')
        if verify_prefix(repo, intent, head):
            raise ValueError('Plano de release ficou incompleto')
    return head
