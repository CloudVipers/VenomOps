"""One fixer per type of finding."""

from __future__ import annotations

from .base import AlreadyFixedError, Fixer, FixerError, FixOutcome
from .ebs_gp3 import EbsGp3Fixer
from .k8s_memory import K8sMemoryLimitFixer
from .required_tags import RequiredTagsFixer
from .s3_encryption import S3EncryptionFixer

FIXERS: tuple[Fixer, ...] = (S3EncryptionFixer(), RequiredTagsFixer(), EbsGp3Fixer(), K8sMemoryLimitFixer())


# The venom-doctor rules were first published as KD-K8S-* (when the tool was called kdoctor) and are now VD-K8S-*.
# Findings saved before the rename still carry the old prefix, so they keep working: the registry reads them as the
# current id.
_LEGACY_ID_PREFIXES: tuple[tuple[str, str], ...] = (("KD-K8S-", "VD-K8S-"),)


def current_id(finding_id: str) -> str:
    """The id a finding is known by today (maps the legacy ``KD-K8S-*`` prefix to ``VD-K8S-*``)."""
    for old, new in _LEGACY_ID_PREFIXES:
        if finding_id.startswith(old):
            return new + finding_id[len(old) :]
    return finding_id


def get_fixer(finding_id: str) -> Fixer | None:
    """The fixer registered for a finding ``id`` (e.g. ``TF-S3-001``), or None."""
    finding_id = current_id(finding_id)
    for fixer in FIXERS:
        if finding_id in fixer.ids:
            return fixer
    return None


__all__ = ["FIXERS", "AlreadyFixedError", "Fixer", "FixerError", "FixOutcome", "current_id", "get_fixer"]
