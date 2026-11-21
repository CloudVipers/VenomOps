"""What a new top-level block appended with ``edit_hcl``/``append_block`` may contain (enforced in code).

The content of ``append_block`` can come from the optional LLM agent, and ``terraform init``/``plan`` evaluate it on
the reviewer's machine. Terraform code can execute programs during ``plan`` (``data "external"``), download arbitrary
providers or modules during ``init`` (a new ``required_providers`` entry, an unknown resource type prefix, a remote
``module`` source) or carry ``local-exec``/``remote-exec`` provisioners. So the block is parsed and only accepted if:

* it contains nothing but ``resource`` blocks (no ``data``, ``module``, ``provider``, ``terraform``, ``check``,
  ``locals``, ``variable``, ``output``, ``import``, ``moved``, ``removed``, ...);
* every resource type (and any ``provider = x.alias`` meta-argument) belongs to a provider already used by the
  repository, and never to a provider that can run code or touch the local machine;
* no ``provisioner`` or ``connection`` block appears anywhere, and ``local-exec``/``remote-exec`` are not even
  mentioned.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from . import hcl

# Providers whose resources or data sources run programs, call arbitrary URLs or write to the local machine.
# Refused even if the repository already uses them.
DENIED_PROVIDERS = frozenset({"external", "null", "local", "http", "terraform"})
_FORBIDDEN_KEYS = frozenset({"provisioner", "connection"})
_FORBIDDEN_TEXT = re.compile(r"local-exec|remote-exec", re.IGNORECASE)
# python-hcl2 renders the reference `aws.west` as "${aws.west}".
_PROVIDER_REF = re.compile(r"^([a-z][a-z0-9-]*)(\.[A-Za-z_][A-Za-z0-9_-]*)?$")


class BlockNotAllowedError(hcl.EditError):
    """The block to append is outside the allowed policy."""


def provider_of(resource_type: str) -> str:
    """Terraform's implied provider local name for a resource type: the prefix before the first ``_``."""
    return resource_type.split("_", 1)[0]


def project_providers(texts: Iterable[str]) -> frozenset[str]:
    """Provider local names the repository already uses (resources, data sources, provider blocks, required_providers).

    Files that do not parse are ignored: they cannot widen the allowlist.
    """
    found: set[str] = set()
    for text in texts:
        try:
            parsed = hcl.parse(text)
        except hcl.EditError:
            continue
        for kind in ("resource", "data"):
            for entry in parsed.get(kind, []):
                found.update(provider_of(rtype) for rtype in hcl._normalize(entry))  # noqa: SLF001 - same package
        for entry in parsed.get("provider", []):
            found.update(hcl._normalize(entry))  # noqa: SLF001
        for entry in parsed.get("terraform", []):
            for req in hcl._normalize(entry).get("required_providers", []):  # noqa: SLF001
                found.update(req)
    return frozenset(found - DENIED_PROVIDERS)


def _walk_keys(node: Any) -> Iterable[str]:
    if isinstance(node, dict):
        for key, value in node.items():
            yield str(key)
            yield from _walk_keys(value)
    elif isinstance(node, list):
        for item in node:
            yield from _walk_keys(item)


def check_appendable(snippet: str, allowed_providers: frozenset[str]) -> None:
    """Raise :class:`BlockNotAllowedError` unless ``snippet`` only declares allowed ``resource`` blocks."""
    if _FORBIDDEN_TEXT.search(snippet):
        raise BlockNotAllowedError("local-exec/remote-exec are not allowed in an appended block")
    try:
        parsed = hcl.parse(snippet)
    except hcl.EditError as exc:
        raise BlockNotAllowedError(f"the block to append is not valid HCL: {exc}") from exc
    kinds = set(parsed)
    if kinds != {"resource"}:
        extra = sorted(kinds - {"resource"}) or ["nothing"]
        raise BlockNotAllowedError(f"only resource blocks can be appended (found: {', '.join(extra)})")

    for entry in parsed["resource"]:
        for rtype, by_name in hcl._normalize(entry).items():  # noqa: SLF001 - same package
            provider = provider_of(rtype)
            if provider in DENIED_PROVIDERS or provider not in allowed_providers:
                allowed = ", ".join(sorted(allowed_providers)) or "none"
                raise BlockNotAllowedError(
                    f"resource type {rtype!r} uses provider {provider!r}, which is not allowed "
                    f"(allowed: providers already used by the repository: {allowed})"
                )
            for body in by_name.values():
                forbidden = _FORBIDDEN_KEYS.intersection(_walk_keys(body))
                if forbidden:
                    raise BlockNotAllowedError(f"{', '.join(sorted(forbidden))} blocks are not allowed in {rtype}")
                ref = body.get("provider") if isinstance(body, dict) else None
                if ref is not None:
                    text = str(ref)
                    if text.startswith("${") and text.endswith("}"):
                        text = text[2:-1]
                    match = _PROVIDER_REF.match(text)
                    if not match or match.group(1) in DENIED_PROVIDERS or match.group(1) not in allowed_providers:
                        raise BlockNotAllowedError(f"provider meta-argument {ref!r} is not allowed in {rtype}")
