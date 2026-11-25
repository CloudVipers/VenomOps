"""``venom-eval``: list and materialize corpus cases, run the experiments and summarize their records.

Experiments that call a model (E1, E2) need an explicit ``--model-id`` **and** ``--confirm`` (they cost money);
without ``--confirm`` they only print what they would run. E3 reads saved outputs and E4 is deterministic.

``--endpoint-url`` (or ``AWS_ENDPOINT_URL_BEDROCK_RUNTIME`` / ``AWS_ENDPOINT_URL``) points E1/E2 at a Bedrock-compatible
emulator, e.g. LocalStack backed by a local model, to rehearse a run for free. Every record stores the endpoint, so a
rehearsal can never be mistaken for a Bedrock result.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from . import BENCHMARK, REPO_ROOT, corpus, e1, e2, e3, e4, results

ENDPOINT_ENV = ("AWS_ENDPOINT_URL_BEDROCK_RUNTIME", "AWS_ENDPOINT_URL")


def endpoint_url(flag: str | None) -> str | None:
    """The endpoint boto3 will call: the flag, else the same environment variables boto3 itself honors."""
    if flag:
        return flag
    return next((os.environ[name] for name in ENDPOINT_ENV if os.environ.get(name)), None)


def run_meta(model_id: str, endpoint: str | None) -> dict[str, str]:
    return {"model_id": model_id, "endpoint": endpoint or "bedrock"}


def _client(region: str | None, endpoint: str | None) -> Any:
    import boto3

    session = boto3.Session(region_name=region)
    if session.get_credentials() is None:
        raise SystemExit("no AWS credentials available (configure AWS_PROFILE or environment variables)")
    return session.client("bedrock-runtime", endpoint_url=endpoint)


def _select(target: str, contains: str | None, limit: int | None) -> list[corpus.Case]:
    cases = [c for c in corpus.enumerate_cases(corpus.load_manifest()) if c.target == target]
    if contains:
        cases = [c for c in cases if contains in c.id]
    return cases[:limit] if limit else cases


def _gate(args: argparse.Namespace, n_cases: int, per_case: int) -> str | None:
    if not args.model_id or not args.model_id.strip():
        raise SystemExit("an explicit --model-id is required (there is no default model)")
    endpoint = endpoint_url(args.endpoint_url)
    calls = n_cases * per_case * args.repetitions
    print(
        f"{n_cases} casos × {per_case} × {args.repetitions} repeticiones = {calls} ejecuciones contra {args.model_id}"
    )
    if endpoint:
        print(f"endpoint {endpoint}: no es Bedrock; la corrida es un ensayo y no cuenta como resultado de la tesis")
    if not args.confirm:
        raise SystemExit("dry run: añade --confirm para llamar al modelo (tiene costo)")
    return endpoint


def cmd_cases(args: argparse.Namespace) -> int:
    cases = corpus.enumerate_cases(corpus.load_manifest())
    if args.target:
        cases = [c for c in cases if c.target == args.target]
    for c in cases:
        print(c.id)
    print(f"# {len(cases)} casos", file=sys.stderr)
    return 0


def cmd_materialize(args: argparse.Namespace) -> int:
    manifest = corpus.load_manifest()
    case = next((c for c in corpus.enumerate_cases(manifest) if c.id == args.case), None)
    if case is None:
        raise SystemExit(f"unknown case {args.case!r} (see: venom-eval cases)")
    out: Path = args.out
    out.mkdir(parents=True, exist_ok=False)
    if case.target == "pr-agent":
        _, finding = corpus.materialize_pr_agent(manifest, case, out)
        (out / "finding.json").write_text(json.dumps(finding, ensure_ascii=False, indent=2), encoding="utf-8")
    elif case.target == "arch-committee":
        plan, context = corpus.materialize_committee(manifest, case)
        (out / "plan.json").write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
        if context:
            (out / "context.json").write_text(json.dumps(context, ensure_ascii=False, indent=2), encoding="utf-8")
    else:
        finding = corpus.materialize_explain(manifest, case)
        (out / "finding.json").write_text(json.dumps([finding], ensure_ascii=False, indent=2), encoding="utf-8")
    print(out)
    return 0


def cmd_e1(args: argparse.Namespace) -> int:
    manifest = corpus.load_manifest()
    cases = _select("pr-agent", args.contains, args.limit)
    conditions = args.conditions or list(e1.CONDITIONS)
    endpoint = _gate(args, len(cases), len(conditions))
    client = _client(args.region, endpoint)
    out = args.out or results.new_run_dir("e1")
    writer = results.ResultsWriter(out, "e1", run_meta(args.model_id, endpoint))
    for rep in range(args.repetitions):
        for case in cases:
            for condition in conditions:
                with tempfile.TemporaryDirectory(prefix="venom-eval-") as tmp:
                    repo, finding = corpus.materialize_pr_agent(manifest, case, Path(tmp))
                    existing = e1.existing_tf_files(repo)
                    trace = e1.run_case(condition, client, args.model_id, repo, finding)
                    verdict = e1.judge(case.goal, trace, existing)
                writer.write({**e1.record_for(case, trace, verdict), "repetition": rep})
                print(f"[{rep}] {condition:12} {case.id}: {verdict}")
    print(f"registros: {writer.path}")
    return 0


def cmd_e2(args: argparse.Namespace) -> int:
    from arch_committee.llm import BedrockLLM

    manifest = corpus.load_manifest()
    cases = _select("arch-committee", args.contains, args.limit)
    endpoint = _gate(args, len(cases), len(args.rounds) * len(args.defenses) * 2)
    llm = BedrockLLM(_client(args.region, endpoint), args.model_id)
    out = args.out or results.new_run_dir("e2")
    writer = results.ResultsWriter(out, "e2", run_meta(args.model_id, endpoint))
    bases = {b["id"]: b for b in manifest["bases"] if b["target"] == "arch-committee"}
    for rep in range(args.repetitions):
        for rounds in args.rounds:
            for defense in args.defenses:
                clean: dict[str, list[dict[str, Any]]] = {}
                for case in cases:
                    base = bases[case.base]
                    if case.base not in clean:
                        plan = json.loads((REPO_ROOT / base["plan"]).read_text(encoding="utf-8"))
                        clean[case.base], _, _ = e2.run_review(llm, plan, [], rounds, defense)
                    plan, context = corpus.materialize_committee(manifest, case)
                    attacked, tokens, suspicions = e2.run_review(llm, plan, context, rounds, defense)
                    verdict = e2.judge(case.goal, clean[case.base], attacked, base["resource"], suspicions)
                    writer.write(
                        {
                            "case_id": case.id,
                            "goal": case.goal,
                            "technique": case.technique,
                            "vector": case.vector,
                            "rounds": rounds,
                            "defense": defense,
                            "repetition": rep,
                            "verdict": verdict,
                            "tokens": tokens,
                            "clean": clean[case.base],
                            "attacked": attacked,
                        }
                    )
                    print(f"[{rep}] rounds={rounds} defense={defense} {case.id}: {verdict}")
    print(f"registros: {writer.path}")
    return 0


def cmd_e3(args: argparse.Namespace) -> int:
    labels = e3.load_labels(args.labels)
    systems: dict[str, dict[str, list[e3.Prediction]]] = {}
    for spec in args.system:
        name, _, rest = spec.partition("=")
        adapter, _, directory = rest.partition(":")
        if not name or adapter not in e3.ADAPTERS or not directory:
            raise SystemExit(f"--system must look like name=adapter:directory with adapter in {sorted(e3.ADAPTERS)}")
        systems[name] = e3.load_system(Path(directory), adapter)
    for spec in args.union or []:
        parts = spec.split("+")
        systems[spec] = e3.union(*(systems[p] for p in parts))
    scored = {name: e3.score(preds, labels) for name, preds in systems.items()}
    record = {"labels": str(args.labels), "systems": scored}
    if args.out:
        results.ResultsWriter(args.out, "e3", {"model_id": args.model_id or "n/a"}).write(record)
    print(results.summarize_e3([record]))
    return 0


def cmd_e4(args: argparse.Namespace) -> int:
    record = {"corpus": str(e4.CORPUS.relative_to(REPO_ROOT)), "redactors": e4.run_all()}
    if args.out:
        results.ResultsWriter(args.out, "e4", {"model_id": "n/a"}).write(record)
    print(results.summarize_e4([record]))
    return 0


def cmd_summarize(args: argparse.Namespace) -> int:
    print(results.summarize(results.load(args.directory)))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="venom-eval", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("cases", help="list the injection corpus cases")
    p.add_argument("--target", choices=["pr-agent", "arch-committee", "venom-doctor-explain"])
    p.set_defaults(func=cmd_cases)

    p = sub.add_parser("materialize", help="write one case to a directory (e.g. to run it by hand)")
    p.add_argument("--case", required=True)
    p.add_argument("--out", type=Path, required=True)
    p.set_defaults(func=cmd_materialize)

    for name, func, help_text in (
        ("e1", cmd_e1, "E1: integrity of the actions (pr-agent, Bedrock)"),
        ("e2", cmd_e2, "E2: integrity of the judgment (arch-committee, Bedrock)"),
    ):
        p = sub.add_parser(name, help=help_text)
        p.add_argument("--model-id", required=True, help="explicit Bedrock model id (no default)")
        p.add_argument("--region")
        p.add_argument(
            "--endpoint-url", help="Bedrock-compatible emulator for a free rehearsal (recorded in each record)"
        )
        p.add_argument("--repetitions", type=int, default=5)
        p.add_argument("--contains", help="only cases whose id contains this text")
        p.add_argument("--limit", type=int)
        p.add_argument("--out", type=Path)
        p.add_argument("--confirm", action="store_true", help="really call the model (costs money)")
        if name == "e1":
            p.add_argument("--conditions", nargs="+", choices=list(e1.CONDITIONS))
        else:
            p.add_argument("--rounds", type=int, nargs="+", choices=[1, 2], default=[1, 2])
            p.add_argument("--defenses", nargs="+", choices=list(e2.DEFENSES), default=list(e2.DEFENSES))
        p.set_defaults(func=func)

    p = sub.add_parser("e3", help="E3: detection compared, from saved outputs")
    p.add_argument("--labels", type=Path, default=BENCHMARK / "iac" / "labels.json")
    p.add_argument("--system", action="append", required=True, help="name=adapter:directory (checkov|trivy|findings)")
    p.add_argument("--union", action="append", help="e.g. committee+checkov")
    p.add_argument("--model-id", help="model used by the committee outputs, for the record")
    p.add_argument("--out", type=Path)
    p.set_defaults(func=cmd_e3)

    p = sub.add_parser("e4", help="E4: confidentiality (deterministic)")
    p.add_argument("--out", type=Path)
    p.set_defaults(func=cmd_e4)

    p = sub.add_parser("summarize", help="tables with Wilson intervals and McNemar from saved records")
    p.add_argument("directory", type=Path)
    p.set_defaults(func=cmd_summarize)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
