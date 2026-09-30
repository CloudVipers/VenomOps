"""TF-S3-001: S3 bucket without server-side encryption -> add SSE-S3 (AES256) configuration."""

from __future__ import annotations

from typing import ClassVar

from findings_schema import Finding

from .. import hcl
from ..tools import ToolBox
from .base import AlreadyFixedError, FixerError, FixOutcome, locate_resource

ENCRYPTION_TYPE = "aws_s3_bucket_server_side_encryption_configuration"

SNIPPET = """resource "{etype}" "{name}" {{
  bucket = aws_s3_bucket.{name}.id

  rule {{
    apply_server_side_encryption_by_default {{
      sse_algorithm = "AES256"
    }}
  }}
}}"""


class S3EncryptionFixer:
    ids: ClassVar[frozenset[str]] = frozenset({"TF-S3-001"})
    title: ClassVar[str] = "S3 bucket sin cifrado en reposo"

    def apply(self, finding: Finding, tools: ToolBox) -> FixOutcome:
        path, _, name = locate_resource(tools, finding, frozenset({"aws_s3_bucket"}))

        for other in tools.list_files("**/*.tf"):
            text = tools.read_file(other)
            if hcl.has_resource(text, ENCRYPTION_TYPE, name):
                raise AlreadyFixedError(f"{ENCRYPTION_TYPE}.{name} already exists in {other}")
            parsed = hcl.parse(text)
            for entry in parsed.get("resource", []):
                enc = hcl._normalize(entry).get(ENCRYPTION_TYPE, {})  # noqa: SLF001 - same package
                for attrs in enc.values():
                    if f"aws_s3_bucket.{name}." in str(attrs.get("bucket", "")):
                        raise AlreadyFixedError(f"bucket {name} is already covered by an encryption configuration")
        bucket_text = tools.read_file(path)
        block = hcl.find_resource(bucket_text, "aws_s3_bucket", name)
        if block is not None and "server_side_encryption_configuration" in bucket_text[block.start : block.end]:
            raise AlreadyFixedError(f"aws_s3_bucket.{name} already has an inline encryption configuration")

        if not tools.edit_hcl(
            path,
            {"op": "append_block", "content": SNIPPET.format(etype=ENCRYPTION_TYPE, name=name)},
        ):
            raise FixerError("the edit produced no change")
        return FixOutcome(
            summary=f"Habilitar cifrado SSE-S3 (AES256) en el bucket `{name}`.",
            details=[
                f"Se agrega `{ENCRYPTION_TYPE}.{name}` en `{path}` asociado a `aws_s3_bucket.{name}`.",
                "Usa claves administradas por S3 (AES256): sin costo adicional ni cambios en las aplicaciones.",
                'Para SSE-KMS con clave propia: `sse_algorithm = "aws:kms"` y `kms_master_key_id`.',
            ],
            risk="low",
            files=frozenset({path}),
        )
