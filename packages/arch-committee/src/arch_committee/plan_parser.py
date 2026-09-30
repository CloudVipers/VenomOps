"""Terraform plan (``terraform show -json plan.out``) -> internal model, with sensitive data removed.

Everything that can reach an LLM or a report goes through here: values Terraform marks as sensitive
(``after_sensitive``) are masked, secret-looking keys and strings are redacted, and unknown/empty noise is
dropped so the model sees a compact, deterministic view of the change set.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .redact import redact

SENSITIVE = "[SENSITIVE]"
REDACTED = "[REDACTED]"
_SECRET_KEY = re.compile(r"(password|passwd|secret|token|api[_-]?key|access[_-]?key|private[_-]?key|credential)", re.I)
# Attributes that add tokens but no architectural information.
_NOISE_KEYS = frozenset({"tags_all", "timeouts", "id", "arn", "owner_id"})
_REF_INDEX = re.compile(r"\[[^\]]*\]")
# Configured attributes that are unknown until apply only because they wire resources together (ids, arns, names of
# other resources): not interesting for a review. Anything else (e.g. a policy built from an unknown ARN) is.
_PLUMBING = re.compile(r"(^|_)(id|ids|arn|arns)$")
_PLUMBING_NAMES = frozenset({"bucket", "role", "name"})


class PlanError(ValueError):
    """The input is not a usable Terraform plan."""


@dataclass(frozen=True)
class PlannedResource:
    address: str
    type: str
    name: str
    module: str | None
    index: str | None
    provider: str
    actions: tuple[str, ...]
    attributes: dict[str, Any]
    unknown_attributes: tuple[str, ...]
    depends_on: tuple[str, ...]
    # Configured by the user but not known until apply (so their content cannot be reviewed from the plan).
    review_unknown: tuple[str, ...] = ()

    @property
    def base_address(self) -> str:
        """Address without the count/for_each ``[index]`` (``aws_nat_gateway.x[1]`` -> ``aws_nat_gateway.x``)."""
        return _REF_INDEX.sub("", self.address)


@dataclass
class PlanModel:
    terraform_version: str
    resources: list[PlannedResource]
    variables: dict[str, Any] = field(default_factory=dict)

    @property
    def action_counts(self) -> dict[str, int]:
        counts: Counter[str] = Counter()
        for r in self.resources:
            counts["+".join(r.actions) if r.actions else "no-op"] += 1
        return dict(sorted(counts.items()))

    @property
    def type_counts(self) -> dict[str, int]:
        return dict(sorted(Counter(r.type for r in self.resources).items()))

    def find(self, rtype: str | None = None) -> list[PlannedResource]:
        return [r for r in self.resources if rtype is None or r.type == rtype]

    def by_address(self, address: str) -> PlannedResource | None:
        return next((r for r in self.resources if r.address == address), None)


# ---- masking -----------------------------------------------------------------------------------


def _mask(value: Any, sensitive: Any) -> Any:
    """Apply Terraform's ``*_sensitive`` tree: ``True`` masks the whole subtree."""
    if sensitive is True:
        return None if value is None else SENSITIVE
    if isinstance(value, dict):
        smap = sensitive if isinstance(sensitive, dict) else {}
        return {k: _mask(v, smap.get(k)) for k, v in value.items()}
    if isinstance(value, list):
        slist = sensitive if isinstance(sensitive, list) else []
        return [_mask(v, slist[i] if i < len(slist) else None) for i, v in enumerate(value)]
    return value


def _clean(value: Any, key: str = "", *, top: bool = True) -> Any:
    """Redact secrets, drop noise and empty values. Keeps ``False`` and ``0`` (they carry meaning).

    Computed-attribute noise (``id``, ``arn``, ``tags_all``...) is only dropped at the resource's top level: a nested
    block may legitimately have an ``id``.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if top and k in _NOISE_KEYS:
                continue
            cleaned = _clean(v, k, top=False)
            if cleaned is None or cleaned == {} or cleaned == [] or cleaned == "":
                continue
            out[k] = cleaned
        return out
    if isinstance(value, list):
        items = [_clean(v, key, top=False) for v in value]
        return [i for i in items if i is not None and i != {} and i != [] and i != ""]
    if isinstance(value, str):
        if value == SENSITIVE:
            return value
        if _SECRET_KEY.search(key) and value:
            return REDACTED
        return redact(value)
    return value


# ---- dependencies from the configuration block ----------------------------------------------------


def _collect_refs(node: Any, out: set[str]) -> None:
    if isinstance(node, dict):
        for k, v in node.items():
            if k == "references" and isinstance(v, list):
                out.update(str(r) for r in v)
            else:
                _collect_refs(v, out)
    elif isinstance(node, list):
        for v in node:
            _collect_refs(v, out)


def _base_ref(ref: str) -> str | None:
    """``aws_eip.nat[count.index].id`` -> ``aws_eip.nat``; variables, locals and attribute-only refs -> None."""
    ref = _REF_INDEX.sub("", ref)
    parts = ref.split(".")
    if parts[0] in {"var", "local", "count", "each", "path", "terraform", "self"}:
        return None
    if parts[0] == "data" and len(parts) >= 3:
        return ".".join(parts[:3])
    if parts[0] == "module" and len(parts) >= 2:
        return ".".join(parts[:2])
    return ".".join(parts[:2]) if len(parts) >= 2 else None


def _configured_attributes(configuration: dict[str, Any]) -> dict[str, frozenset[str]]:
    """Attribute names the user wrote for each resource (from the plan's ``configuration`` block)."""
    configured: dict[str, frozenset[str]] = {}

    def walk(module: dict[str, Any], prefix: str) -> None:
        for res in module.get("resources", []):
            configured[f"{prefix}{res['address']}"] = frozenset(res.get("expressions", {}))
        for name, call in module.get("module_calls", {}).items():
            walk(call.get("module", {}), f"{prefix}module.{name}.")

    walk(configuration.get("root_module", {}), "")
    return configured


def _config_dependencies(configuration: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    deps: dict[str, tuple[str, ...]] = {}

    def walk(module: dict[str, Any], prefix: str) -> None:
        for res in module.get("resources", []):
            refs: set[str] = set()
            _collect_refs(res.get("expressions", {}), refs)
            refs.update(str(d) for d in res.get("depends_on", []))
            address = f"{prefix}{res['address']}"
            own = _base_ref(address)
            found = {b for r in refs if (b := _base_ref(r)) and b != own}
            deps[address] = tuple(sorted(found))
        for name, call in module.get("module_calls", {}).items():
            walk(call.get("module", {}), f"{prefix}module.{name}.")

    walk(configuration.get("root_module", {}), "")
    return deps


# ---- public API -----------------------------------------------------------------------------------


def parse_plan(data: dict[str, Any]) -> PlanModel:
    if not isinstance(data, dict) or "resource_changes" not in data:
        raise PlanError(
            "not a Terraform plan JSON: 'resource_changes' is missing (use `terraform show -json plan.out`)"
        )
    if data.get("errored"):
        raise PlanError("the plan is marked as errored; fix it before asking for a review")

    deps = _config_dependencies(data.get("configuration", {}))
    configured = _configured_attributes(data.get("configuration", {}))
    resources: list[PlannedResource] = []
    for rc in data["resource_changes"]:
        change = rc.get("change", {})
        actions = tuple(change.get("actions", []))
        if actions == ("no-op",) or actions == ("read",):
            continue  # unchanged resources and data reads are not part of what is being reviewed
        after = change.get("after") if actions != ("delete",) else change.get("before")
        sensitive = change.get("after_sensitive") if actions != ("delete",) else change.get("before_sensitive")
        attrs = _clean(_mask(after or {}, sensitive))
        unknown = tuple(
            sorted(k for k, v in (change.get("after_unknown") or {}).items() if v is True and k not in _NOISE_KEYS)
        )
        address = str(rc["address"])
        base = _REF_INDEX.sub("", address)
        review_unknown = tuple(
            k
            for k in unknown
            if k in configured.get(base, frozenset()) and not _PLUMBING.search(k) and k not in _PLUMBING_NAMES
        )
        index = rc.get("index")
        module_addr = rc.get("module_address")
        resources.append(
            PlannedResource(
                address=address,
                type=str(rc["type"]),
                name=str(rc["name"]),
                module=str(module_addr) if module_addr else None,
                index=None if index is None else str(index),
                provider=str(rc.get("provider_name", "")).rsplit("/", 1)[-1],
                actions=actions,
                attributes=attrs,
                unknown_attributes=unknown,
                depends_on=deps.get(base, ()),
                review_unknown=review_unknown,
            )
        )
    resources.sort(key=lambda r: r.address)

    variables = {
        k: (SENSITIVE if _SECRET_KEY.search(k) else _clean(v.get("value") if isinstance(v, dict) else v, k))
        for k, v in (data.get("variables") or {}).items()
    }
    return PlanModel(terraform_version=str(data.get("terraform_version", "")), resources=resources, variables=variables)


def load_plan(path: Path) -> PlanModel:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise PlanError(f"{path}: not valid JSON ({exc.msg}, line {exc.lineno})") from exc
    return parse_plan(data)


# ---- rendering for the model -----------------------------------------------------------------------


def _shorten(value: Any, limit: int) -> Any:
    if isinstance(value, str) and len(value) > limit:
        return value[:limit] + "…"
    if isinstance(value, dict):
        return {k: _shorten(v, limit) for k, v in value.items()}
    if isinstance(value, list):
        return [_shorten(v, limit) for v in value]
    return value


def render_for_llm(model: PlanModel, max_chars: int = 60_000) -> tuple[str, bool]:
    """Compact JSON of the plan for the agents. Returns ``(text, truncated)``.

    Attributes that are only known after apply are left out as noise (the dependency edges already say how resources
    connect), except the ones the user configured whose *content* is unknown (``known_after_apply``): the agents must
    know they cannot be reviewed rather than assume they are missing. Degrades in steps to respect ``max_chars``:
    shorter strings, then headers only (address/type/actions/deps).
    """

    def build(attr_limit: int | None) -> str:
        items = []
        for r in model.resources:
            item: dict[str, Any] = {"address": r.address, "type": r.type, "actions": list(r.actions)}
            if attr_limit is not None:
                item["attributes"] = _shorten(r.attributes, attr_limit)
            if r.review_unknown and attr_limit is not None:
                item["known_after_apply"] = list(r.review_unknown)
            if r.depends_on:
                item["depends_on"] = list(r.depends_on)
            items.append(item)
        doc = {
            "terraform_version": model.terraform_version,
            "summary": {"actions": model.action_counts, "types": model.type_counts},
            "resources": items,
        }
        if model.variables:
            doc["variables"] = model.variables
        return json.dumps(doc, ensure_ascii=False, separators=(",", ":"), sort_keys=True)

    for limit in (400, 120, None):
        text = build(limit)
        if len(text) <= max_chars:
            return text, limit != 400
    return build(None)[:max_chars], True
