"""KD-K8S-002 (from kdoctor): container killed by OOM -> raise ``limits.memory`` in the owning Terraform workload.

Contract with kdoctor (docs/decisiones/0004): the finding carries an evidence of kind ``memory-limit-change`` with
``container=<name>;from=<quantity>;to=<quantity>``. Without it (e.g. the container had no limit) there is nothing
safe to change automatically.
"""

from __future__ import annotations

import re
from typing import Any, ClassVar

from findings_schema import Finding

from .. import hcl
from ..tools import ToolBox, ToolError
from .base import AlreadyFixedError, FixerError, FixOutcome

EVIDENCE_KIND = "memory-limit-change"
_QUANTITY = re.compile(r"^\d+(\.\d+)?(Ki|Mi|Gi)?$")

# Terraform resource type -> regex (given the Terraform name) that the owned Pods' names must match.
_WORKLOADS: dict[str, str] = {
    "kubernetes_pod": "{n}",
    "kubernetes_pod_v1": "{n}",
    "kubernetes_deployment": r"{n}-[a-z0-9]+-[a-z0-9]{{5}}",  # <deployment>-<replicaset-hash>-<pod-hash>
    "kubernetes_deployment_v1": r"{n}-[a-z0-9]+-[a-z0-9]{{5}}",
    "kubernetes_stateful_set": r"{n}-\d+",
    "kubernetes_stateful_set_v1": r"{n}-\d+",
    "kubernetes_daemon_set_v1": r"{n}-[a-z0-9]{{5}}",
    "kubernetes_job_v1": r"{n}-[a-z0-9]{{5}}",
}


def parse_change(finding: Finding) -> tuple[str, str, str]:
    """``(container, from, to)`` from the finding's ``memory-limit-change`` evidence."""
    for ev in finding.evidence:
        if ev.kind != EVIDENCE_KIND:
            continue
        fields = dict(part.partition("=")[::2] for part in ev.detail.split(";"))
        container, old, new = (fields.get(k, "").strip() for k in ("container", "from", "to"))
        if container and _QUANTITY.match(old) and _QUANTITY.match(new):
            return container, old, new
        raise FixerError(f"malformed '{EVIDENCE_KIND}' evidence: {ev.detail!r}")
    raise FixerError(
        f"the finding has no '{EVIDENCE_KIND}' evidence (kdoctor found no current memory limit, so there is no value "
        "to change): define requests/limits by hand"
    )


def _metadata(attrs: dict[str, Any]) -> tuple[str | None, str]:
    """Literal ``(name, namespace)`` from a workload's ``metadata`` block (None when it is not a literal)."""
    meta = attrs.get("metadata")
    meta = meta[0] if isinstance(meta, list) and meta else meta
    if not isinstance(meta, dict):
        return None, "default"

    def literal(v: Any) -> str | None:
        return v if isinstance(v, str) and "${" not in v else None

    return literal(meta.get("name")), literal(meta.get("namespace")) or "default"


class K8sMemoryLimitFixer:
    ids: ClassVar[frozenset[str]] = frozenset({"KD-K8S-002"})
    title: ClassVar[str] = "Contenedor terminado por OOMKilled"

    def find_workload(self, tools: ToolBox, finding: Finding) -> tuple[str, str, str]:
        pod, namespace = finding.resource.name, finding.resource.namespace or "default"
        matches: list[tuple[str, str, str]] = []
        for path in tools.list_files("**/*.tf"):
            parsed = hcl.parse(tools.read_file(path))
            for entry in parsed.get("resource", []):
                for rtype, by_name in hcl._normalize(entry).items():  # noqa: SLF001 - same package
                    pattern = _WORKLOADS.get(rtype)
                    if pattern is None:
                        continue
                    for rname, attrs in by_name.items():
                        name, ns = _metadata(attrs)
                        if name and ns == namespace and re.fullmatch(pattern.format(n=re.escape(name)), pod):
                            matches.append((path, rtype, rname))
        if not matches:
            raise FixerError(
                f"no kubernetes_* workload in the Terraform matches pod {namespace}/{pod} "
                f"(looked for {sorted(_WORKLOADS)} with a literal metadata.name/namespace)"
            )
        if len(matches) > 1:
            raise FixerError(f"pod {namespace}/{pod} matches several Terraform workloads: {matches}")
        return matches[0]

    def apply(self, finding: Finding, tools: ToolBox) -> FixOutcome:
        container, old, new = parse_change(finding)
        path, rtype, rname = self.find_workload(tools, finding)
        try:
            changed = tools.edit_hcl(
                path,
                {
                    "op": "set_memory_limit",
                    "resource": f"{rtype}.{rname}",
                    "container": container,
                    "old": old,
                    "new": new,
                },
            )
        except ToolError as exc:
            if "already" in str(exc):
                raise AlreadyFixedError(str(exc)) from exc
            raise FixerError(str(exc)) from exc
        if not changed:
            raise FixerError("the edit produced no change")
        return FixOutcome(
            summary=f"Subir limits.memory del contenedor `{container}` de {old} a {new} en `{rtype}.{rname}`.",
            details=[
                f"Resultado: {tools.edits[-1].detail}.",
                f"Origen: kdoctor detectó OOMKilled en el pod `{finding.resource.namespace}/{finding.resource.name}`.",
                f"{new} es un punto de partida sugerido por kdoctor: ajustar con el consumo real (metrics-server o tu "
                "herramienta de métricas). Si el consumo sigue creciendo, hay una fuga de memoria que corregir.",
                "Más memoria por réplica significa más costo y puede requerir nodos más grandes.",
            ],
            risk="low",
            files=frozenset({path}),
        )
