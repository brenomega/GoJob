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
from release_worker import install_release_worker


def enable_automation(repo, *, merge, home=None, units=None):
    return activate(repo, merge, home=home, units=units)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    parser.add_argument('--source-sha', required=True)
    parser.add_argument('--enable', action='store_true')
    args = parser.parse_args()
    print(json.dumps(install_release_worker(args.repo, args.source_sha, enable=args.enable)))


if __name__ == '__main__':
    main()
