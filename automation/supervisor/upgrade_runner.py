#!/usr/bin/env python3
"""Host controller for the runner release PR and its durable handoff."""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

if __name__ == '__main__':
    sys.path[:0] = map(str, Path(__file__).resolve().parents[:2])

from gojob_runner.agent_privacy import redact
from gojob_runner.merge_evidence import verify_ci
from gojob_runner.review_policy import HUMAN_KINDS, MAX_REVIEW_ROUNDS, collect_reviews, disposition, review_feedback
from gojob_runner.tools import (
    ExternalFailure, GitHub, TransientFailure, VerifiedCIFailure, command, git, validate_path, worktree_changes,
)
from release_agent_recovery import call_codex, recover_implementer
from release_commits import package_intent, repair_intent, resume_commits
from release_control import ReleaseLedger, atomic_json
from release_package import package_path

ROOT = Path(__file__).resolve().parents[2]
VERSION = (ROOT/'RUNNER_VERSION').read_text().strip()
EAP = '0.11.0'
BRANCH = f'fix/{EAP}-runner-v{VERSION}-execucao-duravel'
ISSUE_TITLE = f'[RUNNER {VERSION}] Retome releases interrompidas e reduza retrabalho'
SOURCE_PREFIXES = ('automation/gojob_runner/', 'automation/tests/', 'automation/supervisor/')
REPAIR_FILES = {'RUNNER_VERSION', 'CHANGELOG_RUNNER.md'}


def release_scope(path):
    return path in REPAIR_FILES or path.startswith(SOURCE_PREFIXES)



def verify_candidate_scope(repo, base, head, allowed):
    paths = git(repo, 'diff', '--name-only', '--no-renames', '-z', base, head).split('\0')
    for path in filter(None, paths):
        validate_path(repo, path)
        if not allowed(path):
            raise ValueError('Candidato de release tem alterações fora do escopo')

def load(path, default):
    return json.loads(path.read_text()) if path.exists() else default


def gates(repo):
    env = {**os.environ, 'PYTHONPATH': os.pathsep.join(map(str, (
        repo/'automation', repo/'automation/supervisor')))}
    for args in (('-m', 'unittest', 'discover', '-s', 'tests'),
                 ('-m', 'unittest', 'discover', '-s', 'automation/tests'),
                 ('-m', 'gojob_runner.gates')):
        command([sys.executable, '-B', *args], cwd=repo, env=env, timeout=600)


def validate_review(report, role, head, base):
    required = (('opportunity_cost', 'alternative', 'north_star_impact') if role == 'strategic'
                else ('real_evidence', 'tests_assessed'))
    decisions = ('PASS', 'REVISE', 'STOP') if role == 'strategic' else ('ACCEPT', 'REVISE', 'BLOCK')
    if ((report.get('head_sha'), report.get('base_sha')) != (head, base)
        or report.get('decision') not in decisions
        or not isinstance(report.get('findings'), list)
        or len(str(report.get('rationale') or '').strip()) < 12
        or any(not report.get(field) for field in required)):
        raise ValueError(f'Parecer {role} incompleto ou desvinculado do snapshot')


def validate_reviews(row, head, base, pr):
    if (row.get('head'), row.get('base'), row.get('pr', pr)) != (head, base, pr):
        raise ValueError('Parecer de release para outro PR, HEAD ou BASE')
    for role in ('strategic', 'technical'):
        validate_review(row[role], role, head, base)
    return disposition(row['strategic'], row['technical'])


def prior_candidate(repo):
    ref = 'refs/remotes/origin/fix/0.10.0-runner-v4.0.0-release-resiliente'
    if not git(repo, 'for-each-ref', '--format=%(objectname)', ref):
        return 'PR #13 sem referência local disponível'
    sha = git(repo, 'rev-parse', ref)
    point = git(repo, 'merge-base', sha, 'origin/develop')
    commits = git(repo, 'log', '--format=%h %s', f'{point}..{sha}')
    paths = git(repo, 'diff', '--name-only', '--no-renames', point, sha, '--', 'automation/')
    return redact(f'PR #13, candidato não aprovado, SHA {sha}.\n{commits}\n{paths}')[:6500]


def implement(repo, row, ledger):
    head, base, number = row['head'], row['base'], row['round']
    ci_failure = row.get('ci_failure')
    name = f'{number}-{head}' if not ci_failure else f"ci-{row['pr']}-{base}-{head}"
    journal = ledger.root/'implementation'/f'{name}.json'
    attempt = load(journal, {})
    remaining = 8 - int(git(repo, 'rev-list', '--count', f'{base}..{head}'))
    scope = {'allowed_prefixes': SOURCE_PREFIXES, 'allowed_files': REPAIR_FILES, 'max_commits': remaining}
    if attempt.get('commit_intent'):
        corrected = resume_commits(repo, EAP, attempt['commit_intent'], **scope)
        if not ci_failure:
            gates(repo)
        return corrected
    staged, dirty = worktree_changes(repo)
    if (staged or git(repo, 'rev-parse', 'HEAD') != head
        or any(not release_scope(path) for path in dirty)):
        raise ValueError('HEAD, índice ou diff fora do escopo do reparo; preservar worktree')
    for path in dirty:
        validate_path(repo, path)
    if remaining < 1:
        ledger.emit('NEEDS_USER', 'commit_budget_exhausted', head=head, base=base)
        raise ValueError('Limite de commits da release atingido')
    schema = repo/'automation/gojob_runner/schemas/developer.json'
    feedback = (row.get('previous_reviews', {}) if ci_failure else
                review_feedback(row['strategic'], row['technical']))
    # A crash or a failed transport consumes the primary call for this round.
    # Subsequent passes use the independent recovery budget.
    report = attempt.get('report')
    if not attempt and not ci_failure:
        atomic_json(journal, {'state': 'STARTED'})
        prompt = (
            f'PAPEL INDEPENDENT RELEASE IMPLEMENTER. EAP #{EAP}, HEAD {head}, BASE {base}. '
            'Leia os contratos do runner e o diff. Corrija apenas código e testes do runner. '
            'Preserve alterações interrompidas e decisões do usuário. Não execute comandos Git de escrita, '
            'gh ou systemctl. Devolva plano exato de commits em português. '
            f'Orçamento restante: {remaining} commits. Preferir um commit corretivo. '
            f'Pareceres redigidos: {json.dumps(feedback, ensure_ascii=False)}. '
            f'Evidência anterior: {prior_candidate(repo)}'
        )
        try:
            report = call_codex(repo, schema, prompt, True, 2400, label='release-implementer', ledger=ledger)
        except Exception as exc:
            report = {'status': 'BLOCKED', 'summary': redact(exc)}
        saved = {'status': report['status'], 'summary': redact(report.get('summary', ''))}
        if report['status'] == 'DONE':
            saved['commit_plan'] = report.get('commit_plan')
        atomic_json(journal, {'state': 'REPORTED', 'report': saved})
    if not report or report.get('status') != 'DONE':
        report = recover_implementer(repo, base, head, feedback, report, schema=schema,
                                     ledger=ledger, eap=EAP, round_number=number, ci_failure=ci_failure,
                                     remaining_commits=remaining)
        report = {'status': report['status'], 'commit_plan': report.get('commit_plan')}
        atomic_json(journal, {'state': 'REPORTED', 'report': report})
    intent = repair_intent(repo, head, EAP, report, **scope)
    if ci_failure:
        try:
            gates(repo)
        except TransientFailure:
            raise
        except ExternalFailure as exc:
            atomic_json(journal, {'state': 'REPORTED', 'report': {'status': 'BLOCKED', 'summary': redact(exc)}})
            ledger.emit('REPAIRING', 'ci_repair_gate_failure', head=head, base=base, cause=exc)
            raise
    atomic_json(journal, {'state': 'COMMITTING', 'commit_intent': intent})
    corrected = resume_commits(repo, EAP, intent, **scope)
    if not ci_failure:
        gates(repo)
    return corrected


def resume_correction(repo, scm, pr, base, state, path, ledger, *, recheck_ci=False):
    row = state['active_correction']
    if (row['pr'], row['base']) != (pr, base):
        raise ValueError('Correção pendente pertence a outro PR ou BASE')
    if 'to' not in row:
        scm.snapshot(pr, BRANCH, row['head'], base)
        if (recheck_ci and row.get('ci_failure') and git(repo, 'rev-parse', 'HEAD') == row['head']
            and not any(worktree_changes(repo))):
            try:
                scm.checks(pr, row['head'], base, BRANCH)
            except VerifiedCIFailure:
                pass
            else:
                state.setdefault('superseded_ci_failures', []).append(state.pop('active_correction'))
                atomic_json(path, state)
                return
        corrected = implement(repo, row, ledger)
        verify_candidate_scope(repo, row['head'], corrected, release_scope)
        row['to'] = corrected
        atomic_json(path, state)
    elif git(repo, 'rev-parse', 'HEAD') != row['to'] or any(worktree_changes(repo)):
        raise ValueError('Worktree diverge da correção pronta para publicação; preservar')
    git(repo, 'push', 'origin', BRANCH, timeout=240)
    state.setdefault('corrections', []).append(state.pop('active_correction'))
    pending = state.get('pending') or {}
    if (pending.get('pr'), pending.get('head'), pending.get('base')) == (pr, row['head'], base):
        state.setdefault('superseded_reviews', []).append(state.pop('pending'))
    atomic_json(path, state)


def check_review_veto(row, head, base, pr, ledger):
    if not row:
        return
    for role in ('strategic', 'technical'):
        report = row.get(role)
        if report and report.get('blocking_kind') in HUMAN_KINDS:
            validate_review(report, role, head, base)
            ledger.emit('NEEDS_USER', 'release_review_blocked', pr=pr, head=head, base=base,
                        reason=report.get('rationale'))
            raise ValueError('Parecer exige decisão humana ou resolução externa')


def verify_reviewed_transition(repo, state, last, head, base, pr):
    old = last['head']
    verdict = validate_reviews(last, old, base, pr)
    corrected = old
    for row in state.get('corrections', []):
        if (row['pr'], row['base'], row['head']) == (pr, base, corrected):
            corrected = row['to']
    # Older journals can contain an already-published correction without a
    # commit intent. New intents must account for the entire transition.
    authorized = verdict.action == 'REPAIR' if corrected == old else corrected == head
    if (verdict.action == 'NEEDS_USER' or not authorized
        or git(repo, 'merge-base', old, head) != old):
        raise ValueError('HEAD divergente de um reparo autorizado')
    verify_candidate_scope(repo, old, head, release_scope)


def review_release(repo, scm, pr, base, state, path, ledger):
    while True:
        if state.get('active_correction'):
            resume_correction(repo, scm, pr, base, state, path, ledger)
        head = git(repo, 'rev-parse', 'HEAD')
        last = state['rounds'][-1] if state['rounds'] else None
        if last and last['head'] != head:
            verify_reviewed_transition(repo, state, last, head, base, pr)
            last['repair'] = {'from': last['head'], 'to': head}
            atomic_json(path, state)
        if last and last['head'] == head:
            verdict = validate_reviews(last, head, base, pr)
            if verdict.action == 'NEEDS_USER':
                ledger.emit('NEEDS_USER', 'release_review_blocked', pr=pr, head=head, base=base, reason=verdict.reason)
                raise ValueError(verdict.reason)
        pending = state.get('pending') or {}
        if (pending.get('pr'), pending.get('head'), pending.get('base')) == (pr, head, base):
            check_review_veto(pending, head, base, pr, ledger)
        try:
            scm.checks(pr, head, base, BRANCH)
        except VerifiedCIFailure as failure:
            if len(state['rounds']) >= MAX_REVIEW_ROUNDS:
                ledger.emit('NEEDS_USER', 'review_budget_exhausted', pr=pr, head=head, base=base)
                raise
            if any(worktree_changes(repo)):
                raise ValueError('CI falhou com diff local fora de uma correção registrada; preservar') from failure
            state['active_correction'] = {'pr': pr, 'head': head, 'base': base, 'round': len(state['rounds'])+1,
                                          'ci_failure': failure.evidence,
                                          'previous_reviews': review_feedback(last['strategic'], last['technical']) if last else {}}
            atomic_json(path, state)
            continue
        if last and last['head'] == head:
            row = last
        else:
            if len(state['rounds']) >= MAX_REVIEW_ROUNDS:
                ledger.emit('NEEDS_USER', 'review_budget_exhausted', pr=pr, head=head, base=base)
                raise ValueError('Três rodadas completas sem revisão aplicável')
            key = {'pr': pr, 'head': head, 'base': base, 'round': len(state['rounds'])+1}
            row = state.get('pending') or {}
            if any(row.get(field) != value for field, value in key.items()):
                row = key
            prior = prior_candidate(repo)
            feedback = review_feedback(last['strategic'], last['technical']) if last else {}

            def invoke(role):
                schema = repo/f'automation/gojob_runner/schemas/{role}.json'
                prompt = (
                    f'PAPEL {role.upper()} REVIEWER independente. PR #{pr}, HEAD {head}, BASE {base}. '
                    'Leia AGENTS.md e os contratos do runner. Examine diff real, evidências e testes. '
                    'Descreva achados verificáveis, custos e obrigações não delegáveis. '
                    'Verifique achados anteriores e fundamente novas regressões. '
                    'Use FIXABLE para objeções corrigíveis. Sessão somente leitura, sem GitHub writes. '
                    f'Evidência anterior: {prior}. Pareceres anteriores: {json.dumps(feedback, ensure_ascii=False)}'
                )
                return call_codex(repo, schema, prompt, False, 2200, label='release-reviewer-'+role, ledger=ledger)

            def save(partial):
                state['pending'] = partial
                atomic_json(path, state)

            collect_reviews(row, invoke, lambda report, role: validate_review(report, role, head, base),
                            save, parallel=True)
            state['rounds'].append(row)
            state.pop('pending', None)
            atomic_json(path, state)
        verdict = validate_reviews(row, head, base, pr)
        scm.snapshot(pr, BRANCH, head, base)
        if verdict.action == 'NEEDS_USER' or (verdict.action == 'REPAIR' and len(state['rounds']) >= MAX_REVIEW_ROUNDS):
            reason = verdict.reason if verdict.action == 'NEEDS_USER' else 'Três rodadas sem aprovação'
            ledger.emit('NEEDS_USER', 'release_review_blocked', pr=pr, head=head, base=base, reason=reason)
            raise ValueError(reason)
        for role in ('strategic', 'technical'):
            scm.comment(pr, head, base, role.upper()+'_REVIEW', row[role])
        if verdict.action == 'ACCEPT':
            if any(worktree_changes(repo)):
                raise ValueError('Worktree contém alterações após aprovação')
            return head
        state['active_correction'] = {**row, 'pr': pr}
        atomic_json(path, state)


def issue_number(scm):
    issue = scm.get_issue(ISSUE_TITLE)
    if issue:
        return issue['number']
    result = scm.gh('issue', 'create', '--title', ISSUE_TITLE, '--body',
                    f'Release do runner {VERSION}, EAP {EAP}.')
    match = re.search(r'/issues/(\d+)$', result)
    if not match:
        raise ValueError('Criação de Issue incerta; reconciliar antes de repetir')
    return int(match[1])


def finish_merge(scm, state, path, *, revalidate_protection=False):
    intent = state['merge_intent']
    pr, head, base = (intent[field] for field in ('pr', 'head', 'base'))
    if (validate_reviews(state['rounds'][-1], head, base, pr).action != 'ACCEPT'
        or any(intent['ci'].get(key) != intent[key] for key in ('pr', 'head', 'base'))):
        raise ValueError('Intenção de merge sem provas para o snapshot')
    remote = scm.pr(pr)
    if not state.get('merge_confirmed') and remote.get('state') == 'OPEN':
        remote = scm.merge(pr, BRANCH, head, base)
    elif revalidate_protection:
        scm.require_merge_protection(intent['ci'])
    merge = scm.confirm_merge(remote, BRANCH, head, base)
    if git(scm.repo, 'show', f'{merge}:RUNNER_VERSION').strip() != VERSION:
        raise ValueError('Versão do merge difere da release')
    if state.get('merge_confirmed', merge) != merge:
        raise ValueError('Merge diverge da confirmação persistida')
    state['merge_confirmed'] = merge
    atomic_json(path, state)
    scm.issue_close(intent['issue'])
    return {'status': 'UPGRADED', 'version': VERSION, 'pr': pr, 'head': head, 'merge': merge}


def upgrade_once(repo, ledger):
    scm = GitHub(repo)
    scm.verify_identity()
    root = ledger.home/'release-worktrees'
    journal = root/f'{VERSION}-review-cycle.json'
    state = load(journal, {'version': VERSION, 'rounds': []})
    if state.get('version') != VERSION:
        raise ValueError('Journal pertence a outra versão')
    if state.get('merge_intent'):
        try:
            return finish_merge(scm, state, journal)
        except VerifiedCIFailure:
            state.setdefault('superseded_merge_intents', []).append(state.pop('merge_intent'))
            atomic_json(journal, state)
    existing = scm.get_pr(BRANCH)
    if existing and existing['state'] == 'MERGED':
        # Reconcile a release approved by the previous host controller, which
        # recorded both reviews but had no durable merge intent.
        pr = existing['number']
        row = state['rounds'][-1] if state['rounds'] else None
        if not row or validate_reviews(row, row['head'], row['base'], pr).action != 'ACCEPT':
            raise ValueError('Merge anterior sem pareceres independentes aplicáveis')
        head, base = row['head'], row['base']
        proof = verify_ci(scm._api, pr, head, base)
        if not proof:
            raise ValueError('Merge anterior sem CI verificável do snapshot')
        issue = scm.get_issue(ISSUE_TITLE)
        if not issue:
            raise ValueError('Merge anterior sem Issue de release identificável')
        scm.confirm_merge(scm.pr(pr), BRANCH, head, base)
        state['merge_intent'] = {'pr': pr, 'issue': issue['number'], 'head': head, 'base': base, 'ci': proof}
        atomic_json(journal, state)
        return finish_merge(scm, state, journal)
    if existing and existing['state'] != 'OPEN':
        raise ValueError('PR anterior fechado; preservar para reconciliação')
    if existing and state.get('pr', existing['number']) != existing['number']:
        raise ValueError('PR diverge do journal de release')
    git(repo, 'fetch', 'origin', 'develop')
    base = git(repo, 'rev-parse', 'origin/develop')
    if state.get('base', base) != base:
        raise ValueError('Base da release avançou; preservar PR e worktree')
    state['base'] = base
    folder = root/f'runner-{VERSION}'
    root.mkdir(mode=0o700, exist_ok=True)
    if not folder.exists():
        if git(repo, 'branch', '--list', BRANCH):
            git(repo, 'worktree', 'add', str(folder), BRANCH)
        elif existing:
            git(repo, 'fetch', 'origin', BRANCH)
            git(repo, 'worktree', 'add', '-b', BRANCH, str(folder), f'origin/{BRANCH}')
        else:
            git(repo, 'worktree', 'add', '-b', BRANCH, str(folder), base)
    if git(folder, 'branch', '--show-current') != BRANCH or git(folder, 'merge-base', 'HEAD', base) != base:
        raise ValueError('Identidade da worktree diverge da release')
    last = state['rounds'][-1] if state['rounds'] else None
    head = git(folder, 'rev-parse', 'HEAD')
    if not existing and (head == base or state.get('preparation')):
        if 'preparation' not in state:
            state['preparation'] = package_intent(folder, base, ROOT, EAP)
            atomic_json(journal, state)
        if state['preparation']['source'] != base:
            raise ValueError('Preparação pertence a outra BASE')
        head = resume_commits(folder, EAP, state['preparation'], package=ROOT)
    if state.get('active_correction'):
        if not existing:
            raise ValueError('Correção pendente sem PR correspondente')
        resume_correction(folder, GitHub(folder), existing['number'], base, state, journal, ledger, recheck_ci=True)
        head = git(folder, 'rev-parse', 'HEAD')
    staged, dirty = worktree_changes(folder)
    if staged or (dirty and (not last or not existing or head != last['head']
                  or validate_reviews(last, head, base, existing['number']).action != 'REPAIR')):
        raise ValueError('Worktree suja fora de um reparo conhecido; preservar')
    verify_candidate_scope(folder, base, head, package_path)
    if last and last['head'] != head:
        if not existing:
            raise ValueError('Parecer anterior sem PR correspondente')
        verify_reviewed_transition(folder, state, last, head, base, existing['number'])
    if not existing and not dirty:
        gates(folder)
    issue = issue_number(scm)
    git(folder, 'push', '-u', 'origin', BRANCH, timeout=240)
    if existing:
        pr = existing['number']
    else:
        result = scm.gh('pr', 'create', '--base', 'develop', '--head', BRANCH,
                        '--title', ISSUE_TITLE, '--body', f'Refs #{issue}\nContinua a investigação do PR #13, que permanece não aprovado.')
        match = re.search(r'/pull/(\d+)$', result)
        if not match:
            raise ValueError('Criação de PR incerta; reconciliar antes de repetir')
        pr = int(match[1])
    if state.get('pr', pr) != pr:
        raise ValueError('PR diverge do journal de release')
    state['pr'] = pr
    atomic_json(journal, state)
    head = review_release(folder, GitHub(folder), pr, base, state, journal, ledger)
    gates(folder)
    proof = GitHub(folder).checks(pr, head, base, BRANCH)
    state['merge_intent'] = {'issue': issue, 'pr': pr, 'head': head, 'base': base, 'ci': proof}
    atomic_json(journal, state)
    ledger.emit('MERGING', 'release_merge_intent', pr=pr, head=head, base=base)
    return finish_merge(scm, state, journal)


def upgrade(repo, *, ledger=None):
    ledger = ledger or ReleaseLedger(VERSION)
    if ledger.status().get('state') == 'NEEDS_USER':
        return {'status': 'NEEDS_USER'}
    return upgrade_once(Path(repo).resolve(), ledger)


def resume_protected_merge(repo, ledger, head):
    path = ledger.home/'release-worktrees'/f'{VERSION}-review-cycle.json'
    state = load(path, {})
    intent = state.get('merge_intent', {})
    if state.get('version') != VERSION or intent.get('head') != head:
        raise ValueError('SHA autorizado difere da intenção de merge da release')
    scm = GitHub(Path(repo).resolve())
    scm.verify_identity()
    return finish_merge(scm, state, path, revalidate_protection=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', required=True)
    args = parser.parse_args()
    from release_worker import once
    print(json.dumps(once(args.repo), ensure_ascii=False))


if __name__ == '__main__':
    main()
