#!/usr/bin/env python3
"""Stage the independent candidate worker or activate the approved runner merge."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

if __name__ == '__main__':
    sys.path[:0] = map(str, Path(__file__).resolve().parents[:2])

from install_supervisor import activate
from release_control import ReleaseLedger
from release_worker import VERSION, install_release_worker


def enable_automation(repo, *, merge, home=None, units=None):
    return activate(repo, merge, home=home, units=units)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo')
    parser.add_argument('--source-sha')
    parser.add_argument('--home', type=Path)
    parser.add_argument('--units', type=Path)
    parser.add_argument('--status', action='store_true')
    parser.add_argument('--stage-only', action='store_true', help='Grava o pacote e as unidades sem iniciar o systemd')
    args = parser.parse_args()
    if args.status:
        print(json.dumps(ReleaseLedger(VERSION, args.home).status(), ensure_ascii=False))
        return
    if not args.repo or not args.source_sha:
        parser.error('--repo e --source-sha são obrigatórios para instalar')
    print(json.dumps(install_release_worker(args.repo, args.source_sha,
                     enable=not args.stage_only, home=args.home, units=args.units), ensure_ascii=False))


if __name__ == '__main__':
    main()
