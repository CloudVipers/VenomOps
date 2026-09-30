"""TF-TAG-001: resource missing mandatory tags -> add them (existing tag values are never overwritten)."""

from __future__ import annotations

from typing import ClassVar

from findings_schema import Finding

from ..tools import ToolBox
from .base import AlreadyFixedError, FixerError, FixOutcome, locate_resource

EVIDENCE_KIND = "required-tags"


def parse_required_tags(finding: Finding) -> dict[str, str]:
    """Read ``Key=Value;Key2=Value2`` from the finding's ``required-tags`` evidence."""
    tags: dict[str, str] = {}
    for ev in finding.evidence:
        if ev.kind != EVIDENCE_KIND:
            continue
        for pair in ev.detail.split(";"):
            key, sep, value = pair.partition("=")
            key, value = key.strip(), value.strip()
            if sep and key and value:
                tags[key] = value
    if not tags:
        raise FixerError(
            f"the finding has no '{EVIDENCE_KIND}' evidence with the values to apply "
            "(expected detail like 'Environment=prod;Owner=team-a')"
        )
    return tags


class RequiredTagsFixer:
    ids: ClassVar[frozenset[str]] = frozenset({"TF-TAG-001"})
    title: ClassVar[str] = "Tags obligatorios faltantes"

    def apply(self, finding: Finding, tools: ToolBox) -> FixOutcome:
        tags = parse_required_tags(finding)
        path, rtype, rname = locate_resource(tools, finding)
        changed = tools.edit_hcl(path, {"op": "ensure_tags", "resource": f"{rtype}.{rname}", "tags": tags})
        if not changed:
            raise AlreadyFixedError(f"{rtype}.{rname} already has all of: {', '.join(tags)}")
        added = tools.edits[-1].detail
        return FixOutcome(
            summary=f"Agregar los tags obligatorios a `{rtype}.{rname}`.",
            details=[
                f"Tags solicitados por el finding: {', '.join(f'{k}={v}' for k, v in tags.items())}.",
                f"Resultado: {added}.",
                "Los tags que ya existían conservan su valor; solo se agregan los faltantes.",
            ],
            risk="low",
            files=frozenset({path}),
        )
