"""Build and activate complete runner generations from immutable Git objects."""
from __future__ import annotations

import ctypes
import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

from gojob_runner.merge_evidence import OID
from release_control import atomic_json

PREFIXES = ('automation/gojob_runner/', 'automation/supervisor/', 'automation/tests/')
FILES = frozenset({'RUNNER_VERSION', 'CHANGELOG_RUNNER.md',
                   'docs/RUNNER_OPERATING_CONTRACT.md', 'docs/RELEASE_ARCHITECTURE.md'})
ENTRY_POINTS = ('stable_supervisor.py', 'rescue_watchdog.py', 'release_worker.py',
                'upgrade_runner.py', 'install_supervisor.py', 'quickstart.py', 'watchdog_launcher.py')


def package_path(path):
    path = PurePosixPath(path)
    return (not path.is_absolute() and '..' not in path.parts
            and (str(path) in FILES or str(path).startswith(PREFIXES))
            and '__pycache__' not in path.parts and path.suffix != '.pyc')


def git_bytes(repo, *args):
    return subprocess.check_output(['git', *args], cwd=repo, timeout=120, stderr=subprocess.PIPE)


def package_files(repo, sha):
    if not OID.fullmatch(sha or '') or git_bytes(repo, 'cat-file', '-t', sha).strip() != b'commit':
        raise ValueError('Pacote exige SHA completo de um commit')
    files = {}
    for row in git_bytes(repo, 'ls-tree', '-r', '-z', sha).split(b'\0'):
        if not row:
            continue
        header, raw_path = row.split(b'\t', 1)
        mode, kind, oid = header.decode().split()
        relative = raw_path.decode()
        if package_path(relative):
            if kind != 'blob' or mode not in ('100644', '100755'):
                raise ValueError('Arquivo não regular no pacote: ' + relative)
            files[relative] = git_bytes(repo, 'cat-file', 'blob', oid)
    required = {'RUNNER_VERSION', 'automation/gojob_runner/runner.py',
                *(f'automation/supervisor/{entry}' for entry in ENTRY_POINTS)}
    if not required.issubset(files):
        raise ValueError('Pacote incompleto: ' + ', '.join(sorted(required-files.keys())))
    return files


def canary(root):
    for path in root.glob('automation/**/*.py'):
        compile(path.read_bytes(), str(path), 'exec')
    for entry in ENTRY_POINTS:
        subprocess.run([sys.executable, '-I', '-B', str(root/'automation/supervisor'/entry), '--help'],
                       cwd=root, capture_output=True, check=True, timeout=20)


def build_package(repo, sha, root):
    files = package_files(repo, sha)
    manifest = {'commit': sha, 'version': files['RUNNER_VERSION'].decode().strip(),
                'files': {path: hashlib.sha256(content).hexdigest() for path, content in files.items()}}
    root = Path(root)
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    destination = root/sha
    if not destination.exists():
        with tempfile.TemporaryDirectory(prefix='.generation-', dir=root) as temporary:
            stage = Path(temporary)/'complete'
            for path, content in files.items():
                target = stage/path
                target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                target.write_bytes(content)
            atomic_json(stage/'manifest.json', manifest)
            canary(stage)
            try:
                os.rename(stage, destination)
            except OSError:
                if not destination.is_dir():
                    raise
    verify_package(destination, manifest)
    return destination


def verify_package(root, manifest=None):
    root = Path(root)
    stored = json.loads((root/'manifest.json').read_text())
    actual = {path.relative_to(root).as_posix() for path in root.rglob('*')
              if path.is_file() or path.is_symlink()}
    if (root.is_symlink() or (manifest is not None and stored != manifest)
        or not OID.fullmatch(stored.get('commit', '')) or stored['commit'] != root.name
        or actual != set(stored['files']) | {'manifest.json'}
        or any((root/path).is_symlink() or not package_path(path)
               or hashlib.sha256((root/path).read_bytes()).hexdigest() != digest
               for path, digest in stored['files'].items())):
        raise ValueError('Manifesto ou conteúdo do pacote diverge')
    return stored


def supervisor_dir(generation):
    generation = Path(generation).resolve()
    packaged = generation/'automation/supervisor'
    return packaged if packaged.is_dir() else generation


def replace_link(link, target):
    with tempfile.TemporaryDirectory(prefix='.pointer-', dir=link.parent) as temporary:
        pending = Path(temporary)/'next'
        pending.symlink_to(target, target_is_directory=True)
        os.replace(pending, link)


def _exchange(left, right):
    # Existing deployments point their units directly at a real stable directory.
    # Linux renameat2 converts that layout without a window with no stable entry.
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.renameat2(-100, os.fsencode(left), -100, os.fsencode(right), 2):
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))


def activate_package(home, generation):
    home = Path(home)
    active = home/'stable'
    previous = home/'stable.previous'
    source = supervisor_dir(generation)
    if active.resolve() == source:
        return False
    if active.is_symlink():
        replace_link(previous, active.resolve())
        replace_link(active, source)
    elif active.is_dir():
        archive = home/'supervisor-generations'
        archive.mkdir(mode=0o700, exist_ok=True)
        backup = Path(tempfile.mkdtemp(prefix='legacy-', dir=archive))/'stable'
        shutil.copytree(active, backup, symlinks=True)
        replace_link(previous, backup)
        with tempfile.TemporaryDirectory(prefix='.legacy-exchange-', dir=home) as temporary:
            pending = Path(temporary)/'next'
            pending.symlink_to(source, target_is_directory=True)
            _exchange(active, pending)
    else:
        replace_link(active, source)
    return True


def restore_previous(home):
    home = Path(home)
    previous = home/'stable.previous'
    if not previous.is_symlink() or not previous.resolve().is_dir():
        raise ValueError('Geração anterior indisponível')
    replace_link(home/'stable', previous.resolve())
