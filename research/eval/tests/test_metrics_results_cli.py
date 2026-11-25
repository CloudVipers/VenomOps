from __future__ import annotations

import json
from pathlib import Path

import pytest

from venom_eval import cli, metrics, results


def test_wilson_matches_reference_values() -> None:
    p, lo, hi = metrics.wilson(5, 10)
    assert p == 0.5 and lo == pytest.approx(0.2366, abs=1e-4) and hi == pytest.approx(0.7634, abs=1e-4)
    p, lo, hi = metrics.wilson(0, 10)
    assert (p, lo) == (0.0, 0.0) and hi == pytest.approx(0.2775, abs=1e-4)
    assert metrics.wilson(0, 0) == (0.0, 0.0, 1.0)
    with pytest.raises(ValueError):
        metrics.wilson(11, 10)


def test_mcnemar_exact() -> None:
    assert metrics.mcnemar_exact(0, 6) == pytest.approx(0.03125)
    assert metrics.mcnemar_exact(3, 3) == 1.0
    assert metrics.mcnemar_exact(0, 0) == 1.0


def test_prf_and_bootstrap_are_deterministic() -> None:
    assert metrics.prf(2, 2, 0) == {"precision": 0.5, "recall": 1.0, "f1": pytest.approx(2 / 3)}
    assert metrics.prf(0, 0, 0)["f1"] == 0.0
    values = [0.0, 1.0, 1.0, 0.5, 0.75]
    first = metrics.bootstrap_ci(values, resamples=2000)
    assert first == metrics.bootstrap_ci(values, resamples=2000)
    assert first[1] <= first[0] <= first[2]


def test_records_roundtrip_and_summaries(tmp_path: Path) -> None:
    writer = results.ResultsWriter(tmp_path, "e1", {"model_id": "m"})
    for cond, succeeded in (("prompt-only", True), ("prompt+code", False)):
        writer.write(
            {
                "case_id": "c1",
                "goal": "read-secret",
                "condition": cond,
                "repetition": 0,
                "verdict": {"attempted": True, "succeeded": succeeded},
            }
        )
    records = results.load(tmp_path)
    assert len(records) == 2
    meta = records[0]
    assert meta["experiment"] == "e1" and meta["temperature"] == 0.0 and meta["git_commit"]
    assert "pr-agent" in meta["prompt_versions"] and "arch-committee.security" in meta["prompt_versions"]
    table = results.summarize(records)
    assert "| m | prompt-only | read-secret | 1/1 = 100.0%" in table and "McNemar" in table


def test_cli_lists_cases(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["cases", "--target", "venom-doctor-explain"]) == 0
    assert len(capsys.readouterr().out.strip().splitlines()) == 5


def test_cli_does_not_call_the_model_without_confirm(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="--confirm"):
        cli.main(["e1", "--model-id", "some-model", "--limit", "2", "--repetitions", "1"])
    assert "2 casos" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="explicit --model-id"):
        cli.main(["e2", "--model-id", "  ", "--limit", "1"])


def test_cli_e4_writes_a_record(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["e4", "--out", str(tmp_path)]) == 0
    assert "pr-agent" in capsys.readouterr().out
    record = json.loads((tmp_path / "e4.jsonl").read_text().splitlines()[0])
    assert record["redactors"]["arch-committee"]["secrets"] == 22


def test_cli_materializes_a_case(tmp_path: Path) -> None:
    out = tmp_path / "case"
    assert (
        cli.main(
            [
                "materialize",
                "--case",
                "arch-committee/public-bucket/context-finding/suppress-finding.direct",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    assert (out / "plan.json").exists() and (out / "context.json").exists()


def test_endpoint_flag_wins_over_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_ENDPOINT_URL_BEDROCK_RUNTIME", raising=False)
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    assert cli.endpoint_url(None) is None
    monkeypatch.setenv("AWS_ENDPOINT_URL", "http://localhost:4566")
    assert cli.endpoint_url(None) == "http://localhost:4566"
    monkeypatch.setenv("AWS_ENDPOINT_URL_BEDROCK_RUNTIME", "http://localhost:4567")
    assert cli.endpoint_url(None) == "http://localhost:4567"
    assert cli.endpoint_url("http://127.0.0.1:9000") == "http://127.0.0.1:9000"


def test_records_say_which_endpoint_answered(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("AWS_ENDPOINT_URL_BEDROCK_RUNTIME", raising=False)
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    assert cli.run_meta("m", None) == {"model_id": "m", "endpoint": "bedrock"}
    assert cli.run_meta("m", "http://localhost:4566") == {"model_id": "m", "endpoint": "http://localhost:4566"}


def test_dry_run_warns_about_a_non_bedrock_endpoint(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit, match="--confirm"):
        cli.main(
            [
                "e1",
                "--model-id",
                "llama3.2",
                "--endpoint-url",
                "http://localhost:4566",
                "--limit",
                "1",
                "--repetitions",
                "1",
            ]
        )
    out = capsys.readouterr().out
    assert "http://localhost:4566" in out
    assert "no es Bedrock" in out


def test_e1_summary_compares_spotlighting_on_attempts() -> None:
    records = []
    for case, (raw, spot) in enumerate(((True, False), (True, False), (True, True))):
        for cond, attempted in (("prompt-only-raw", raw), ("prompt-only", spot)):
            records.append(
                {
                    "model_id": "m",
                    "case_id": f"c{case}",
                    "repetition": 0,
                    "condition": cond,
                    "goal": "read-secret",
                    "verdict": {"attempted": attempted, "succeeded": attempted},
                }
            )
    table = results.summarize_e1(records)
    assert "McNemar (intento, sin delimitación vs. con delimitación): b=2, c=0" in table
