"""Go and Python must agree on every example: same verdict and same (path, keyword) issues."""

from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path
from typing import Any, cast

import pytest

from findings_schema import validate

from .conftest import PKG_ROOT, example_files

pytestmark = pytest.mark.skipif(shutil.which("go") is None, reason="Go toolchain not installed")

ALL = example_files("valid") + example_files("invalid")


def _go_results() -> dict[str, dict[str, Any]]:
    proc = subprocess.run(
        ["go", "run", "./go/cmd/findings-validate", *map(str, ALL)],
        cwd=PKG_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert proc.returncode in (0, 1), proc.stderr  # 1 = at least one invalid file, expected here
    rows = [json.loads(line) for line in proc.stdout.splitlines() if line.strip()]
    return {Path(str(r["file"])).name: r for r in rows}


def test_go_and_python_agree_on_every_example() -> None:
    go = _go_results()
    assert set(go) == {p.name for p in ALL}
    for path in ALL:
        data = json.loads(path.read_text(encoding="utf-8"))
        py_issues = {(i.path, i.keyword) for i in validate(data)}
        go_row = go[path.name]
        go_issues = {
            (i["path"], i["keyword"]) for i in cast(list[dict[str, str]], go_row["issues"])
        }
        assert go_row["valid"] == (not py_issues), path.name
        assert go_issues == py_issues, path.name
