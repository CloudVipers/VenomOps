from __future__ import annotations

from pathlib import Path

import pytest

from findings_schema.signature import (
    ENV_KEY,
    SignatureError,
    key_from_env,
    key_id,
    sidecar,
    sign,
    verify,
    verify_bytes,
    write_signature,
)

# Same vector as go/signature_test.go: both implementations must agree byte for byte.
KEY = b"test-key-0123456789abcdef0123456789abcdef"
DATA = b'[{"id": "X"}]\n'
SIG = "e270cdfd6eef0d22b8660eb89f7fee637d1e65b6f2abbc24d8a754ed99801d5d"


def test_sign_matches_the_shared_vector() -> None:
    assert sign(DATA, KEY) == {"alg": "HMAC-SHA256", "key_id": "6a41f8cfb764", "sig": SIG}
    assert key_id(KEY) == "6a41f8cfb764"
    verify(DATA, sign(DATA, KEY), KEY)


@pytest.mark.parametrize(
    "data,signature,key,message",
    [
        (b"[]\n", {"alg": "HMAC-SHA256", "key_id": "6a41f8cfb764", "sig": SIG}, KEY, "modified"),
        (
            DATA,
            {"alg": "HMAC-SHA256", "key_id": "6a41f8cfb764", "sig": SIG},
            b"x" * 40,
            "different key",
        ),
        (DATA, {"alg": "none", "key_id": "6a41f8cfb764", "sig": SIG}, KEY, "algorithm"),
    ],
)
def test_verify_rejects(data: bytes, signature: dict[str, str], key: bytes, message: str) -> None:
    with pytest.raises(SignatureError, match=message):
        verify(data, signature, key)


def test_sidecar_roundtrip_and_tampering(tmp_path: Path) -> None:
    doc = tmp_path / "findings.json"
    doc.write_bytes(DATA)
    assert write_signature(doc, KEY) == sidecar(doc) == tmp_path / "findings.json.sig"
    verify_bytes(doc.read_bytes(), sidecar(doc), KEY)
    with pytest.raises(SignatureError, match="modified"):
        verify_bytes(DATA.replace(b"X", b"Y"), sidecar(doc), KEY)
    with pytest.raises(SignatureError, match="not found"):
        verify_bytes(DATA, tmp_path / "missing.sig", KEY)


def test_key_from_env() -> None:
    assert key_from_env({}) is None
    assert key_from_env({ENV_KEY: KEY.decode()}) == KEY
    with pytest.raises(SignatureError, match="at least 32"):
        key_from_env({ENV_KEY: "short"})
