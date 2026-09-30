"""Prior findings (kdoctor, pr-agent, manual...) offered to the committee as extra context.

They describe what was observed on the RUNNING system, which a plan alone cannot show (e.g. kdoctor saw a container
OOM-killed that the plan still declares with a low memory limit). They are validated against findings-schema,
redacted and size-capped before reaching any model, and treated as data, never as instructions.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from findings_schema import Finding, FindingValidationError

from .redact import redact

MAX_CONTEXT_FINDINGS = 50
MAX_CONTEXT_CHARS = 10_000
_SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


class ContextError(ValueError):
    """A context file cannot be used (unreadable, not JSON, or violating findings-schema)."""


def load_context_findings(paths: list[Path]) -> list[Finding]:
    """Load findings from files holding one finding or an array (e.g. ``kdoctor -o json`` output)."""
    findings: list[Finding] = []
    for path in paths:
        try:
            data: Any = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise ContextError(f"{path}: {exc.strerror or exc}") from exc
        except json.JSONDecodeError as exc:
            raise ContextError(f"{path}: not valid JSON ({exc.msg}, line {exc.lineno})") from exc
        for i, doc in enumerate(data if isinstance(data, list) else [data]):
            try:
                findings.append(Finding.from_dict(doc))
            except FindingValidationError as exc:
                raise ContextError(f"{path} [{i}]: does not satisfy findings-schema: {exc.issues[0]}") from exc
    return findings


def _trim(text: str, limit: int) -> str:
    text = " ".join(redact(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_context(findings: list[Finding], max_chars: int = MAX_CONTEXT_CHARS) -> tuple[str, bool]:
    """Compact, redacted JSON of the findings (most severe first). Returns ``(text, truncated)``."""
    ordered = sorted(findings, key=lambda f: _SEVERITY_RANK[f.severity.value])
    truncated = len(ordered) > MAX_CONTEXT_FINDINGS
    items = [
        {
            "id": f.id,
            "source": f.source.value,
            "severity": f.severity.value,
            "title": _trim(f.title, 200),
            "resource": "/".join(x for x in (f.resource.type, f.resource.namespace, f.resource.name) if x),
            "root_cause": _trim(f.root_cause, 300),
            "evidence": [{"kind": e.kind, "detail": _trim(e.detail, 200)} for e in f.evidence[:3]],
        }
        for f in ordered[:MAX_CONTEXT_FINDINGS]
    ]
    while items:
        text = json.dumps(items, ensure_ascii=False, separators=(",", ":"))
        if len(text) <= max_chars:
            return text, truncated
        items.pop()
        truncated = True
    return "[]", truncated or bool(findings)
