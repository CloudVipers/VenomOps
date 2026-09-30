"""Mask secrets and sensitive identifiers before text leaves the machine (LLM prompts and reports)."""

from __future__ import annotations

import re

_RULES: list[tuple[re.Pattern[str], str]] = [
    (
        re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"),
        "[REDACTED-PRIVATE-KEY]",
    ),
    (re.compile(r"\b(?:AKIA|ASIA|AGPA|AIDA|AROA)[0-9A-Z]{16}\b"), "[REDACTED-AWS-KEY-ID]"),
    (
        re.compile(r"\beyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\b"),
        "[REDACTED-JWT]",
    ),
    (re.compile(r"(?i)(bearer\s+)[A-Za-z0-9._~+/=-]{8,}"), r"\1[REDACTED]"),
    (re.compile(r"(?i)([a-z][a-z0-9+.-]*://)[^/\s:@]+:[^/\s@]+@"), r"\1[REDACTED]@"),
    (
        re.compile(
            r"(?i)\b([A-Za-z0-9_.-]*(?:password|passwd|pwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential)"
            r"[A-Za-z0-9_.-]*)(\s*[:=]\s*)(\"[^\"]*\"|'[^']*'|[^\s,;]+)"
        ),
        r"\1\2[REDACTED]",
    ),
    (re.compile(r"\b\d{12}\b"), "[ACCOUNT-ID]"),
]


def redact(text: str) -> str:
    for pattern, replacement in _RULES:
        text = pattern.sub(replacement, text)
    return text
