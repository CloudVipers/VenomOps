from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from arch_committee.plan_parser import UNKNOWN, PlanError, load_plan, parse_plan, render_for_llm

from .conftest import plan_path


def rc(address: str, rtype: str, after: dict[str, Any], *, actions: list[str] | None = None, sensitive: Any = None,
       index: Any = None, module: str | None = None) -> dict[str, Any]:  # fmt: skip
    change: dict[str, Any] = {"actions": actions or ["create"], "before": None, "after": after, "after_unknown": {}}
    if sensitive is not None:
        change["after_sensitive"] = sensitive
    entry: dict[str, Any] = {"address": address_of(address, index, module), "type": rtype, "name": address.split(".")[-1],
                             "provider_name": "registry.terraform.io/hashicorp/aws", "change": change}  # fmt: skip
    if index is not None:
        entry["index"] = index
    if module:
        entry["module_address"] = module
    return entry


def address_of(address: str, index: Any, module: str | None) -> str:
    base = f"{address}[{index}]" if index is not None else address
    return f"{module}.{base}" if module else base


def plan_of(*changes: dict[str, Any], configuration: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"format_version": "1.2", "terraform_version": "1.9.0", "resource_changes": list(changes),
            "configuration": configuration or {"root_module": {}}}  # fmt: skip


def test_example_plans_parse_with_the_expected_shape() -> None:
    bucket = load_plan(plan_path("public-bucket"))
    assert sorted(bucket.type_counts) == [
        "aws_iam_policy",
        "aws_s3_bucket",
        "aws_s3_bucket_policy",
        "aws_s3_bucket_public_access_block",
    ]
    assert bucket.action_counts == {"create": 4}

    rds = load_plan(plan_path("rds-single-az"))
    db = rds.find("aws_db_instance")[0]
    assert db.attributes["multi_az"] is False and db.attributes["publicly_accessible"] is True
    assert db.attributes["backup_retention_period"] == 0  # falsy values are kept: they carry meaning

    nat = load_plan(plan_path("nat-per-subnet"))
    assert len(nat.find("aws_nat_gateway")) == 3 and len(nat.resources) == 20


def test_dependencies_come_from_the_configuration_references() -> None:
    nat = load_plan(plan_path("nat-per-subnet"))
    gw = nat.by_address("aws_nat_gateway.per_subnet[1]")
    assert gw is not None
    assert set(gw.depends_on) >= {"aws_eip.nat", "aws_subnet.public", "aws_internet_gateway.main"}
    assert gw.base_address == "aws_nat_gateway.per_subnet" and gw.index == "1"
    rds = load_plan(plan_path("rds-single-az"))
    assert set(rds.find("aws_db_instance")[0].depends_on) == {"aws_db_subnet_group.orders", "aws_security_group.db"}


def test_sensitive_values_are_masked_using_terraform_markers() -> None:
    plan = plan_of(rc("aws_db_instance.db", "aws_db_instance", {"password": "hunter2", "username": "admin", "engine": "postgres"},
                      sensitive={"password": True}))  # fmt: skip
    attrs = parse_plan(plan).resources[0].attributes
    assert attrs["password"] == "[SENSITIVE]" and attrs["username"] == "admin"
    assert "hunter2" not in json.dumps(attrs)


def test_nested_sensitive_trees_and_lists() -> None:
    plan = plan_of(rc("aws_x.y", "aws_x", {"creds": [{"key": "k1", "id": "a"}, {"key": "k2", "id": "b"}]},
                      sensitive={"creds": [{"key": True}, {"key": True}]}))  # fmt: skip
    creds = parse_plan(plan).resources[0].attributes["creds"]
    assert [c["key"] for c in creds] == ["[SENSITIVE]", "[SENSITIVE]"] and [c["id"] for c in creds] == ["a", "b"]


def test_secret_looking_keys_and_strings_are_redacted_even_without_markers() -> None:
    plan = plan_of(rc("aws_lambda_function.f", "aws_lambda_function", {
        "environment": {"variables": {"DB_PASSWORD": "s3cr3t!", "API_TOKEN": "tok-123456"}},
        "description": "uses AKIAIOSFODNN7EXAMPLE in account 123456789012",
    }))  # fmt: skip
    dumped = json.dumps(parse_plan(plan).resources[0].attributes)
    for leaked in ("s3cr3t!", "tok-123456", "AKIAIOSFODNN7EXAMPLE", "123456789012"):
        assert leaked not in dumped


def test_noise_is_dropped_but_meaningful_falsy_values_stay() -> None:
    plan = plan_of(
        rc(
            "aws_x.y",
            "aws_x",
            {"tags_all": {"a": "b"}, "id": "i-1", "empty": [], "none": None, "blank": "", "on": False, "n": 0},
        )
    )
    assert parse_plan(plan).resources[0].attributes == {"on": False, "n": 0}


def test_noop_and_data_reads_are_not_reviewed() -> None:
    plan = plan_of(rc("aws_x.keep", "aws_x", {"a": 1}, actions=["no-op"]), rc("aws_x.read", "aws_x", {"a": 1}, actions=["read"]),
                   rc("aws_x.new", "aws_x", {"a": 1}), rc("aws_x.gone", "aws_x", {}, actions=["delete"]))  # fmt: skip
    model = parse_plan(plan)
    assert [r.address for r in model.resources] == ["aws_x.gone", "aws_x.new"]
    assert model.action_counts == {"create": 1, "delete": 1}


def test_module_resources_and_dependencies() -> None:
    configuration = {"root_module": {"module_calls": {"net": {"module": {"resources": [
        {"address": "aws_subnet.a", "expressions": {"vpc_id": {"references": ["aws_vpc.main.id", "aws_vpc.main"]}}}]}}}}}  # fmt: skip
    plan = plan_of(
        rc("aws_subnet.a", "aws_subnet", {"cidr_block": "10.0.0.0/24"}, module="module.net"),
        configuration=configuration,
    )
    res = parse_plan(plan).resources[0]
    assert res.address == "module.net.aws_subnet.a" and res.module == "module.net"


@pytest.mark.parametrize(
    ("data", "message"),
    [
        ({}, "resource_changes"),
        ({"foo": 1}, "resource_changes"),
        ({"resource_changes": [], "errored": True}, "errored"),
    ],
)
def test_unusable_plans_are_rejected(data: dict[str, Any], message: str) -> None:
    with pytest.raises(PlanError, match=message):
        parse_plan(data)


def test_load_plan_reports_invalid_json(tmp_path: Path) -> None:
    bad = tmp_path / "plan.json"
    bad.write_text("{oops")
    with pytest.raises(PlanError, match="not valid JSON"):
        load_plan(bad)


def test_render_is_deterministic_and_compact() -> None:
    model = load_plan(plan_path("rds-single-az"))
    a, truncated = render_for_llm(model)
    assert a == render_for_llm(model)[0] and not truncated
    doc = json.loads(a)
    assert doc["summary"]["actions"] == {"create": 6}
    assert len(a) < 4000


def test_large_plans_degrade_instead_of_failing() -> None:
    big = plan_of(*[rc(f"aws_x.r{i}", "aws_x", {"description": "d" * 900, "flag": False}) for i in range(60)])
    model = parse_plan(big)
    text, truncated = render_for_llm(model, max_chars=8000)
    assert truncated and len(text) <= 8000 and json.loads(text)["summary"]["types"] == {"aws_x": 60}
    tiny, truncated = render_for_llm(model, max_chars=500)
    assert truncated and len(tiny) <= 500


def test_configured_but_unknown_values_stay_in_place_as_a_marker() -> None:
    configuration = {"root_module": {"resources": [{"address": "aws_s3_bucket_policy.p", "expressions": {
        "bucket": {"references": ["aws_s3_bucket.b.id", "aws_s3_bucket.b"]},
        "policy": {"references": ["aws_s3_bucket.b.arn", "aws_s3_bucket.b"]}}}]}}  # fmt: skip
    change = rc("aws_s3_bucket_policy.p", "aws_s3_bucket_policy", {})
    change["change"]["after_unknown"] = {"bucket": True, "policy": True, "id": True, "computed_only": True}
    res = parse_plan(plan_of(change, configuration=configuration)).resources[0]
    assert res.attributes == {"bucket": UNKNOWN, "policy": UNKNOWN}  # computed_only/id were never configured: noise
    assert res.review_unknown == ("bucket", "policy")
    text, _ = render_for_llm(parse_plan(plan_of(change, configuration=configuration)))
    assert json.loads(text)["resources"][0]["attributes"]["policy"] == "(known after apply)"


def test_unknown_values_inside_nested_blocks_are_not_mistaken_for_absent() -> None:
    """Real case: `route { nat_gateway_id = aws_nat_gateway.x.id }` showed a route WITHOUT a NAT gateway, and a real model
    reported a critical 'route without NAT' that did not exist."""
    nat = load_plan(plan_path("nat-per-subnet"))
    route = nat.find("aws_route_table")[0].attributes["route"]
    assert route == [{"cidr_block": "0.0.0.0/0", "nat_gateway_id": UNKNOWN}]


@pytest.mark.parametrize(
    "expr",
    [
        {"route": {"references": ["aws_nat_gateway.x"]}},  # the block collapsed into one expression
        {
            "route": [
                {
                    "cidr_block": {"constant_value": "0.0.0.0/0"},
                    "nat_gateway_id": {"references": ["aws_nat_gateway.x.id"]},
                }
            ]
        },
    ],
)
def test_both_shapes_of_block_expressions_are_understood(expr: dict[str, Any]) -> None:
    change = rc("aws_route_table.r", "aws_route_table", {"route": [{"cidr_block": "0.0.0.0/0"}]})
    change["change"]["after_unknown"] = {"route": [{"nat_gateway_id": True}]}
    cfg = {"root_module": {"resources": [{"address": "aws_route_table.r", "expressions": expr}]}}
    attrs = parse_plan(plan_of(change, configuration=cfg)).resources[0].attributes
    assert attrs["route"] == [{"cidr_block": "0.0.0.0/0", "nat_gateway_id": UNKNOWN}]


def test_unknown_values_the_user_never_wrote_are_not_reported() -> None:
    change = rc("aws_route_table.r", "aws_route_table", {"route": [{"cidr_block": "0.0.0.0/0"}]})
    change["change"]["after_unknown"] = {"route": [{"nat_gateway_id": True}], "vpc_id": True}
    cfg = {
        "root_module": {
            "resources": [
                {"address": "aws_route_table.r", "expressions": {"route": [{"cidr_block": {"constant_value": "x"}}]}}
            ]
        }
    }
    attrs = parse_plan(plan_of(change, configuration=cfg)).resources[0].attributes
    assert attrs == {"route": [{"cidr_block": "0.0.0.0/0"}]}  # nat_gateway_id/vpc_id were computed by the provider


def test_the_public_bucket_example_exposes_its_policy_to_the_reviewers() -> None:
    policy = load_plan(plan_path("public-bucket")).find("aws_s3_bucket_policy")[0]
    assert '"Principal":"*"' in policy.attributes["policy"] and "policy" not in policy.review_unknown
