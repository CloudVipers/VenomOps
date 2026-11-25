from __future__ import annotations

import base64
import json
from collections import Counter
from pathlib import Path
from typing import Any

import pytest
from arch_committee.plan_parser import parse_plan, render_for_llm
from findings_schema import Finding
from pr_agent import hcl

from venom_eval import REPO_ROOT, corpus


def test_the_matrix_has_the_documented_size(cases: list[corpus.Case]) -> None:
    assert len(cases) == 145
    assert Counter(c.target for c in cases) == {"pr-agent": 80, "arch-committee": 60, "venom-doctor-explain": 5}
    assert len({c.id for c in cases}) == len(cases)


def test_enumeration_is_deterministic(manifest: dict[str, Any]) -> None:
    assert corpus.enumerate_cases(manifest) == corpus.enumerate_cases(corpus.load_manifest())


def test_every_payload_is_synthetic_and_well_formed(manifest: dict[str, Any]) -> None:
    for p in manifest["payloads"]:
        assert p["goal"] in manifest["goals"] and p["technique"] in manifest["techniques"]
        assert 1 <= len(p["parts"]) <= 2 and all(part.strip() for part in p["parts"])
        assert (len(p["parts"]) == 2) == (p["technique"] == "split")
        for url in ("http://", "https://"):
            for part in p["parts"]:
                if url in part:
                    assert "attacker.example.com" in part
    encoded = [p for p in manifest["payloads"] if p["technique"] == "encoded"]
    direct = {p["goal"]: p["parts"][0] for p in manifest["payloads"] if p["technique"] == "direct"}
    for p in encoded:
        assert base64.b64decode(p["parts"][0].rsplit(" ", 1)[1]).decode() == direct[p["goal"]]


def test_an_unknown_goal_is_rejected(manifest: dict[str, Any]) -> None:
    broken = json.loads(json.dumps(manifest))
    broken["payloads"][0]["goal"] = "nope"
    with pytest.raises(corpus.CorpusError):
        corpus.enumerate_cases(broken)


@pytest.mark.parametrize("vector", ["hcl-comment", "tag-value", "variable-description", "finding-evidence"])
def test_pr_agent_cases_plant_the_payload_and_stay_valid(
    vector: str, cases: list[corpus.Case], manifest: dict[str, Any], tmp_path: Path
) -> None:
    for i, case in enumerate(c for c in cases if c.target == "pr-agent" and c.vector == vector):
        repo, finding = corpus.materialize_pr_agent(manifest, case, tmp_path / str(i))
        texts = {p.name: p.read_text(encoding="utf-8") for p in repo.glob("*.tf")}
        for text in texts.values():
            hcl.parse(text)
        Finding.from_dict(finding)
        where = json.dumps(finding, ensure_ascii=False) if vector == "finding-evidence" else "\n".join(texts.values())
        for part in case.parts:
            probe = part.split(" ", 3)[-1][:20]
            assert probe in where.replace('\\"', '"'), f"{case.id}: payload not planted"
        for name, content in corpus.CANARIES.items():
            assert (repo / name).read_text(encoding="utf-8") == content


def test_pr_agent_materialization_writes_only_inside_the_destination(
    cases: list[corpus.Case], manifest: dict[str, Any], tmp_path: Path
) -> None:
    dest = tmp_path / "case"
    corpus.materialize_pr_agent(manifest, cases[0], dest)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["case"]


def test_committee_cases_keep_the_plan_parseable_and_carry_the_payload(
    cases: list[corpus.Case], manifest: dict[str, Any]
) -> None:
    for case in (c for c in cases if c.target == "arch-committee"):
        plan, context = corpus.materialize_committee(manifest, case)
        rendered, _ = render_for_llm(parse_plan(plan))
        if case.vector == "context-finding":
            assert len(context) == 1
            finding = Finding.from_dict(context[0])
            assert [e.detail for e in finding.evidence] == list(case.parts)
        else:
            assert not context
            for part in case.parts:
                assert part[:20] in rendered, f"{case.id}: payload missing from what the agents see"


def test_committee_materialization_does_not_mutate_the_example(
    cases: list[corpus.Case], manifest: dict[str, Any]
) -> None:
    case = next(c for c in cases if c.target == "arch-committee" and c.vector == "tag-value")
    before = (REPO_ROOT / "examples/plans/public-bucket.json").read_text(encoding="utf-8")
    corpus.materialize_committee(manifest, case)
    corpus.materialize_committee(manifest, case)
    assert (REPO_ROOT / "examples/plans/public-bucket.json").read_text(encoding="utf-8") == before


def test_explain_cases_append_log_lines(cases: list[corpus.Case], manifest: dict[str, Any]) -> None:
    for case in (c for c in cases if c.target == "venom-doctor-explain"):
        finding = corpus.materialize_explain(manifest, case)
        Finding.from_dict(finding)
        assert [e["detail"] for e in finding["evidence"][-len(case.parts) :]] == list(case.parts)


def test_materializers_refuse_cases_of_another_target(
    cases: list[corpus.Case], manifest: dict[str, Any], tmp_path: Path
) -> None:
    committee_case = next(c for c in cases if c.target == "arch-committee")
    with pytest.raises(corpus.CorpusError):
        corpus.materialize_pr_agent(manifest, committee_case, tmp_path)
    with pytest.raises(corpus.CorpusError):
        corpus.materialize_explain(manifest, committee_case)
