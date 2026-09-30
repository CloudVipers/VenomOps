"""The only operations an agent (deterministic fixer or LLM) may perform on a repository.

Every operation is confined to ``root`` (see :mod:`pr_agent.safety`), edits are structured (no free-form
file writes) and verified by re-parsing the HCL, and terraform runs only through the allowlisted runner.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .. import hcl
from ..safety import CommandResult, PathNotAllowedError, SafeRunner, resolve_in_repo

MAX_READ_BYTES = 200_000
MAX_LIST_RESULTS = 200
EDITABLE_SUFFIX = ".tf"


class ToolError(Exception):
    """A tool call failed in a way the caller (or the model) can understand and react to."""


@dataclass
class EditRecord:
    path: str
    detail: str


@dataclass
class ToolBox:
    """Tools bound to one working copy (``root``)."""

    root: Path
    runner: SafeRunner
    edits: list[EditRecord] = field(default_factory=list)

    # ---- read-only tools ------------------------------------------------------------------

    def read_file(self, path: str) -> str:
        target = resolve_in_repo(self.root, path)
        if not target.is_file():
            raise ToolError(f"not a file: {path}")
        if target.stat().st_size > MAX_READ_BYTES:
            raise ToolError(f"file too large to read ({target.stat().st_size} bytes): {path}")
        return target.read_text(encoding="utf-8")

    def list_files(self, pattern: str = "**/*.tf") -> list[str]:
        if pattern.startswith("/") or ".." in Path(pattern).parts:
            raise PathNotAllowedError(f"pattern must be relative to the repository: {pattern!r}")
        found: list[str] = []
        for p in sorted(self.root.glob(pattern)):
            if not p.is_file():
                continue
            rel = p.relative_to(self.root).as_posix()
            try:
                resolve_in_repo(self.root, rel)
            except PathNotAllowedError:
                continue  # secrets/VCS internals are invisible, not just unreadable
            found.append(rel)
            if len(found) >= MAX_LIST_RESULTS:
                break
        return found

    # ---- edit tool --------------------------------------------------------------------------

    def edit_hcl(self, path: str, patch: dict[str, Any]) -> bool:
        """Apply one structured patch to a ``.tf`` file. Returns True if the file changed.

        Supported ``op`` values: ``append_block`` (content), ``replace_attr`` (resource, attr, old, new),
        ``ensure_tags`` (resource, tags) and ``set_memory_limit`` (resource, container, old, new).
        The edited text must parse as valid HCL or nothing is written.
        """
        target = resolve_in_repo(self.root, path)
        if target.suffix != EDITABLE_SUFFIX or not target.is_file():
            raise ToolError(f"only existing {EDITABLE_SUFFIX} files can be edited: {path}")
        before = target.read_text(encoding="utf-8")
        op = patch.get("op")
        try:
            after, detail = self._apply(op, before, patch)
            hcl.parse(after)
        except hcl.EditError as exc:
            raise ToolError(str(exc)) from exc
        if after == before:
            return False
        tmp = target.with_suffix(".tf.tmp")
        tmp.write_text(after, encoding="utf-8")
        os.replace(tmp, target)  # atomic: the file is either untouched or fully edited
        self.edits.append(EditRecord(path, detail))
        return True

    @staticmethod
    def _split_address(patch: dict[str, Any]) -> tuple[str, str]:
        address = str(patch.get("resource", ""))
        rtype, _, rname = address.partition(".")
        if not rtype or not rname or "." in rname:
            raise hcl.EditError(f"resource must look like '<type>.<name>', got {address!r}")
        return rtype, rname

    def _apply(self, op: Any, text: str, patch: dict[str, Any]) -> tuple[str, str]:
        if op == "append_block":
            content = str(patch.get("content", ""))
            if not content.strip():
                raise hcl.EditError("append_block requires content")
            return hcl.append_block(text, content), "append block"
        if op == "replace_attr":
            rtype, rname = self._split_address(patch)
            block = hcl.find_resource(text, rtype, rname)
            if block is None:
                raise hcl.EditError(f"resource {rtype}.{rname} not found in this file")
            attr, old, new = (str(patch.get(k, "")) for k in ("attr", "old", "new"))
            if not attr or not old or not new:
                raise hcl.EditError("replace_attr requires attr, old and new")
            new_text, count = hcl.replace_attr_value(text, block, attr, old, new)
            if count == 0:
                raise hcl.EditError(f'{rtype}.{rname} has no {attr} = "{old}"')
            return new_text, f"{rtype}.{rname}: {attr} {old} -> {new} ({count}x)"
        if op == "set_memory_limit":
            rtype, rname = self._split_address(patch)
            if not rtype.startswith("kubernetes_"):
                raise hcl.EditError("set_memory_limit only applies to kubernetes_* workload resources")
            block = hcl.find_resource(text, rtype, rname)
            if block is None:
                raise hcl.EditError(f"resource {rtype}.{rname} not found in this file")
            container, old, new = (str(patch.get(k, "")) for k in ("container", "old", "new"))
            quantity = re.compile(r"^\d+(\.\d+)?(Ki|Mi|Gi)?$")
            if not container or not quantity.match(old) or not quantity.match(new):
                raise hcl.EditError(
                    "set_memory_limit requires container and Kubernetes quantities for old/new (e.g. 32Mi)"
                )
            new_text, detail = hcl.set_memory_limit(text, block, container, old, new)
            return new_text, f"{rtype}.{rname}: {detail}"
        if op == "ensure_tags":
            rtype, rname = self._split_address(patch)
            block = hcl.find_resource(text, rtype, rname)
            if block is None:
                raise hcl.EditError(f"resource {rtype}.{rname} not found in this file")
            tags = patch.get("tags")
            if (
                not isinstance(tags, dict)
                or not tags
                or not all(isinstance(k, str) and isinstance(v, str) and k for k, v in tags.items())
            ):
                raise hcl.EditError("ensure_tags requires a non-empty map of string tags")
            new_text, added = hcl.ensure_tags(text, block, tags)
            return new_text, f"{rtype}.{rname}: tags added {added}"
        raise hcl.EditError(f"unknown op {op!r} (use append_block, replace_attr or ensure_tags)")

    # ---- terraform (allowlisted) ------------------------------------------------------------

    def terraform_validate(self) -> CommandResult:
        """``terraform init -backend=false`` (providers only, never remote state) then ``validate``."""
        init = self.runner.run(["terraform", "init", "-backend=false", "-input=false", "-no-color"])
        if not init.ok:
            return init
        return self.runner.run(["terraform", "validate", "-no-color"])

    def terraform_plan(self) -> CommandResult:
        """Read-only ``terraform plan``; its text output is attached to the PR."""
        return self.runner.run(["terraform", "plan", "-input=false", "-no-color", "-lock=false", "-refresh=false"])


# Tool definitions in Bedrock Converse "toolSpec" shape, used only by the optional LLM agent.
TOOL_SPECS: list[dict[str, Any]] = [
    {
        "name": "read_file",
        "description": "Read a file inside the repository (never state files, tfvars or secrets).",
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": ["path"],
            }
        },
    },
    {
        "name": "list_files",
        "description": "List repository files matching a glob relative to the repo root (default **/*.tf).",
        "inputSchema": {"json": {"type": "object", "properties": {"glob": {"type": "string"}}}},
    },
    {
        "name": "edit_hcl",
        "description": (
            "Apply ONE structured edit to a .tf file. op=append_block {content}; "
            "op=replace_attr {resource:'type.name', attr, old, new}; "
            "op=ensure_tags {resource:'type.name', tags:{K:V}}; "
            "op=set_memory_limit {resource:'kubernetes_*.name', container, old:'32Mi', new:'64Mi'}."
        ),
        "inputSchema": {
            "json": {
                "type": "object",
                "properties": {"path": {"type": "string"}, "patch": {"type": "object"}},
                "required": ["path", "patch"],
            }
        },
    },
    {
        "name": "terraform_validate",
        "description": "Run terraform validate on the working copy (providers are initialised without touching state).",
        "inputSchema": {"json": {"type": "object", "properties": {}}},
    },
    {
        "name": "terraform_plan",
        "description": "Run a read-only terraform plan and return its output.",
        "inputSchema": {"json": {"type": "object", "properties": {}}},
    },
]
