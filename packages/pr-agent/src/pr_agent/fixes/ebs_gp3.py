"""TF-EBS-001: gp2 volumes -> gp3 (cheaper, and 3000 IOPS baseline independent of size)."""

from __future__ import annotations

from typing import ClassVar

from findings_schema import Finding

from .. import hcl
from ..tools import ToolBox
from .base import AlreadyFixedError, FixerError, FixOutcome, locate_resource

# resource type -> attribute that holds the volume type (nested block attributes are covered too)
VOLUME_ATTR = {
    "aws_ebs_volume": "type",
    "aws_instance": "volume_type",
    "aws_launch_template": "volume_type",
}
GP2_LARGE_GIB = 1000  # above this, gp2 gives more than 3000 baseline IOPS (3 IOPS/GiB)


class EbsGp3Fixer:
    ids: ClassVar[frozenset[str]] = frozenset({"TF-EBS-001"})
    title: ClassVar[str] = "Volúmenes EBS gp2 que deberían ser gp3"

    def apply(self, finding: Finding, tools: ToolBox) -> FixOutcome:
        path, rtype, rname = locate_resource(tools, finding, frozenset(VOLUME_ATTR))
        try:
            changed = tools.edit_hcl(
                path,
                {
                    "op": "replace_attr",
                    "resource": f"{rtype}.{rname}",
                    "attr": VOLUME_ATTR[rtype],
                    "old": "gp2",
                    "new": "gp3",
                },
            )
        except Exception as exc:  # noqa: BLE001
            if "has no" in str(exc):
                raise AlreadyFixedError(f"{rtype}.{rname} has no gp2 volume (already gp3 or another type)") from exc
            raise
        if not changed:
            raise FixerError("the edit produced no change")

        details = [
            f"Resultado: {tools.edits[-1].detail}.",
            "gp3 cuesta ~20 % menos por GiB y ofrece 3000 IOPS y 125 MB/s de base.",
        ]
        risk = "low"
        attrs = hcl.resource_attrs(hcl.parse(tools.read_file(path)), rtype, rname) or {}
        size = attrs.get("size")
        if isinstance(size, int) and size > GP2_LARGE_GIB:
            risk = "medium"
            details.append(
                f"⚠ El volumen es de {size} GiB: con gp2 tenía más de 3000 IOPS de base. "
                "Revisar y, si hace falta, definir `iops`/`throughput` para no perder rendimiento."
            )
        return FixOutcome(
            summary=f"Migrar `{rtype}.{rname}` de gp2 a gp3.",
            details=details + ["El cambio de tipo de volumen se aplica en línea, sin reemplazar el recurso."],
            risk=risk,
            files=frozenset({path}),
        )
