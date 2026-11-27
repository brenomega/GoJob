"""Immutable evidence for the GitHub Actions and merge gates."""
from __future__ import annotations

import re

WORKFLOW = '.github/workflows/ci.yml'
WORKFLOW_NAME = 'gojob-verify'
JOB = 'verify'
OID = re.compile(r'[0-9a-f]{40}')


def verify_ci(api, pr, head, base):
    """Return the latest attempt for this PR snapshot from the trusted workflow."""
    runs = api(f'actions/workflows/ci.yml/runs?event=pull_request&head_sha={head}&per_page=100')
    def belongs_to_snapshot(run):
        return (run.get('head_sha') == head and run.get('event') == 'pull_request'
                and (run.get('path') or '').partition('@')[0] == WORKFLOW
                and run.get('name') == WORKFLOW_NAME
                and any(row.get('number') == pr and row.get('head', {}).get('sha') == head
                        and row.get('base', {}).get('sha') == base
                        and row.get('base', {}).get('ref') == 'develop'
                        for row in run.get('pull_requests', [])))

    candidates = [run for run in runs.get('workflow_runs', []) if belongs_to_snapshot(run)]
    if not candidates:
        return None
    run = max(candidates, key=lambda row: row['id'])
    if run.get('status') != 'completed':
        return None
    if run.get('conclusion') != 'success':
        raise ValueError(f'{WORKFLOW_NAME} rejeitou o snapshot em CI')
    rid, attempt = run['id'], run['run_attempt']
    jobs = api(f'actions/runs/{rid}/attempts/{attempt}/jobs').get('jobs', [])
    matching = [job for job in jobs if job.get('name') == JOB and job.get('run_id') == rid]
    if len(matching) != 1:
        raise ValueError(f'{WORKFLOW_NAME} terminou sem um job {JOB} inequívoco')
    job = matching[0]
    if job.get('status') != 'completed' or job.get('conclusion') != 'success':
        raise ValueError(f'CI {JOB} não aprovou HEAD/BASE')
    url = job.get('check_run_url') or ''
    match = re.search(r'/check-runs/([0-9]+)$', url)
    if not match:
        raise ValueError(f'CI {JOB} sem identidade verificável do check')
    check = api(f'check-runs/{match[1]}')
    app = check.get('app') or {}
    if (app.get('slug') != 'github-actions' or not isinstance(app.get('id'), int)
        or check.get('id') != int(match[1]) or check.get('name') != JOB
        or check.get('head_sha') != head or check.get('conclusion') != 'success'):
        raise ValueError(f'Check {JOB} de origem ou SHA divergente')
    return {'pr': pr, 'head': head, 'base': base, 'workflow': WORKFLOW,
            'run_id': rid, 'run_attempt': attempt, 'job_id': job['id'], 'app_id': app['id']}


def require_strict_protection(api, proof):
    """Check existing server enforcement without changing repository settings."""
    settings = api('branches/develop/protection')
    required = settings.get('required_status_checks') or {}
    if (required.get('strict') is not True
        or (settings.get('enforce_admins') or {}).get('enabled') is not True
        or not any(check.get('context') == JOB and check.get('app_id') == proof['app_id']
                   for check in required.get('checks', []))):
        raise ValueError('develop exige proteção estrita do check verify do GitHub Actions, inclusive administradores')


def reconcile_merge(git, pr_info, *, head, base, branch):
    """Prove the reviewed pair was integrated, even if develop advanced later."""
    merged = (pr_info.get('mergeCommit') or {}).get('oid')
    if (pr_info.get('state') != 'MERGED' or not OID.fullmatch(merged or '')
        or pr_info.get('headRefOid') != head or pr_info.get('headRefName') != branch
        or pr_info.get('baseRefName') != 'develop'):
        raise ValueError('Resultado de merge sem identidade do PR revisado')
    git('fetch', 'origin', 'develop')
    if git('show', '-s', '--format=%P', merged).split() != [base, head]:
        raise ValueError('Pais do merge diferem de BASE e HEAD revisados')
    if git('merge-base', merged, 'origin/develop') != merged:
        raise ValueError('Merge confirmado pelo PR ainda não alcançável em develop')
    return merged
