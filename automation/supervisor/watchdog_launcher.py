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
        if not script.is_file():
            continue
        argv = [sys.executable, '-I', '-B', str(script)]
        probe = subprocess.run([*argv, '--help'], capture_output=True, timeout=20)
        if not probe.returncode:
            os.execv(sys.executable, [*argv, '--repo', args.repo, '--home', str(args.home)])
    raise RuntimeError('Nenhuma geração aprovada do watchdog passou no canário')


if __name__ == '__main__':
    main()
