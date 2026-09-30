"""Committee flow: round 1 (parallel analysis) -> round 2 (cross-examination) -> moderator.

Design notes (see ADR 0003): a small explicit orchestrator on top of the :class:`~arch_committee.llm.LLM`
interface. A shared :class:`~arch_committee.llm.Budget` bounds tokens and rounds; when it runs out, or a call
fails, the committee degrades (skips steps and says so) instead of failing, and it never loses a finding or hides
a disagreement silently.
"""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError

from .agents import MODERATOR, SPECIALISTS, AgentSpec
from .llm import LLM, Budget, LLMError
from .models import (
    AgentFinding,
    Challenge,
    Disagreement,
    FinalFinding,
    ModeratorOutput,
    Position,
    Round1Output,
    Round2Output,
    tool_schema,
)
from .plan_parser import PlanModel, render_for_llm

M = TypeVar("M", bound=BaseModel)

SEVERITY_RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}
MAX_FINDINGS_PER_AGENT = 8
PLAN_BRIEF_CHARS = 12_000


@dataclass
class CommitteeConfig:
    max_total_tokens: int = 200_000
    max_rounds: int = 2  # 1 = analysis + moderator (no cross-examination); 2 = adds the rebuttal round
    max_output_tokens: int = 4096
    plan_max_chars: int = 60_000
    workers: int = 4


@dataclass(frozen=True)
class Raised:
    fid: str  # SEC-1, COST-2, ...
    agent: str
    finding: AgentFinding


@dataclass(frozen=True)
class ChallengeRecord:
    by: str  # agent name
    challenge: Challenge


@dataclass(frozen=True)
class StepUsage:
    step: str  # round1 | round2 | moderator
    agent: str
    tokens: int


@dataclass
class CommitteeResult:
    plan: PlanModel
    raised: list[Raised]
    challenges: list[ChallengeRecord]
    summary: str
    final: list[FinalFinding]
    disagreements: list[Disagreement]
    accepted_risks: list[Any]
    moderator_ran: bool
    usage: list[StepUsage]
    tokens_used: int
    tokens_max: int
    rounds_run: int
    notes: list[str] = field(default_factory=list)
    generated_at: datetime = field(default_factory=lambda: datetime.now(UTC))
    truncated_plan: bool = False


# ---- one guarded model call -------------------------------------------------------------------------------------


class _Runner:
    def __init__(self, llm: LLM, budget: Budget, config: CommitteeConfig) -> None:
        self.llm, self.budget, self.config = llm, budget, config

    def call(
        self, agent: AgentSpec, user: str, tool_name: str, description: str, model: type[M]
    ) -> tuple[M | None, int, str | None]:
        """Returns ``(parsed | None, tokens, note | None)``; never raises for model/budget problems."""
        system = agent.system_prompt()
        estimate = (len(system) + len(user)) // 3 + self.config.max_output_tokens
        if not self.budget.reserve(estimate):
            return (
                None,
                0,
                f"{agent.name}: omitido por presupuesto de tokens "
                f"(se necesitaban ~{estimate}, quedan {self.budget.remaining})",
            )
        try:
            payload, usage = self.llm.converse_tool(
                system=system,
                user=user,
                tool_name=tool_name,
                tool_description=description,
                schema=tool_schema(model),
                max_tokens=self.config.max_output_tokens,
            )
        except LLMError as exc:
            self.budget.release(estimate)
            return None, 0, f"{agent.name}: falló la llamada al modelo ({exc})"
        self.budget.settle(estimate, usage.total)
        try:
            return model.model_validate(payload), usage.total, None
        except ValidationError as exc:
            return (
                None,
                usage.total,
                f"{agent.name}: respuesta con formato inválido ({exc.error_count()} errores de validación)",
            )


# ---- helpers ----------------------------------------------------------------------------------------------------


def _valid_addresses(plan: PlanModel) -> set[str]:
    return {r.address for r in plan.resources} | {r.base_address for r in plan.resources} | {"plan"}


def _compact(f: AgentFinding) -> dict[str, Any]:
    return {
        "title": f.title,
        "severity": f.severity,
        "resource": f.resource,
        "root_cause": f.root_cause,
        "evidence": [e.model_dump() for e in f.evidence],
    }


def _others_json(raised: list[Raised], exclude_agent: str) -> str:
    return json.dumps(
        [{"id": r.fid, "agent": r.agent, **_compact(r.finding)} for r in raised if r.agent != exclude_agent],
        ensure_ascii=False,
    )


def _consolidate_without_moderator(
    raised: list[Raised], challenges: list[ChallengeRecord]
) -> tuple[list[FinalFinding], list[Disagreement]]:
    """Deterministic fallback when the moderator cannot run: keep every finding, merge exact duplicates."""
    groups: dict[tuple[str, str], list[Raised]] = {}
    for r in raised:
        groups.setdefault((r.finding.resource, r.finding.title.strip().lower()), []).append(r)
    final: list[FinalFinding] = []
    for members in groups.values():
        best = min(members, key=lambda r: SEVERITY_RANK[r.finding.severity])
        data = best.finding.model_dump()
        final.append(
            FinalFinding(
                **data,
                merged_from=[m.fid for m in members],
                decision="Consolidación automática sin moderador: se conserva la severidad más alta reportada.",
            )
        )
    final.sort(key=lambda f: SEVERITY_RANK[f.severity])

    by_id = {r.fid: r for r in raised}
    disagreements: list[Disagreement] = []
    for target in sorted({c.challenge.target for c in challenges if c.challenge.stance == "disagree"}):
        raised_by = by_id[target]
        against = [c for c in challenges if c.challenge.target == target and c.challenge.stance == "disagree"]
        disagreements.append(
            Disagreement(
                topic=raised_by.finding.title,
                finding_ids=[target],
                positions=[
                    Position(
                        agent=raised_by.agent,
                        stance=f"Reporta {raised_by.finding.severity}: {raised_by.finding.root_cause}",
                    ),
                    *[Position(agent=c.by, stance=c.challenge.argument) for c in against],
                ],
                resolution="unresolved",
                rationale="El moderador no pudo ejecutarse (presupuesto o error del modelo): requiere decisión humana.",
            )
        )
    return final, disagreements


def _normalize_moderator(
    out: ModeratorOutput, raised: list[Raised], challenges: list[ChallengeRecord], valid: set[str], notes: list[str]
) -> tuple[list[FinalFinding], list[Disagreement]]:
    """Validate the moderator's answer and apply the safety nets (no finding lost, no disagreement hidden)."""
    ids = {r.fid for r in raised}
    by_id = {r.fid: r for r in raised}
    final: list[FinalFinding] = []
    for f in out.findings:
        refs = [i for i in f.merged_from if i in ids]
        if not refs:
            notes.append(f"moderador: se descartó «{f.title}» porque no referencia ningún hallazgo real")
            continue
        if f.resource not in valid:
            notes.append(f"moderador: se descartó «{f.title}» por un recurso que no está en el plan ({f.resource})")
            continue
        final.append(f.model_copy(update={"merged_from": refs}))

    covered = {i for f in final for i in f.merged_from}
    for ar in out.accepted_risks:
        covered.update(i for i in ar.finding_ids if i in ids)
    for r in raised:
        if r.fid not in covered:
            final.append(
                FinalFinding(
                    **r.finding.model_dump(),
                    merged_from=[r.fid],
                    decision=f"Sin consolidar por el moderador: se conserva tal como lo reportó {r.agent}.",
                )
            )
            notes.append(f"{r.fid} no fue tratado por el moderador y se conservó sin cambios")

    disagreements = [
        d.model_copy(update={"finding_ids": [i for i in d.finding_ids if i in ids]}) for d in out.disagreements
    ]
    handled = {i for d in disagreements for i in d.finding_ids}
    for target in sorted({c.challenge.target for c in challenges if c.challenge.stance == "disagree"} - handled):
        r = by_id[target]
        against = [c for c in challenges if c.challenge.target == target and c.challenge.stance == "disagree"]
        disagreements.append(
            Disagreement(
                topic=r.finding.title,
                finding_ids=[target],
                positions=[
                    Position(agent=r.agent, stance=f"Reporta {r.finding.severity}: {r.finding.root_cause}"),
                    *[Position(agent=c.by, stance=c.challenge.argument) for c in against],
                ],
                resolution="unresolved",
                rationale="El moderador no se pronunció sobre este desacuerdo: requiere decisión humana.",
            )
        )
        notes.append(f"desacuerdo sobre {target} no resuelto por el moderador; se registró como no resuelto")
    return final, disagreements


# ---- main flow --------------------------------------------------------------------------------------------------


def run_committee(plan: PlanModel, llm: LLM, config: CommitteeConfig | None = None) -> CommitteeResult:
    config = config or CommitteeConfig()
    budget = Budget(config.max_total_tokens, config.max_rounds)
    runner = _Runner(llm, budget, config)
    notes: list[str] = []
    usage: list[StepUsage] = []
    valid = _valid_addresses(plan)

    plan_text, truncated = render_for_llm(plan, config.plan_max_chars)
    plan_brief, _ = render_for_llm(plan, PLAN_BRIEF_CHARS)
    if truncated:
        notes.append("el plan era grande: se acortaron valores largos de atributos antes de enviarlo a los agentes")

    # Round 1: the four specialists analyse the plan in parallel.
    def round1(agent: AgentSpec) -> tuple[AgentSpec, Round1Output | None, int, str | None]:
        user = (
            "Review this Terraform plan from your specialty and report your findings with the tool.\n\n"
            f"<plan>\n{plan_text}\n</plan>"
        )
        out, tokens, note = runner.call(
            agent, user, "report_findings", "Report your findings about the plan.", Round1Output
        )
        return agent, out, tokens, note

    with ThreadPoolExecutor(max_workers=config.workers) as pool:
        r1 = list(pool.map(round1, SPECIALISTS))  # map keeps the specialists' order: results are deterministic

    raised: list[Raised] = []
    for agent, out1, tokens, note in r1:
        usage.append(StepUsage("round1", agent.name, tokens))
        if note:
            notes.append(note)
        if out1 is None:
            continue
        counter = 0
        for f in out1.findings[:MAX_FINDINGS_PER_AGENT]:
            if f.resource not in valid:
                notes.append(
                    f"{agent.name}: se descartó «{f.title}» por un recurso que no está en el plan ({f.resource})"
                )
                continue
            counter += 1
            raised.append(Raised(f"{agent.code}-{counter}", agent.name, f))

    # Round 2: cross-examination (only if there is something to cross-examine and rounds allow it).
    challenges: list[ChallengeRecord] = []
    rounds_run = 1
    if config.max_rounds >= 2 and raised:
        rounds_run = 2

        def round2(agent: AgentSpec) -> tuple[AgentSpec, Round2Output | None, int, str | None]:
            own = [r.fid for r in raised if r.agent == agent.name]
            others = _others_json(raised, agent.name)
            if others == "[]":
                return agent, None, 0, None
            user = (
                "Other specialists raised the findings below. Your own findings are "
                f"{own or 'none'}: do not challenge them. For each finding of THEIRS you have a grounded opinion on, "
                "respond with stance agree|disagree|refine and a short argument tied to the plan or to an explicit "
                "cost/risk trade-off (propose a severity if you think it is wrong). "
                "Skip findings you have no opinion on.\n\n"
                f"<plan_brief>\n{plan_brief}\n</plan_brief>\n\n<findings>\n{others}\n</findings>"
            )
            out, tokens, note = runner.call(
                agent, user, "respond_to_findings", "Respond to other agents' findings.", Round2Output
            )
            return agent, out, tokens, note

        with ThreadPoolExecutor(max_workers=config.workers) as pool:
            r2 = list(pool.map(round2, SPECIALISTS))
        by_id = {r.fid: r for r in raised}
        for agent, out2, tokens, note in r2:
            if tokens or note:
                usage.append(StepUsage("round2", agent.name, tokens))
            if note:
                notes.append(note)
            if out2 is None:
                continue
            seen: set[tuple[str, str]] = set()
            for c in out2.challenges:
                target = by_id.get(c.target)
                if target is None or target.agent == agent.name or (agent.name, c.target) in seen:
                    continue  # unknown id, self-challenge or duplicate: ignore
                seen.add((agent.name, c.target))
                challenges.append(ChallengeRecord(agent.name, c))
    elif config.max_rounds < 2:
        notes.append("max_rounds=1: no hubo ronda de réplica")

    # Moderator.
    summary = ""
    final: list[FinalFinding] = []
    disagreements: list[Disagreement] = []
    accepted: list[Any] = []
    moderator_ran = False
    if raised:
        raised_payload = [{"id": r.fid, "agent": r.agent, **_compact(r.finding)} for r in raised]
        challenge_payload = [
            {
                "by": c.by,
                "target": c.challenge.target,
                "stance": c.challenge.stance,
                "argument": c.challenge.argument,
                "suggested_severity": c.challenge.suggested_severity,
            }
            for c in challenges
        ]
        user = (
            "Consolidate and decide. Raised findings and the challenges made to them follow.\n\n"
            f"<plan_brief>\n{plan_brief}\n</plan_brief>\n\n"
            f"<raised>\n{json.dumps(raised_payload, ensure_ascii=False)}\n</raised>\n\n"
            f"<challenges>\n{json.dumps(challenge_payload, ensure_ascii=False)}\n</challenges>"
        )
        out, tokens, note = runner.call(
            MODERATOR, user, "finalize", "Publish the committee's final decisions.", ModeratorOutput
        )
        usage.append(StepUsage("moderator", MODERATOR.name, tokens))
        if note:
            notes.append(note)
        if out is not None:
            moderator_ran = True
            final, disagreements = _normalize_moderator(out, raised, challenges, valid, notes)
            summary, accepted = out.summary, list(out.accepted_risks)
        else:
            final, disagreements = _consolidate_without_moderator(raised, challenges)
            summary = "El moderador no pudo ejecutarse; se muestran los hallazgos de los especialistas sin consolidar."
            notes.append("se usó la consolidación automática (sin moderador)")
    else:
        summary = "Los especialistas no reportaron hallazgos sobre este plan."

    return CommitteeResult(
        plan=plan,
        raised=raised,
        challenges=challenges,
        summary=summary,
        final=final,
        disagreements=disagreements,
        accepted_risks=accepted,
        moderator_ran=moderator_ran,
        usage=usage,
        tokens_used=budget.used,
        tokens_max=config.max_total_tokens,
        rounds_run=rounds_run,
        notes=notes,
        truncated_plan=truncated,
    )
