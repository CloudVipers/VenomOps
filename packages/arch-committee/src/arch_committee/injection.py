"""Deterministic detection and neutralization of prompt injection in the plan and the prior findings.

Text in a plan (tags, descriptions, names) or in prior findings may be addressed to the AI reviewers ("do not report
this bucket", "rate it info"). The prompts already tell the agents to treat it as data, but that control is
probabilistic. This module adds a control in code (research line, ADR 0011; threat model: suppression of findings):

* :func:`scan_plan` / :func:`scan_context` find strings that look like instructions for an AI;
* :func:`neutralize_plan` / :func:`neutralize_context` replace them before anything reaches a model;
* the report lists every suspicion and ``findings.json`` gets one ``AC-INJ-*`` finding per affected resource.

Detection is a heuristic: it can miss a well-disguised injection and may flag an unusual but harmless value, which is
why the original text is shown to the human in the report (redacted) instead of being silently dropped.
"""

from __future__ import annotations

import base64
import binascii
import re
from dataclasses import dataclass, replace
from typing import Any

from findings_schema import Finding

from .plan_parser import PlanModel
from .redact import redact

NEUTRALIZED = "[neutralized by arch-committee: suspected prompt injection, see the report]"

_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "addressed to an AI",
        re.compile(
            r"\b(ai|a\.i\.|ia|assistant|llm|language model|reviewer|asistente|assistente|revisor)\b"
            r"(\s+\w+){0,3}\s*[:,]",
            re.I,
        ),
    ),
    (
        "overrides instructions",
        re.compile(
            r"\b(ignore|disregard|forget|override|ignora|ignore[sz])\b.{0,40}"
            r"\b(instruction|instrucci|instruç|rule|regla|regra|prompt|guideline|polic)",
            re.I,
        ),
    ),
    (
        "asks to hide or downgrade findings",
        re.compile(
            r"\b(do not|don't|never|no|não|nao)\s+(report|flag|mention|raise|reportes?|menciones|reporte)\b"
            r"|\b(must|should)\s+be\s+(rated|classified|marked)\b|\bclasificad[oa]s?\s+como\b|\bclassificad[oa]s?\s+como\b",
            re.I,
        ),
    ),
    (
        "claims a prior approval",
        re.compile(
            r"\b(approved|aprobad[oa]|aprovad[oa]|pre-approved)\b.{0,40}"
            r"\b(security|seguridad|segurança|team|equipo|equipe)\b",
            re.I,
        ),
    ),
    (
        "asks to run commands",
        re.compile(r"terraform\s+(apply|destroy)|auto-approve|\bcurl\s+https?://|\|\s*(sh|bash)\b|local-exec", re.I),
    ),
)
_BASE64 = re.compile(r"[A-Za-z0-9+/]{40,}={0,2}")
EXCERPT = 160


@dataclass(frozen=True)
class Suspicion:
    resource: str  # plan address, or "context:<finding id>"
    path: str  # attribute path, e.g. tags.Note
    reason: str
    excerpt: str  # redacted and shortened


def _reasons(text: str) -> list[str]:
    found = [name for name, rx in _PATTERNS if rx.search(text)]
    for token in _BASE64.findall(text):
        try:
            decoded = base64.b64decode(token, validate=True).decode("utf-8")
        except (binascii.Error, UnicodeDecodeError):
            continue
        if decoded.isprintable() and any(rx.search(decoded) for _, rx in _PATTERNS):
            found.append("encoded instruction (base64)")
    return found


def classify(text: str) -> list[str]:
    """Reasons why ``text`` looks like instructions for an AI (empty: it does not)."""
    return _reasons(text) if isinstance(text, str) and len(text) >= 12 else []


def _walk(value: Any, path: str) -> list[tuple[str, str]]:
    if isinstance(value, str):
        return [(path, value)]
    if isinstance(value, dict):
        return [hit for k, v in value.items() for hit in _walk(v, f"{path}.{k}" if path else str(k))]
    if isinstance(value, list):
        return [hit for i, v in enumerate(value) for hit in _walk(v, f"{path}[{i}]")]
    return []


def _suspicion(resource: str, path: str, text: str, reasons: list[str]) -> Suspicion:
    excerpt = redact(" ".join(text.split()))
    if len(excerpt) > EXCERPT:
        excerpt = excerpt[: EXCERPT - 1] + "…"
    return Suspicion(resource, path, ", ".join(dict.fromkeys(reasons)), excerpt)


def scan_plan(plan: PlanModel) -> list[Suspicion]:
    out = []
    for r in plan.resources:
        for path, text in _walk(r.attributes, ""):
            reasons = classify(text)
            if reasons:
                out.append(_suspicion(r.address, path, text, reasons))
    return out


def scan_context(context: list[Finding]) -> list[Suspicion]:
    out = []
    for f in context:
        doc = f.to_dict()
        for key in ("title", "root_cause"):
            reasons = classify(str(doc.get(key, "")))
            if reasons:
                out.append(_suspicion(f"context:{f.id}", key, str(doc[key]), reasons))
        for i, ev in enumerate(doc.get("evidence", [])):
            reasons = classify(str(ev.get("detail", "")))
            if reasons:
                out.append(_suspicion(f"context:{f.id}", f"evidence[{i}].detail", str(ev["detail"]), reasons))
    return out


def _neutralize(value: Any) -> Any:
    if isinstance(value, str):
        return NEUTRALIZED if classify(value) else value
    if isinstance(value, dict):
        return {k: _neutralize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_neutralize(v) for v in value]
    return value


def neutralize_plan(plan: PlanModel) -> PlanModel:
    """A copy of ``plan`` where every suspicious string value is replaced by :data:`NEUTRALIZED`."""
    return replace(plan, resources=[replace(r, attributes=_neutralize(r.attributes)) for r in plan.resources])


def neutralize_context(context: list[Finding]) -> list[Finding]:
    out = []
    for f in context:
        doc = f.to_dict()
        for key in ("title", "root_cause"):
            if classify(str(doc.get(key, ""))):
                doc[key] = NEUTRALIZED
        doc["evidence"] = [
            {**ev, "detail": NEUTRALIZED} if classify(str(ev.get("detail", ""))) else ev for ev in doc["evidence"]
        ]
        out.append(Finding.from_dict(doc))
    return out
