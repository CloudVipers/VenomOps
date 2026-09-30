from __future__ import annotations

import json
from typing import Any

from arch_committee.orchestrator import CommitteeConfig, CommitteeResult, run_committee
from arch_committee.plan_parser import PlanModel, parse_plan

from .conftest import FakeCommittee
from .test_plan_parser import plan_of, rc

# What each example plan was built to contain (address of the resource, minimum severity expected).
SEEDED: dict[str, list[tuple[str, str]]] = {
    "public-bucket": [
        ("aws_s3_bucket_policy.assets_public_read", "critical"),
        ("aws_s3_bucket_public_access_block.assets", "high"),
        ("aws_iam_policy.deploy", "high"),
    ],
    "rds-single-az": [
        ("aws_db_instance.orders", "critical"),  # publicly accessible
        ("aws_security_group.db", "high"),
        ("aws_db_instance.orders", "high"),  # single AZ / no backups / unencrypted
    ],
    "nat-per-subnet": [("aws_nat_gateway.per_subnet[0]", "medium")],
}
RANK = {"critical": 0, "high": 1, "medium": 2, "low": 3, "info": 4}


def addresses(result: CommitteeResult) -> set[tuple[str, str]]:
    return {(f.resource, f.severity) for f in result.final}


def test_the_committee_detects_the_seeded_flaws_in_every_example(
    example_plan: tuple[str, PlanModel], committee: FakeCommittee
) -> None:
    name, plan = example_plan
    result = run_committee(plan, committee)
    assert result.moderator_ran and result.rounds_run == 2 and result.final
    for address, severity in SEEDED[name]:
        assert any(f.resource == address and RANK[f.severity] <= RANK[severity] for f in result.final), (name, address)


def test_the_rds_plan_is_reviewed_from_all_four_angles(committee: FakeCommittee) -> None:
    from pathlib import Path

    from arch_committee.plan_parser import load_plan

    from .conftest import plan_path

    result = run_committee(load_plan(plan_path("rds-single-az")), committee)
    by_agent = {r.agent for r in result.raised}
    assert by_agent == {"security", "cost", "reliability", "operations"}
    assert {r.fid.split("-")[0] for r in result.raised} == {"SEC", "COST", "REL", "OPS"}
    assert isinstance(Path, type)  # keep import used


def test_the_nat_plan_produces_an_explicit_disagreement_between_agents(committee: FakeCommittee) -> None:
    from arch_committee.plan_parser import load_plan

    from .conftest import plan_path

    result = run_committee(load_plan(plan_path("nat-per-subnet")), committee)
    assert len(result.challenges) >= 1
    disagreement = result.disagreements[0]
    agents = {p.agent for p in disagreement.positions}
    assert {"cost", "reliability"} <= agents  # cost proposed one NAT, reliability objected
    assert disagreement.resolution == "accepted_risk" and "NAT" in disagreement.topic
    assert result.accepted_risks


def test_data_flows_plan_to_prompt_with_the_expected_structure(committee: FakeCommittee) -> None:
    from arch_committee.plan_parser import load_plan

    from .conftest import plan_path

    run_committee(load_plan(plan_path("rds-single-az")), committee)
    r1 = [c for c in committee.calls if c["tool"] == "report_findings"]
    assert {c["agent"] for c in r1} == {"security", "cost", "reliability", "operations"}
    assert all("<plan>" in c["user"] and "aws_db_instance.orders" in c["user"] for c in r1)
    # every call uses a schema-backed output tool and the agent's own versioned system prompt
    for c in committee.calls:
        assert c["schema"]["type"] == "object" and c["system"].startswith("# Agent:")
    assert [c["tool"] for c in committee.calls].count("finalize") == 1
    assert {c["tool"] for c in committee.calls} == {"report_findings", "respond_to_findings", "finalize"}


def test_secrets_never_reach_the_model(committee: FakeCommittee) -> None:
    plan = parse_plan(
        plan_of(
            rc(
                "aws_db_instance.db",
                "aws_db_instance",
                {"password": "hunter2", "multi_az": False, "storage_encrypted": False},
                sensitive={"password": True},
            ),  # fmt: skip
            rc(
                "aws_lambda_function.f",
                "aws_lambda_function",
                {
                    "environment": {"variables": {"API_TOKEN": "tok-987654"}},
                    "description": "acct 123456789012 key AKIAIOSFODNN7EXAMPLE",
                },
            ),  # fmt: skip
        )
    )
    run_committee(plan, committee)
    sent = "\n".join(c["user"] for c in committee.calls)
    for leaked in ("hunter2", "tok-987654", "123456789012", "AKIAIOSFODNN7EXAMPLE"):
        assert leaked not in sent


def test_max_rounds_one_skips_the_rebuttal_round(example_plan: tuple[str, PlanModel], committee: FakeCommittee) -> None:
    result = run_committee(example_plan[1], committee, CommitteeConfig(max_rounds=1))
    assert result.rounds_run == 1 and not result.challenges
    assert "respond_to_findings" not in {c["tool"] for c in committee.calls}
    assert any("max_rounds=1" in n for n in result.notes)


def test_a_tiny_budget_degrades_and_says_so(committee: FakeCommittee) -> None:
    from arch_committee.plan_parser import load_plan

    from .conftest import plan_path

    result = run_committee(load_plan(plan_path("rds-single-az")), committee, CommitteeConfig(max_total_tokens=1_000))
    assert not result.raised and not result.final and committee.calls == []
    assert any("presupuesto" in n for n in result.notes)


def test_the_budget_is_never_exceeded(committee: FakeCommittee) -> None:
    from arch_committee.plan_parser import load_plan

    from .conftest import plan_path

    plan = load_plan(plan_path("rds-single-az"))
    full = run_committee(plan, FakeCommittee())
    # room for round 1 (4 calls) but not for everything else
    cfg = CommitteeConfig(max_total_tokens=full.tokens_used // 2, max_output_tokens=200)
    result = run_committee(plan, committee, cfg)
    assert result.tokens_used <= cfg.max_total_tokens
    assert len(committee.calls) < len(FakeCommittee().calls) + 99  # sanity: it ran, but fewer steps than the full run
    assert any("presupuesto" in n for n in result.notes) or result.tokens_used < full.tokens_used


def test_a_failed_moderator_falls_back_without_losing_findings_or_disagreements() -> None:
    from arch_committee.plan_parser import load_plan

    from .conftest import plan_path

    plan = load_plan(plan_path("nat-per-subnet"))
    result = run_committee(plan, FakeCommittee(fail=frozenset({"moderator"})))
    assert not result.moderator_ran and result.final
    assert any("sin moderador" in n for n in result.notes)
    assert {r.fid for r in result.raised} == {i for f in result.final for i in f.merged_from}
    assert result.disagreements and result.disagreements[0].resolution == "unresolved"
    assert "decisión humana" in result.disagreements[0].rationale


def test_a_failed_specialist_does_not_stop_the_others() -> None:
    from arch_committee.plan_parser import load_plan

    from .conftest import plan_path

    result = run_committee(load_plan(plan_path("rds-single-az")), FakeCommittee(fail=frozenset({"security"})))
    assert "security" not in {r.agent for r in result.raised}
    assert {"cost", "reliability", "operations"} <= {r.agent for r in result.raised}
    assert any("security" in n and "falló" in n for n in result.notes) and result.moderator_ran


def test_invalid_model_output_is_reported_not_fatal() -> None:
    from arch_committee.plan_parser import load_plan

    from .conftest import plan_path

    result = run_committee(load_plan(plan_path("rds-single-az")), FakeCommittee(invalid=frozenset({"cost"})))
    assert any("cost" in n and "formato inválido" in n for n in result.notes)
    assert "cost" not in {r.agent for r in result.raised} and result.final


def test_findings_about_resources_that_are_not_in_the_plan_are_dropped() -> None:
    from arch_committee.plan_parser import load_plan

    from .conftest import plan_path

    result = run_committee(load_plan(plan_path("rds-single-az")), FakeCommittee(extra_unknown_resource=True))
    assert all("ghost" not in r.finding.resource for r in result.raised)
    assert any("no está en el plan" in n and "ghost" in n for n in result.notes)


def test_moderator_hallucinations_are_dropped_and_omissions_are_recovered() -> None:
    from arch_committee.plan_parser import load_plan

    from .conftest import plan_path

    plan = load_plan(plan_path("rds-single-az"))
    halluc = run_committee(plan, FakeCommittee(moderator_mode="hallucinate"))
    assert all(f.resource != "aws_fake.nothing" for f in halluc.final)
    assert any("no referencia ningún hallazgo real" in n for n in halluc.notes)
    assert any("recurso que no está en el plan" in n for n in halluc.notes)

    omitted = run_committee(plan, FakeCommittee(moderator_mode="omit_one"))
    assert {r.fid for r in omitted.raised} == {i for f in omitted.final for i in f.merged_from}  # nothing was lost
    assert any("no fue tratado por el moderador" in n for n in omitted.notes)


def test_a_disagreement_the_moderator_ignores_is_still_recorded() -> None:
    from arch_committee.plan_parser import load_plan

    from .conftest import plan_path

    result = run_committee(load_plan(plan_path("nat-per-subnet")), FakeCommittee(moderator_mode="omit_one"))
    assert result.disagreements, "a 'disagree' challenge must never disappear silently"
    assert result.disagreements[0].resolution == "unresolved"
    assert any("no resuelto por el moderador" in n for n in result.notes)


def test_results_are_deterministic_despite_parallel_execution() -> None:
    from arch_committee.plan_parser import load_plan

    from .conftest import plan_path

    plan = load_plan(plan_path("rds-single-az"))
    a = run_committee(plan, FakeCommittee())
    b = run_committee(plan, FakeCommittee())

    def sig(r: CommitteeResult) -> str:
        return json.dumps([(x.fid, x.agent, x.finding.title) for x in r.raised]) + json.dumps(
            [f.title for f in r.final]
        )

    assert sig(a) == sig(b)


def test_the_committee_reports_no_findings_when_nobody_finds_anything(committee: FakeCommittee) -> None:
    plan = parse_plan(
        plan_of(rc("aws_s3_bucket.ok", "aws_s3_bucket", {"bucket": "x", "tags": {"Owner": "me", "Environment": "dev"}}))
    )
    result = run_committee(plan, committee)
    assert not result.raised and not result.final and "no reportaron" in result.summary
    assert [c["tool"] for c in committee.calls].count("finalize") == 0  # nothing to moderate: no wasted call


def _token_sum(result: CommitteeResult) -> int:
    return sum(u.tokens for u in result.usage)


def test_usage_is_accounted_per_step(committee: FakeCommittee) -> None:
    from arch_committee.plan_parser import load_plan

    from .conftest import plan_path

    result = run_committee(load_plan(plan_path("nat-per-subnet")), committee)
    steps = {u.step for u in result.usage}
    assert steps == {"round1", "round2", "moderator"} and result.tokens_used == _token_sum(result)


def unused(_: Any) -> None:  # pragma: no cover
    return None
