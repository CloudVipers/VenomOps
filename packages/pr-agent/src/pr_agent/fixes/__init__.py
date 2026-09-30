"""One fixer per type of finding."""

from __future__ import annotations

from .base import AlreadyFixedError, Fixer, FixerError, FixOutcome
from .ebs_gp3 import EbsGp3Fixer
from .k8s_memory import K8sMemoryLimitFixer
from .required_tags import RequiredTagsFixer
from .s3_encryption import S3EncryptionFixer

FIXERS: tuple[Fixer, ...] = (S3EncryptionFixer(), RequiredTagsFixer(), EbsGp3Fixer(), K8sMemoryLimitFixer())


def get_fixer(finding_id: str) -> Fixer | None:
    """The fixer registered for a finding ``id`` (e.g. ``TF-S3-001``), or None."""
    for fixer in FIXERS:
        if finding_id in fixer.ids:
            return fixer
    return None


__all__ = ["FIXERS", "AlreadyFixedError", "Fixer", "FixerError", "FixOutcome", "get_fixer"]
