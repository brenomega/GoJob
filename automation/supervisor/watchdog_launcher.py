#!/usr/bin/env python3
"""Run the watchdog from the latest importable approved generation."""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--home', type=Path, required=True)
    args = parser.parse_args()
    installed = Path(__file__).resolve().parent
    candidates = (args.home/'stable', args.home/'stable.previous', installed)
    for folder in dict.fromkeys(path.resolve() for path in candidates):
        script = folder/'rescue_watchdog.py'
        generation = folder.parents[1]
        if not script.is_file() or not (generation/'manifest.json').is_file():
            continue
        argv = [sys.executable, '-I', '-B', str(script)]
        probe_generation = (
            'import runpy, sys; from pathlib import Path; '
            'script=Path(sys.argv[1]); folder=script.parent; '
            'sys.path[:0]=[str(folder), str(folder.parent)]; '
            'from release_package import verify_package; verify_package(folder.parents[1]); '
            'sys.argv=[str(script), "--help"]; runpy.run_path(str(script), run_name="__main__")'
        )
        try:
            probe = subprocess.run([*argv[:3], '-c', probe_generation, str(script)],
                                   capture_output=True, timeout=20)
        except subprocess.TimeoutExpired:
            continue
        if not probe.returncode:
            os.execv(sys.executable, [*argv, '--repo', args.repo, '--home', str(args.home)])
    raise RuntimeError('Nenhuma geração aprovada do watchdog passou no canário')


if __name__ == '__main__':
    main()
