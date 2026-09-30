"""Pull Request title and body (Spanish: the audience is the team reviewing the change)."""

from __future__ import annotations

from findings_schema import Finding

from .fixes import FixOutcome
from .redact import redact
from .safety import CommandResult

MAX_PLAN_CHARS = 6000
RISK_LABEL = {"low": "🟢 Bajo", "medium": "🟡 Medio", "high": "🔴 Alto"}

CHECKLIST = [
    "Revisé el diff: el cambio es el mínimo necesario y no toca nada no relacionado.",
    "El `terraform plan` adjunto muestra solo los cambios esperados (sin recursos destruidos o reemplazados).",
    "Entiendo el riesgo indicado y tengo un plan de reversión si algo sale mal.",
    "El cambio se desplegará mediante el pipeline habitual: este PR no aplica nada por sí mismo.",
]


def title(finding: Finding, outcome: FixOutcome) -> str:
    return f"fix(terraform): {outcome.summary.rstrip('.').replace('`', '')} [{finding.id}]"[:140]


def _code(text: str, lang: str = "") -> str:
    fence = "````" if "```" in text else "```"
    return f"{fence}{lang}\n{text.rstrip()}\n{fence}"


def build(
    finding: Finding,
    outcome: FixOutcome,
    diff: str,
    validate: CommandResult,
    plan: CommandResult | None,
    plan_note: str | None,
) -> str:
    r = finding.resource
    # Kubernetes objects (from kdoctor) have a namespace; Terraform resources are `type.name`.
    where = f"`{r.type} {r.namespace}/{r.name}`" if r.namespace else f"`{r.type}.{r.name}`"
    where += f" en `{r.path}`" if r.path else ""
    evidence = "\n".join(f"- **{e.kind}**: {redact(e.detail)}" for e in finding.evidence)
    details = "\n".join(f"- {d}" for d in outcome.details)

    if plan is not None and plan.ok:
        text = redact(plan.output)
        if len(text) > MAX_PLAN_CHARS:
            text = text[:MAX_PLAN_CHARS] + "\n… (salida truncada)"
        plan_section = (
            f"<details>\n<summary>Ver salida de <code>terraform plan</code></summary>\n\n{_code(text)}\n\n</details>"
        )
    else:
        reason = plan_note or (redact(plan.output)[:1500] if plan is not None else "no se ejecutó")
        plan_section = (
            f"⚠ **`terraform plan` no disponible**: {reason}\n\nRevisar el plan en el pipeline antes de aprobar."
        )

    checklist = "\n".join(f"- [ ] {item}" for item in CHECKLIST)
    return f"""## Finding `{finding.id}` · {finding.severity.value.upper()}

**{finding.title}**

- **Recurso:** {where}
- **Fuente:** {finding.source.value}
- **Causa probable:** {finding.root_cause}

**Evidencia**
{evidence}

## Cambio propuesto

{outcome.summary}

{details}

{_code(diff, "diff")}

## Validación

- `terraform validate`: ✅ correcto

{plan_section}

## Nivel de riesgo

{RISK_LABEL.get(outcome.risk, outcome.risk)} (riesgo del fix según el finding: `{finding.risk_of_fix.value}`)

## Checklist de revisión humana

{checklist}

---
_Generado por **pr-agent** (VenomOps). Requiere **aprobación humana**: el agente nunca hace merge ni aplica cambios._
"""
