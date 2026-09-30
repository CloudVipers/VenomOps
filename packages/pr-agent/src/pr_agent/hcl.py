"""Minimal, verifiable text edits on HCL (Terraform) files.

``python-hcl2`` only *parses* HCL (no positions, no round-trip), so edits are done on the text, in small
well-defined operations, and every result is re-parsed with ``hcl2`` to prove the file is still valid
HCL. The scanner below ignores strings (including ``${...}`` interpolations), comments and heredocs so
braces and keywords inside them are never mistaken for code.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from io import StringIO
from typing import Any

import hcl2


class EditError(Exception):
    """An edit cannot be applied safely (the caller must abort instead of guessing)."""


def sanitize(text: str) -> str:
    """Return ``text`` with strings, comments and heredocs blanked (same length, newlines kept).

    Braces and keywords that remain are guaranteed to be real code.
    """
    out = list(text)
    n = len(text)

    def blank(a: int, b: int) -> None:
        for k in range(a, min(b, n)):
            if out[k] != "\n":
                out[k] = " "

    # stack entries: "code" | "str" | ["interp", depth]
    stack: list[Any] = ["code"]
    i = 0
    while i < n:
        mode = stack[-1]
        c = text[i]
        if mode == "str":
            start = i
            if c == "\\":
                blank(i, i + 2)
                i += 2
                continue
            if text.startswith(("$${", "%%{"), i):
                blank(i, i + 3)
                i += 3
                continue
            if text.startswith(("${", "%{"), i):
                blank(i, i + 2)
                stack.append(["interp", 0])
                i += 2
                continue
            if c == '"':
                stack.pop()
            blank(start, i + 1)
            i += 1
            continue

        # code or interpolation
        if c == '"':
            blank(i, i + 1)
            stack.append("str")
            i += 1
        elif c == "#" or text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j == -1 else j
            blank(i, j)
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j == -1 else j + 2
            blank(i, j)
            i = j
        elif m := re.match(r"<<-?([A-Za-z_][A-Za-z0-9_]*)[ \t]*\n", text[i:]):
            ident = m.group(1)
            end = re.compile(rf"^[ \t]*{re.escape(ident)}[ \t]*$", re.MULTILINE).search(text, i + m.end())
            j = n if end is None else end.end()
            blank(i, j)
            i = j
        elif mode != "code" and c == "{":
            mode[1] += 1
            blank(i, i + 1)
            i += 1
        elif mode != "code" and c == "}":
            blank(i, i + 1)
            if mode[1] == 0:
                stack.pop()
            else:
                mode[1] -= 1
            i += 1
        else:
            if mode != "code":
                blank(i, i + 1)
            i += 1
    return "".join(out)


@dataclass(frozen=True)
class Block:
    """Offsets of a ``resource`` block: ``start`` (header line), ``open``/``close`` braces."""

    start: int
    open: int
    close: int

    @property
    def end(self) -> int:
        return self.close + 1


def find_resource(text: str, rtype: str, rname: str) -> Block | None:
    """Locate ``resource "<rtype>" "<rname>" { ... }`` in real code (not in comments/strings)."""
    clean = sanitize(text)
    header = re.compile(
        rf'^[ \t]*resource[ \t]+"{re.escape(rtype)}"[ \t]+"{re.escape(rname)}"[ \t]*\{{',
        re.MULTILINE,
    )
    for m in header.finditer(text):
        kw = m.start() + (len(m.group(0)) - len(m.group(0).lstrip()))
        if clean[kw : kw + 8] != "resource":
            continue  # the match lives inside a comment or a string
        open_idx = m.end() - 1
        depth = 0
        for k in range(open_idx, len(clean)):
            if clean[k] == "{":
                depth += 1
            elif clean[k] == "}":
                depth -= 1
                if depth == 0:
                    return Block(start=m.start(), open=open_idx, close=k)
        raise EditError(f"unbalanced braces in resource {rtype}.{rname}")
    return None


def has_resource(text: str, rtype: str, rname: str) -> bool:
    return find_resource(text, rtype, rname) is not None


def parse(text: str) -> dict[str, Any]:
    """Parse HCL; raises :class:`EditError` if it is not valid."""
    try:
        return dict(hcl2.load(StringIO(text)))
    except Exception as exc:  # noqa: BLE001 - hcl2/lark raise many exception types
        raise EditError(f"result is not valid HCL: {exc}") from exc


def _unquote(value: str) -> str:
    return value[1:-1] if len(value) >= 2 and value[0] == '"' and value[-1] == '"' else value


def _normalize(node: Any) -> Any:
    """python-hcl2 8.x keeps the quotes around keys/strings and adds ``__is_block__`` markers."""
    if isinstance(node, dict):
        return {_unquote(str(k)): _normalize(v) for k, v in node.items() if not str(k).startswith("__")}
    if isinstance(node, list):
        return [_normalize(v) for v in node]
    if isinstance(node, str):
        return _unquote(node)
    return node


def resource_attrs(parsed: dict[str, Any], rtype: str, rname: str) -> dict[str, Any] | None:
    """Attributes of ``resource rtype.rname`` from :func:`parse` output, with quotes stripped."""
    for entry in parsed.get("resource", []):
        by_type = _normalize(entry)
        found = by_type.get(rtype, {}).get(rname)
        if found is not None:
            return dict(found)
    return None


def append_block(text: str, snippet: str) -> str:
    """Append a new top-level block after a blank line, keeping the file ending with one newline."""
    base = text.rstrip("\n")
    sep = "\n\n" if base else ""
    return f"{base}{sep}{snippet.strip(chr(10))}\n"


def replace_attr_value(text: str, block: Block, attr: str, old: str, new: str) -> tuple[str, int]:
    """Replace ``attr = "old"`` with ``attr = "new"`` anywhere inside ``block`` (nested blocks too).

    Only real code is touched. Returns the new text and how many replacements were made.
    """
    clean = sanitize(text)
    pattern = re.compile(rf'(?<![\w-]){re.escape(attr)}([ \t]*=[ \t]*)"{re.escape(old)}"')
    pieces: list[str] = []
    last = 0
    count = 0
    for m in pattern.finditer(text, block.open, block.close):
        if clean[m.start() : m.start() + len(attr)] != attr:
            continue  # inside a comment/string
        pieces.append(text[last : m.start()])
        pieces.append(f'{attr}{m.group(1)}"{new}"')
        last = m.end()
        count += 1
    pieces.append(text[last:])
    return "".join(pieces), count


def _match_brace(clean: str, open_idx: int, limit: int) -> int:
    depth = 0
    for k in range(open_idx, limit):
        if clean[k] == "{":
            depth += 1
        elif clean[k] == "}":
            depth -= 1
            if depth == 0:
                return k
    raise EditError("unbalanced braces")


def find_nested(clean: str, start: int, end: int, name: str) -> list[tuple[int, int]]:
    """``(open, close)`` of every ``name { ... }`` block or ``name = { ... }`` map inside ``clean[start:end]``."""
    found: list[tuple[int, int]] = []
    for m in re.finditer(rf"(?<![\w-]){re.escape(name)}\s*(?:=\s*)?\{{", clean[start:end]):
        open_idx = start + m.end() - 1
        found.append((open_idx, _match_brace(clean, open_idx, end)))
    return found


def _top_level_string(text: str, clean: str, start: int, end: int, attr: str) -> str | None:
    """Literal value of ``attr = "value"`` written directly in ``text[start:end]`` (not in a nested block)."""
    depth = 0
    i = start
    pattern = re.compile(rf'{re.escape(attr)}[ \t]*=[ \t]*"([^"\n]*)"')
    while i < end:
        if clean[i] == "{":
            depth += 1
        elif clean[i] == "}":
            depth -= 1
        elif (
            depth == 0
            and clean.startswith(attr, i)
            and (i == start or not (clean[i - 1].isalnum() or clean[i - 1] in "_-"))
        ):
            m = pattern.match(text, i)
            if m:
                return m.group(1)
        i += 1
    return None


def set_memory_limit(text: str, block: Block, container: str, old: str, new: str) -> tuple[str, str]:
    """Change ``memory = "old"`` to ``"new"`` inside ``limits`` of the named ``container`` of a workload resource.

    Works for ``limits { memory = ".." }`` blocks and ``limits = { memory = ".." }`` maps, at any depth (Pod,
    Deployment, StatefulSet...). ``requests`` and other containers are never touched. Raises ``EditError`` when the
    target is missing or ambiguous, and with "already" in the message when the limit already has the new value.
    """
    clean = sanitize(text)
    containers = find_nested(clean, block.open + 1, block.close, "container")
    named = [(o, c) for o, c in containers if _top_level_string(text, clean, o + 1, c, "name") == container]
    if not named:
        available = [_top_level_string(text, clean, o + 1, c, "name") for o, c in containers]
        raise EditError(f"container {container!r} not found in the resource (available: {available})")
    if len(named) > 1:
        raise EditError(f"container {container!r} is defined more than once in the resource")
    c_open, c_close = named[0]
    limits = find_nested(clean, c_open + 1, c_close, "limits")
    if len(limits) != 1:
        raise EditError(f"container {container!r} has {len(limits)} 'limits' definitions (expected exactly one)")
    l_open, l_close = limits[0]

    def find(value: str) -> list[re.Match[str]]:
        rx = re.compile(rf'(?<![\w-])memory([ \t]*=[ \t]*)"{re.escape(value)}"')
        return [m for m in rx.finditer(text, l_open, l_close) if clean[m.start() : m.start() + 6] == "memory"]

    hits = find(old)
    if not hits:
        if find(new):
            raise EditError(f"limits.memory of {container!r} is already {new}")
        raise EditError(f'limits of {container!r} has no memory = "{old}"')
    if len(hits) > 1:
        raise EditError(f"limits of {container!r} defines memory more than once")
    m = hits[0]
    return text[: m.start()] + f'memory{m.group(1)}"{new}"' + text[
        m.end() :
    ], f"{container}: limits.memory {old} -> {new}"


_KEY_RE = re.compile(r'^[ \t]*("(?P<q>[^"]+)"|(?P<b>[A-Za-z_][\w-]*))[ \t]*[=:]', re.MULTILINE)


def _hcl_string(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"').replace("${", "$${").replace("%{", "%%{") + '"'


def ensure_tags(text: str, block: Block, entries: dict[str, str]) -> tuple[str, list[str]]:
    """Make sure the literal ``tags = { ... }`` of ``block`` has every key in ``entries``.

    Existing keys keep their values (never overwritten). Returns the new text and the added keys.
    Refuses (EditError) when ``tags`` is not a literal map (variable, merge(...), etc.).
    """
    clean = sanitize(text)
    body = clean[block.open + 1 : block.close]
    m = re.search(r"(?m)^[ \t]*tags[ \t]*=[ \t]*", body)
    # only a top-level attribute of this resource counts (depth 0 inside the block body)
    while m and body[: m.start()].count("{") != body[: m.start()].count("}"):
        m = re.compile(r"(?m)^[ \t]*tags[ \t]*=[ \t]*").search(body, m.end())

    if m is None:
        indent = "  "
        lines = "".join(f"{indent}  {k} = {_hcl_string(v)}\n" for k, v in entries.items())
        insert_at = block.close
        head = "" if text[:insert_at].endswith("\n") else "\n"
        addition = f"{head}\n{indent}tags = {{\n{lines}{indent}}}\n"
        return text[:insert_at] + addition + text[insert_at:], list(entries)

    value_start = block.open + 1 + m.end()
    if clean[value_start] != "{":
        raise EditError("tags is not a literal map (variable or function call); edit it manually")
    depth = 0
    map_close = -1
    for k in range(value_start, block.close):
        if clean[k] == "{":
            depth += 1
        elif clean[k] == "}":
            depth -= 1
            if depth == 0:
                map_close = k
                break
    if map_close < 0:
        raise EditError("unbalanced tags map")

    existing = {(mm.group("q") or mm.group("b")) for mm in _KEY_RE.finditer(text[value_start + 1 : map_close])}
    missing = {k: v for k, v in entries.items() if k not in existing}
    if not missing:
        return text, []

    inner = text[value_start + 1 : map_close]
    indent_match = re.search(r"\n([ \t]+)\S", inner)
    entry_indent = indent_match.group(1) if indent_match else "    "
    closing_indent = re.search(r"\n([ \t]*)$", text[:map_close] + "")
    close_indent = closing_indent.group(1) if closing_indent else entry_indent[:-2]
    lines = "".join(f"{entry_indent}{k} = {_hcl_string(v)}\n" for k, v in missing.items())
    if "\n" not in inner.strip(" \t"):  # one-line map: { A = "x" }  -> expand
        raise EditError("tags map is written on a single line; expand it or edit it manually")
    before = text[:map_close].rstrip(" \t")
    if not before.endswith("\n"):
        before += "\n"
    return before + lines + close_indent + text[map_close:], list(missing)
