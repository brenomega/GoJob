"""Review control policy shared by product and runner release cycles.

A negative review is feedback, not a process crash. Only a demonstrated,
non-delegable obligation can bypass the bounded developer/reviewer correction
loop. This module never creates product code or approves any PR.
"""
from __future__ import annotations
from dataclasses import dataclass

MAX_REVIEW_ROUNDS = 3  # initial review plus at most two correction/re-review cycles
HUMAN_KINDS = frozenset({'HUMAN_DECISION', 'FORBIDDEN_ACTION', 'EXTERNAL_DEPENDENCY'})

@dataclass(frozen=True)
class ReviewDisposition:
    action: str  # ACCEPT, REPAIR, NEEDS_USER
    reason: str


def disposition(strategic: dict, technical: dict) -> ReviewDisposition:
    s = strategic.get('decision')
    t = technical.get('decision')
    if s == 'PASS' and t == 'ACCEPT':
        return ReviewDisposition('ACCEPT', 'Ambos os revisores aprovaram o mesmo HEAD/base')
    if s not in {'PASS', 'REVISE', 'STOP'} or t not in {'ACCEPT', 'REVISE', 'BLOCK'}:
        raise ValueError('Decisão de revisão não reconhecida')
    # A STOP/BLOCK is normally actionable review feedback. A true hard veto
    # must explicitly identify an obligation that editing code cannot resolve.
    for label, review in (('strategic', strategic), ('technical', technical)):
        if review.get('blocking_kind') in HUMAN_KINDS and review.get('decision') in {'STOP', 'BLOCK'}:
            refs = review.get('findings') or []
            if refs and review.get('rationale'):
                return ReviewDisposition('NEEDS_USER',
                    f'{label} apontou impedimento não delegável: {review["blocking_kind"]}')
    findings = list(strategic.get('findings') or []) + list(technical.get('findings') or [])
    if not any(isinstance(f, str) and f.strip() for f in findings):
        return ReviewDisposition('NEEDS_USER', 'Rejeição sem achados acionáveis: diagnóstico necessário')
    return ReviewDisposition('REPAIR', 'Parecer negativo com achados acionáveis; devolver ao implementador')


def review_feedback(strategic: dict, technical: dict) -> dict:
    """Full rationale for the implementer, not merely the terse findings list."""
    fields=('decision', 'findings', 'rationale', 'opportunity_cost', 'alternative',
            'north_star_impact', 'real_evidence', 'tests_assessed', 'blocking_kind')
    return {kind: {field: review[field] for field in fields if field in review}
            for kind, review in (('strategic', strategic), ('technical', technical))}
