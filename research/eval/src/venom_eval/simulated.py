"""Simulated executor for the "prompt-only" condition of experiment E1.

It exposes the same tools as pr-agent (same names and specs, so the model sees exactly the same interface) but
enforces **nothing** and executes **nothing**: every call is recorded and answered as if it had worked. This measures
what a model *tries* to do when only the prompt protects the system, without putting anything at risk:

* no process is ever started (terraform and unknown tools return canned text);
* no file is ever written (edits are recorded, not applied);
* reads are served only from the case's own temporary directory; anything else looks like a missing file.

Prompt-level controls stay identical to the real agent (same system prompt, data wrapped in ``<untrusted_data>``):
only the controls enforced in code are missing.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pr_agent.agent import untrusted

MAX_OUTPUT = 6000


@dataclass(frozen=True)
class ToolCall:
    name: str
    args: dict[str, Any]


@dataclass
class SimulatedToolBox:
    """``root`` is the materialized repository; reads may reach its parent (the case directory) and nothing else."""

    root: Path
    calls: list[ToolCall] = field(default_factory=list)

    @property
    def sandbox(self) -> Path:
        return self.root.resolve().parent

    def _within_sandbox(self, path: str) -> Path | None:
        target = (self.root / path).resolve() if path else self.root.resolve()
        return target if target.is_relative_to(self.sandbox) else None

    def dispatch(self, name: str, args: dict[str, Any]) -> str:
        self.calls.append(ToolCall(name, json.loads(json.dumps(args, default=str))))
        if name == "read_file":
            target = self._within_sandbox(str(args.get("path", "")))
            if target is None or not target.is_file():
                return f"error: no such file: {args.get('path')}"
            return untrusted(
                f"file:{args.get('path')}", target.read_text(encoding="utf-8", errors="replace")[:MAX_OUTPUT]
            )
        if name == "list_files":
            pattern = str(args.get("glob") or "**/*.tf")
            base = self.root.resolve()
            found = []
            try:
                for p in sorted(base.glob(pattern)):
                    if p.is_file() and p.resolve().is_relative_to(self.sandbox):
                        found.append(
                            p.resolve().relative_to(base).as_posix() if p.resolve().is_relative_to(base) else str(p)
                        )
            except (ValueError, NotImplementedError):
                return "error: invalid pattern"
            return untrusted("list_files", "\n".join(found))
        if name == "edit_hcl":
            return "edited"
        if name in ("terraform_validate", "terraform_plan"):
            return untrusted(name, "Success! The configuration is valid.")
        return "ok"  # an invented tool "works" too: there is no allowlist in this condition
