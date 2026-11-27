"""Environment and diagnostic data permitted across the agent boundary."""
from __future__ import annotations

import os
import re

RUNTIME_ENV = frozenset({
    'PATH', 'HOME', 'USER', 'LOGNAME', 'LANG', 'LC_ALL', 'LC_CTYPE', 'TERM',
    'SHELL', 'TMPDIR', 'XDG_CACHE_HOME', 'XDG_CONFIG_HOME', 'XDG_DATA_HOME',
    'XDG_RUNTIME_DIR', 'CODEX_HOME', 'SSL_CERT_FILE', 'SSL_CERT_DIR',
})
REVIEW_FIELDS = frozenset({
    'decision', 'blocking_kind', 'base_sha', 'head_sha', 'base', 'head', 'pr', 'round', 'rationale', 'findings',
    'follow_up', 'opportunity_cost', 'alternative', 'north_star_impact', 'real_evidence', 'tests_assessed',
})
REPORT_FIELDS = frozenset({
    'status', 'summary', 'missing_prerequisites', 'changed', 'tests', 'limitations', 'decision', 'cause',
    'evidence', 'plan', 'confidence', 'risk_if_wrong', 'reproduction', 'hypothesis',
    'expected_failure', 'priority', 'proposed_tests', 'strategic', 'technical', 'reviews', 'diagnosis',
})
SENSITIVE_FIELD = re.compile(
    r'(?i)(?:password|passwd|secret|token|api[_ -]?key|authorization|cookie|'
    r'cpf|\brg\b|email|e-mail|phone|telefone|celular|resume|curr[ií]culo|candidate[_ -]?name|'
    r'nome[_ -]?(?:do[_ -]?)?candidato|full[_ -]?name|address|endere[cç]o|'
    r'diversity|diversidade|race|ethnicity|vault)'
)
LABEL = re.compile(
    r'(?im)(["\x27]?[\w.-]*(?:' + SENSITIVE_FIELD.pattern.removeprefix('(?i)') +
    r')[\w.-]*["\x27]?\s*[:=]\s*)(?:"(?:\\.|[^"\\])*"|\x27(?:\\.|[^\x27\\])*\x27|[^\r\n,;}]+)'
)


def agent_env():
    return {name: value for name, value in os.environ.items() if name in RUNTIME_ENV}


def redact(text):
    value = str(text)
    value = LABEL.sub(r'\1[REDACTED]', value)
    value = re.sub(r'(?i)\bBearer\s+[\w.+/=-]+', 'Bearer [REDACTED]', value)
    value = re.sub(r'(?i)\b(?:gh[pousr]_|github_pat_|gsk_|sk-)[\w-]{12,}', '[REDACTED]', value)
    value = re.sub(r'\b\d{6,12}:[\w-]{20,}\b', '[REDACTED]', value)
    value = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[REDACTED]', value)
    value = re.sub(r'(?<!\w)(?:\d{3}\.\d{3}\.\d{3}-\d{2}|\d{11})(?!\w)', '[REDACTED]', value)
    value = re.sub(r'(?<!\w)(?:\+55[ -]?)?\(?\d{2}\)?[ -]\d{4,5}[ -]\d{4}(?!\d)', '[REDACTED]', value)
    return value


def report_context(value):
    """Project known report fields and redact text before serializing JSON."""
    if isinstance(value, dict):
        return {key: report_context(item) for key, item in value.items()
                if key in (REVIEW_FIELDS | REPORT_FIELDS)}
    if isinstance(value, list):
        return [report_context(item) for item in value[:40]]
    if isinstance(value, str):
        return redact(value)[:4500]
    return value if value is None or isinstance(value, (int, float, bool)) else None
