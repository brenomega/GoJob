"""Review control policy shared by product and runner release cycles.

A negative review is feedback, not a process crash. Only a demonstrated,
non-delegable obligation can bypass the bounded developer/reviewer correction
loop. This module never creates product code or approves any PR.
"""
from __future__ import annotations
from dataclasses import dataclass
from .agent_privacy import report_context

MAX_REVIEW_ROUNDS = 3  # initial review plus at most two correction/re-review cycles
HUMAN_KINDS = frozenset({'HUMAN_DECISION', 'FORBIDDEN_ACTION', 'EXTERNAL_DEPENDENCY'})

@dataclass(frozen=True)
class ReviewDisposition:
    action: str  # ACCEPT, REPAIR, NEEDS_USER
    reason: str


def disposition(strategic: dict, technical: dict) -> ReviewDisposition:
    pairs = (('strategic', strategic, 'PASS', 'STOP'),
             ('technical', technical, 'ACCEPT', 'BLOCK'))
    for label, review, _, _ in pairs:
        kind = review.get('blocking_kind')
        if isinstance(kind,str) and kind in HUMAN_KINDS:
            findings=review.get('findings')
            details=[review.get('rationale'), *(findings if isinstance(findings,list) else [])]
            reason = ' '.join(item for item in details if isinstance(item, str) and item.strip())
            return ReviewDisposition('NEEDS_USER',
                f'{label} apontou {kind}: {reason or "obrigação pendente de esclarecimento"}')
    for label, review, approval, rejection in pairs:
        decision = review.get('decision')
        if decision not in (approval, 'REVISE', rejection) or review.get('blocking_kind', 'FIXABLE') != 'FIXABLE':
            return ReviewDisposition('NEEDS_USER', f'Parecer {label} com decisão ou categoria inválida')
        if decision != approval:
            findings = review.get('findings')
            if (not isinstance(findings, list) or not findings
                or not all(isinstance(item, str) and item.strip() for item in findings)
                or not str(review.get('rationale') or '').strip()):
                return ReviewDisposition('NEEDS_USER', f'Rejeição {label} sem achados acionáveis')
    if strategic['decision'] == 'PASS' and technical['decision'] == 'ACCEPT':
        return ReviewDisposition('ACCEPT', 'Ambos os revisores aprovaram o mesmo HEAD/base')
    return ReviewDisposition('REPAIR', 'Parecer negativo com achados acionáveis; devolver ao implementador')


def review_feedback(strategic: dict, technical: dict) -> dict:
    """Full rationale for the implementer, not merely the terse findings list."""
    return report_context({'strategic': strategic, 'technical': technical})


def collect_reviews(row, invoke, validate, save, *, parallel=False):
    """Checkpoint each validated role as soon as its independent session returns."""
    roles = ('strategic', 'technical')
    for role in roles:
        if role in row:
            validate(row[role], role)

    def run(role):
        report = report_context(invoke(role))
        validate(report, role)
        return report

    def checkpoint(role, report):
        row[role] = report
        save(row)

    missing = [role for role in roles if role not in row]
    if parallel and len(missing) == 2:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = {pool.submit(run, role): role for role in missing}
            errors = []
            for future in as_completed(futures):
                try:
                    report = future.result()
                except Exception as exc:
                    errors.append(exc)
                else:
                    checkpoint(futures[future], report)
            if errors:
                raise errors[0]
    else:
        for role in missing:
            checkpoint(role, run(role))
