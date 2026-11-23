"""Detached HMAC-SHA256 signatures for findings documents (ADR 0012).

The signature covers the exact bytes of the JSON file as written, so producers in any language
agree without a canonical form. It lives next to the file as ``<file>.sig``; the finding schema
itself does not change.

HMAC proves the document was produced by a holder of the shared key and was not modified
afterwards. It is not non-repudiation: every holder of the key can sign (see the ADR for the
asymmetric alternative).
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
from collections.abc import Mapping
from pathlib import Path

ENV_KEY = "VENOMOPS_FINDINGS_KEY"
ALGORITHM = "HMAC-SHA256"
MIN_KEY_BYTES = 32


class SignatureError(ValueError):
    """Missing, malformed or invalid signature, or an unusable key."""


def key_from_env(env: Mapping[str, str] | None = None) -> bytes | None:
    """The shared key from ``VENOMOPS_FINDINGS_KEY`` (None if unset). Short keys are refused."""
    value = (env if env is not None else os.environ).get(ENV_KEY, "")
    if not value:
        return None
    key = value.encode("utf-8")
    if len(key) < MIN_KEY_BYTES:
        raise SignatureError(f"{ENV_KEY} must be at least {MIN_KEY_BYTES} bytes long")
    return key


def key_id(key: bytes) -> str:
    """Public id of a key (first 12 hex chars of its SHA-256): which key signed, never the key."""
    return hashlib.sha256(key).hexdigest()[:12]


def sign(data: bytes, key: bytes) -> dict[str, str]:
    return {
        "alg": ALGORITHM,
        "key_id": key_id(key),
        "sig": hmac.new(key, data, hashlib.sha256).hexdigest(),
    }


def verify(data: bytes, signature: Mapping[str, object], key: bytes) -> None:
    if signature.get("alg") != ALGORITHM:
        raise SignatureError(
            f"unsupported signature algorithm {signature.get('alg')!r} (expected {ALGORITHM})"
        )
    if signature.get("key_id") != key_id(key):
        raise SignatureError("the document was signed with a different key")
    expected = hmac.new(key, data, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(signature.get("sig", ""))):
        raise SignatureError(
            "invalid signature: the document was modified or not produced with this key"
        )


def sidecar(path: Path) -> Path:
    return path.with_name(path.name + ".sig")


def write_signature(path: Path, key: bytes) -> Path:
    target = sidecar(path)
    target.write_text(json.dumps(sign(path.read_bytes(), key)) + "\n", encoding="utf-8")
    return target


def verify_bytes(data: bytes, signature_path: Path, key: bytes) -> None:
    try:
        signature = json.loads(signature_path.read_text(encoding="utf-8"))
    except FileNotFoundError as exc:
        raise SignatureError(f"signature file not found: {signature_path}") from exc
    except (OSError, json.JSONDecodeError) as exc:
        raise SignatureError(f"unreadable signature file {signature_path}: {exc}") from exc
    if not isinstance(signature, dict):
        raise SignatureError(f"malformed signature file {signature_path}")
    verify(data, signature, key)
