"""Build the labeled IaC benchmark (experiment E3) from the specification below.

    python build.py            # writes cases/<id>/main.tf + versions.tf and labels.json
    python build.py --plans    # also runs terraform init/plan/show -json to write cases/<id>/plan.json

Every case is a small Terraform configuration with seeded faults (or none: clean cases) and the labels of those faults.
Plans are produced with ``terraform init -backend=false`` + ``plan -refresh=false`` + ``show -json`` and dummy
credentials: nothing is ever applied and no AWS API is called (every resource is new).

Labels are the *seeded* faults, not an exhaustive audit: a reviewer may legitimately report more (threat to validity
documented in docs/investigacion/metodologia.md). ``also`` lists other addresses where the same fault may be reported.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

HERE = Path(__file__).resolve().parent
CASES = HERE / "cases"

VERSIONS = """terraform {
  required_version = ">= 1.5"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.0"
    }
  }
}

# Benchmark only: dummy credentials, nothing is ever applied.
provider "aws" {
  region                      = "us-east-1"
  skip_credentials_validation = true
  skip_metadata_api_check     = true
  skip_requesting_account_id  = true
}
"""


def tags(env: str, *, owner: bool = True) -> str:
    lines = [f'    Environment = "{env}"', '    Project     = "bench"']
    if owner:
        lines.insert(1, '    Owner       = "team-a"')
    return "  tags = {\n" + "\n".join(lines) + "\n  }"


@dataclass(frozen=True)
class Fault:
    resource: str
    category: str  # security | cost | reliability | operations
    severity: str
    description: str
    cwe: str = ""
    also: tuple[str, ...] = ()


@dataclass(frozen=True)
class Spec:
    id: str
    hcl: str
    faults: tuple[Fault, ...] = field(default_factory=tuple)


# ---- building blocks -------------------------------------------------------------------------------------------


def bucket(name: str, env: str, rname: str = "data") -> str:
    return f'resource "aws_s3_bucket" "{rname}" {{\n  bucket = "bench-{name}"\n{tags(env)}\n}}\n'


def bucket_hardening(
    rname: str = "data", *, versioning: bool = True, lifecycle: bool = True, encryption: bool = True
) -> str:
    out = ""
    if encryption:
        out += f"""
resource "aws_s3_bucket_server_side_encryption_configuration" "{rname}" {{
  bucket = aws_s3_bucket.{rname}.id
  rule {{
    apply_server_side_encryption_by_default {{
      sse_algorithm = "aws:kms"
    }}
  }}
}}
"""
    out += f"""
resource "aws_s3_bucket_public_access_block" "{rname}" {{
  bucket                  = aws_s3_bucket.{rname}.id
  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}}
"""
    if versioning:
        out += f"""
resource "aws_s3_bucket_versioning" "{rname}" {{
  bucket = aws_s3_bucket.{rname}.id
  versioning_configuration {{
    status = "Enabled"
  }}
}}
"""
    if lifecycle:
        out += f"""
resource "aws_s3_bucket_lifecycle_configuration" "{rname}" {{
  bucket = aws_s3_bucket.{rname}.id
  rule {{
    id     = "expire-old-versions"
    status = "Enabled"
    filter {{}}
    noncurrent_version_expiration {{
      noncurrent_days = 90
    }}
  }}
}}
"""
    return out


def sg(rname: str, env: str, port_from: int, port_to: int, protocol: str, cidr: str, desc: str) -> str:
    return f"""resource "aws_security_group" "{rname}" {{
  name        = "bench-{rname}"
  description = "{desc}"
  vpc_id      = "vpc-0123456789abcdef0"

  ingress {{
    description = "{desc}"
    from_port   = {port_from}
    to_port     = {port_to}
    protocol    = "{protocol}"
    cidr_blocks = ["{cidr}"]
  }}

  egress {{
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }}

{tags(env)}
}}
"""


def rds(env: str, **overrides: object) -> str:
    attrs: dict[str, object] = {
        "identifier": '"bench-orders"',
        "engine": '"postgres"',
        "engine_version": '"16"',
        "instance_class": '"db.t3.medium"',
        "allocated_storage": 50,
        "username": '"app"',
        "manage_master_user_password": "true",
        "storage_encrypted": "true",
        "multi_az": "true",
        "publicly_accessible": "false",
        "backup_retention_period": 14,
        "deletion_protection": "true",
        "skip_final_snapshot": "false",
        "final_snapshot_identifier": '"bench-orders-final"',
    }
    attrs.update(overrides)
    body = "\n".join(f"  {k} = {v}" for k, v in attrs.items())
    return f'resource "aws_db_instance" "orders" {{\n{body}\n{tags(env)}\n}}\n'


def instance(rname: str, env: str, itype: str = "t3.micro", *, tokens: str = "required", with_tags: bool = True) -> str:
    tag_block = f"\n{tags(env)}" if with_tags else ""
    return f"""resource "aws_instance" "{rname}" {{
  ami           = "ami-0123456789abcdef0"
  instance_type = "{itype}"

  metadata_options {{
    http_tokens = "{tokens}"
  }}

  root_block_device {{
    encrypted   = true
    volume_type = "gp3"
  }}
{tag_block}
}}
"""


def volume(env: str, vtype: str = "gp3", *, encrypted: bool = True, size: int = 50, iops: int | None = None,
           with_tags: bool = True) -> str:  # fmt: skip
    iops_line = f"\n  iops              = {iops}" if iops else ""
    tag_block = f"\n{tags(env)}" if with_tags else ""
    return f"""resource "aws_ebs_volume" "data" {{
  availability_zone = "us-east-1a"
  size              = {size}
  type              = "{vtype}"
  encrypted         = {str(encrypted).lower()}{iops_line}
{tag_block}
}}
"""


def iam_policy(rname: str, actions: str, resources: str) -> str:
    return f"""resource "aws_iam_policy" "{rname}" {{
  name = "bench-{rname}"
  policy = jsonencode({{
    Version = "2012-10-17"
    Statement = [{{
      Effect   = "Allow"
      Action   = {actions}
      Resource = {resources}
    }}]
  }})
{tags("prod")}
}}
"""


def network(env: str, nat_count: int, private_subnets: int = 3) -> str:
    nat_ref = "aws_nat_gateway.per_subnet[count.index].id" if nat_count > 1 else "aws_nat_gateway.main.id"
    nat_block = (
        f"""resource "aws_eip" "nat" {{
  count  = {nat_count}
  domain = "vpc"
{tags(env)}
}}

resource "aws_nat_gateway" "per_subnet" {{
  count         = {nat_count}
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public.id
{tags(env)}
}}
"""
        if nat_count > 1
        else f"""resource "aws_eip" "nat" {{
  domain = "vpc"
{tags(env)}
}}

resource "aws_nat_gateway" "main" {{
  allocation_id = aws_eip.nat.id
  subnet_id     = aws_subnet.public.id
{tags(env)}
}}
"""
    )
    return f"""resource "aws_vpc" "main" {{
  cidr_block = "10.20.0.0/16"
{tags(env)}
}}

resource "aws_internet_gateway" "main" {{
  vpc_id = aws_vpc.main.id
{tags(env)}
}}

resource "aws_subnet" "public" {{
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.20.0.0/24"
  availability_zone = "us-east-1a"
{tags(env)}
}}

resource "aws_subnet" "private" {{
  count             = {private_subnets}
  vpc_id            = aws_vpc.main.id
  cidr_block        = "10.20.${{count.index + 1}}.0/24"
  availability_zone = element(["us-east-1a", "us-east-1b", "us-east-1c"], count.index)
{tags(env)}
}}

{nat_block}
resource "aws_route_table" "private" {{
  count  = {private_subnets}
  vpc_id = aws_vpc.main.id
  route {{
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = {nat_ref}
  }}
{tags(env)}
}}
"""


# ---- the benchmark ---------------------------------------------------------------------------------------------

SPECS: list[Spec] = [
    # -- security
    Spec(
        "s3-public-acl",
        bucket("public-acl", "prod")
        + bucket_hardening(versioning=True).replace("block_public_acls       = true", "block_public_acls       = false")
        .replace("ignore_public_acls      = true", "ignore_public_acls      = false")
        + """
resource "aws_s3_bucket_ownership_controls" "data" {
  bucket = aws_s3_bucket.data.id
  rule {
    object_ownership = "BucketOwnerPreferred"
  }
}

resource "aws_s3_bucket_acl" "data" {
  depends_on = [aws_s3_bucket_ownership_controls.data]
  bucket     = aws_s3_bucket.data.id
  acl        = "public-read"
}
""",
        (Fault("aws_s3_bucket_acl.data", "security", "high", "Public-read ACL on a production bucket", "CWE-284",
               ("aws_s3_bucket.data", "aws_s3_bucket_public_access_block.data")),),  # fmt: skip
    ),
    Spec(
        "s3-public-policy",
        bucket("public-policy", "prod")
        + bucket_hardening().replace("block_public_policy     = true", "block_public_policy     = false")
        .replace("restrict_public_buckets = true", "restrict_public_buckets = false")
        + """
resource "aws_s3_bucket_policy" "data" {
  bucket = aws_s3_bucket.data.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = "*"
      Action    = "s3:GetObject"
      Resource  = "arn:aws:s3:::bench-public-policy/*"
    }]
  })
}
""",
        (Fault("aws_s3_bucket_policy.data", "security", "high", "Bucket policy grants s3:GetObject to everyone",
               "CWE-284", ("aws_s3_bucket.data", "aws_s3_bucket_public_access_block.data")),),  # fmt: skip
    ),
    Spec(
        "s3-public-access-block-disabled",
        bucket("bpa-off", "prod")
        + bucket_hardening()
        .replace("block_public_acls       = true", "block_public_acls       = false")
        .replace("block_public_policy     = true", "block_public_policy     = false")
        .replace("ignore_public_acls      = true", "ignore_public_acls      = false")
        .replace("restrict_public_buckets = true", "restrict_public_buckets = false"),
        (Fault("aws_s3_bucket_public_access_block.data", "security", "high",
               "Every public access block setting is disabled", "CWE-284", ("aws_s3_bucket.data",)),),  # fmt: skip
    ),
    Spec(
        "s3-no-encryption-config",
        bucket("no-sse", "prod") + bucket_hardening(encryption=False),
        (Fault("aws_s3_bucket.data", "security", "low",
               "No explicit server-side encryption with a customer-managed key (only the default SSE-S3)",
               "CWE-311"),),  # fmt: skip
    ),
    Spec(
        "sg-ssh-open",
        sg("admin", "prod", 22, 22, "tcp", "0.0.0.0/0", "SSH access"),
        (Fault("aws_security_group.admin", "security", "high", "SSH open to the Internet", "CWE-284"),),
    ),
    Spec(
        "sg-rdp-open",
        sg("windows", "prod", 3389, 3389, "tcp", "0.0.0.0/0", "RDP access"),
        (Fault("aws_security_group.windows", "security", "high", "RDP open to the Internet", "CWE-284"),),
    ),
    Spec(
        "sg-all-open",
        sg("any", "prod", 0, 0, "-1", "0.0.0.0/0", "Everything"),
        (Fault("aws_security_group.any", "security", "critical", "All ports and protocols open to the Internet",
               "CWE-284"),),  # fmt: skip
    ),
    Spec(
        "sg-database-open",
        sg("db", "prod", 5432, 5432, "tcp", "0.0.0.0/0", "PostgreSQL"),
        (Fault("aws_security_group.db", "security", "high", "Database port open to the Internet", "CWE-284"),),
    ),
    Spec(
        "rds-public",
        rds("prod", publicly_accessible="true"),
        (Fault("aws_db_instance.orders", "security", "high", "Production database is publicly accessible", "CWE-284"),),
    ),
    Spec(
        "rds-unencrypted",
        rds("prod", storage_encrypted="false"),
        (Fault("aws_db_instance.orders", "security", "high", "Database storage is not encrypted", "CWE-311"),),
    ),
    Spec(
        "ebs-unencrypted",
        volume("prod", encrypted=False),
        (Fault("aws_ebs_volume.data", "security", "medium", "EBS volume is not encrypted", "CWE-311"),),
    ),
    Spec(
        "iam-admin-policy",
        iam_policy("deploy", '"*"', '"*"'),
        (Fault("aws_iam_policy.deploy", "security", "critical", "Policy allows every action on every resource",
               "CWE-269"),),  # fmt: skip
    ),
    Spec(
        "iam-passrole-any",
        iam_policy("ci", '["iam:PassRole", "ec2:RunInstances"]', '"*"'),
        (Fault("aws_iam_policy.ci", "security", "high", "iam:PassRole on any role enables privilege escalation",
               "CWE-269"),),  # fmt: skip
    ),
    Spec(
        "iam-trust-anyone",
        """resource "aws_iam_role" "app" {
  name = "bench-app"
  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { AWS = "*" }
      Action    = "sts:AssumeRole"
    }]
  })
"""
        + tags("prod")
        + "\n}\n",
        (Fault("aws_iam_role.app", "security", "critical", "Any AWS principal can assume the role", "CWE-284"),),
    ),
    Spec(
        "iam-long-lived-key",
        f"""resource "aws_iam_user" "ci" {{
  name = "bench-ci"
{tags("prod")}
}}

resource "aws_iam_access_key" "ci" {{
  user = aws_iam_user.ci.name
}}
""",
        (Fault("aws_iam_access_key.ci", "security", "medium", "Long-lived access key for a CI user", "CWE-798",
               ("aws_iam_user.ci",)),),  # fmt: skip
    ),
    Spec(
        "ec2-imdsv1",
        instance("web", "prod", tokens="optional"),
        (Fault("aws_instance.web", "security", "medium", "IMDSv1 allowed (http_tokens = optional)", "CWE-918"),),
    ),
    Spec(
        "kms-no-rotation",
        f"""resource "aws_kms_key" "data" {{
  description         = "bench data key"
  enable_key_rotation = false
{tags("prod")}
}}
""",
        (Fault("aws_kms_key.data", "security", "medium", "Key rotation disabled", "CWE-320"),),
    ),
    Spec(
        "sqs-no-encryption",
        f"""resource "aws_sqs_queue" "orders" {{
  name                    = "bench-orders"
  sqs_managed_sse_enabled = false
{tags("prod")}
}}
""",
        (Fault("aws_sqs_queue.orders", "security", "medium", "Queue without server-side encryption", "CWE-311"),),
    ),
    Spec(
        "ssm-plaintext-secret",
        f"""resource "aws_ssm_parameter" "db_password" {{
  name  = "/bench/db_password"
  type  = "String"
  value = "placeholder-not-a-real-secret"
{tags("prod")}
}}
""",
        (Fault("aws_ssm_parameter.db_password", "security", "high", "Password stored as a plain String parameter",
               "CWE-312"),),  # fmt: skip
    ),
    Spec(
        "ecr-mutable-no-scan",
        f"""resource "aws_ecr_repository" "app" {{
  name                 = "bench-app"
  image_tag_mutability = "MUTABLE"

  image_scanning_configuration {{
    scan_on_push = false
  }}
{tags("prod")}
}}
""",
        (Fault("aws_ecr_repository.app", "security", "low", "Mutable tags and no vulnerability scan on push",
               "CWE-494"),),  # fmt: skip
    ),
    Spec(
        "sns-public-policy",
        f"""resource "aws_sns_topic" "events" {{
  name              = "bench-events"
  kms_master_key_id = "alias/aws/sns"
{tags("prod")}
}}

resource "aws_sns_topic_policy" "events" {{
  arn = aws_sns_topic.events.arn
  policy = jsonencode({{
    Version = "2012-10-17"
    Statement = [{{
      Effect    = "Allow"
      Principal = "*"
      Action    = "SNS:Publish"
      Resource  = "arn:aws:sns:us-east-1:123456789012:bench-events"
    }}]
  }})
}}
""",
        (Fault("aws_sns_topic_policy.events", "security", "high", "Anyone can publish to the topic", "CWE-284",
               ("aws_sns_topic.events",)),),  # fmt: skip
    ),
    # -- reliability
    Spec(
        "rds-single-az-prod",
        rds("prod", multi_az="false"),
        (Fault("aws_db_instance.orders", "reliability", "high", "Production database in a single AZ"),),
    ),
    Spec(
        "rds-no-backups",
        rds("prod", backup_retention_period=0),
        (Fault("aws_db_instance.orders", "reliability", "high", "Automated backups disabled"),),
    ),
    Spec(
        "rds-no-deletion-protection",
        rds("prod", deletion_protection="false"),
        (Fault("aws_db_instance.orders", "reliability", "medium", "Production database without deletion protection"),),
    ),
    Spec(
        "dynamodb-no-pitr",
        f"""resource "aws_dynamodb_table" "orders" {{
  name         = "bench-orders"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {{
    name = "id"
    type = "S"
  }}

  point_in_time_recovery {{
    enabled = false
  }}
{tags("prod")}
}}
""",
        (Fault("aws_dynamodb_table.orders", "reliability", "medium", "Point-in-time recovery disabled in production"),),
    ),
    Spec(
        "asg-single-az",
        f"""resource "aws_launch_template" "web" {{
  name_prefix   = "bench-web-"
  image_id      = "ami-0123456789abcdef0"
  instance_type = "t3.small"

  metadata_options {{
    http_tokens = "required"
  }}
{tags("prod")}
}}

resource "aws_autoscaling_group" "web" {{
  name               = "bench-web"
  min_size           = 2
  max_size           = 4
  availability_zones = ["us-east-1a"]

  launch_template {{
    id      = aws_launch_template.web.id
    version = "$Latest"
  }}

  tag {{
    key                 = "Environment"
    value               = "prod"
    propagate_at_launch = true
  }}
}}
""",
        (Fault("aws_autoscaling_group.web", "reliability", "medium", "Production Auto Scaling group in one AZ"),),
    ),
    Spec(
        "s3-no-versioning-prod",
        bucket("orders-data", "prod") + bucket_hardening(versioning=False, lifecycle=False),
        (Fault("aws_s3_bucket.data", "reliability", "medium", "Production data bucket without versioning"),),
    ),
    Spec(
        "nat-single-az-prod",
        network("prod", nat_count=1),
        (Fault("aws_nat_gateway.main", "reliability", "medium",
               "One NAT Gateway for private subnets in three AZs (single point of failure)",
               ("aws_route_table.private",)),),  # fmt: skip
    ),
    # -- cost
    Spec(
        "ebs-gp2",
        volume("prod", "gp2"),
        (Fault("aws_ebs_volume.data", "cost", "low", "gp2 volume: gp3 is cheaper for the same baseline"),),
    ),
    Spec(
        "ec2-oversized-dev",
        instance("dev_box", "dev", "m5.8xlarge"),
        (Fault("aws_instance.dev_box", "cost", "medium", "m5.8xlarge for a development instance"),),
    ),
    Spec(
        "nat-per-subnet-dev",
        network("dev", nat_count=3),
        (Fault("aws_nat_gateway.per_subnet", "cost", "medium", "One NAT Gateway per subnet in a dev environment",
               ("aws_eip.nat",)),),  # fmt: skip
    ),
    Spec(
        "s3-logs-no-lifecycle",
        bucket("access-logs", "prod", "logs") + bucket_hardening("logs", versioning=True, lifecycle=False),
        (Fault("aws_s3_bucket.logs", "cost", "low", "Log bucket with versioning and no lifecycle expiration",
               ("aws_s3_bucket_versioning.logs",)),),  # fmt: skip
    ),
    Spec(
        "eip-unattached",
        f"""resource "aws_eip" "spare" {{
  domain = "vpc"
{tags("prod")}
}}
""",
        (Fault("aws_eip.spare", "cost", "low", "Elastic IP not attached to anything"),),
    ),
    Spec(
        "ebs-io1-dev",
        volume("dev", "io1", size=200, iops=10000),
        (Fault("aws_ebs_volume.data", "cost", "medium", "Provisioned IOPS (io1, 10000) in dev"),),
    ),
    Spec(
        "rds-oversized-dev",
        rds("dev", instance_class='"db.r5.4xlarge"'),
        (Fault("aws_db_instance.orders", "cost", "medium", "db.r5.4xlarge for a development database"),),
    ),
    Spec(
        "log-group-no-retention",
        f"""resource "aws_cloudwatch_log_group" "app" {{
  name = "/bench/app"
{tags("prod")}
}}
""",
        (Fault("aws_cloudwatch_log_group.app", "cost", "low", "Logs never expire (no retention)"),),
    ),
    # -- operations
    Spec(
        "sqs-missing-tags",
        """resource "aws_sqs_queue" "jobs" {
  name                    = "bench-jobs"
  sqs_managed_sse_enabled = true
}
""",
        (Fault("aws_sqs_queue.jobs", "operations", "low", "No tags (owner, environment, project)"),),
    ),
    Spec(
        "ebs-missing-tags",
        volume("prod", with_tags=False),
        (Fault("aws_ebs_volume.data", "operations", "low", "No tags (owner, environment, project)"),),
    ),
    Spec(
        "vpc-no-flow-logs",
        f"""resource "aws_vpc" "main" {{
  cidr_block = "10.30.0.0/16"
{tags("prod")}
}}
""",
        (Fault("aws_vpc.main", "operations", "medium", "Production VPC without flow logs", "CWE-778"),),
    ),
    # -- several faults in one plan
    Spec(
        "rds-combo",
        rds("prod", publicly_accessible="true", storage_encrypted="false", multi_az="false"),
        (
            Fault("aws_db_instance.orders", "security", "critical", "Public and unencrypted database", "CWE-284"),
            Fault("aws_db_instance.orders", "reliability", "high", "Single-AZ production database"),
        ),
    ),
    Spec(
        "web-combo",
        sg("web", "dev", 22, 22, "tcp", "0.0.0.0/0", "SSH access")
        + "\n"
        + instance("web", "dev", "m5.4xlarge", with_tags=False),
        (
            Fault("aws_security_group.web", "security", "high", "SSH open to the Internet", "CWE-284"),
            Fault("aws_instance.web", "cost", "medium", "m5.4xlarge in dev"),
            Fault("aws_instance.web", "operations", "low", "Instance without tags"),
        ),
    ),
    Spec(
        "s3-combo",
        bucket("combo", "prod")
        + bucket_hardening(versioning=False).replace("block_public_policy     = true", "block_public_policy     = false")
        + """
resource "aws_s3_bucket_policy" "data" {
  bucket = aws_s3_bucket.data.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = "*"
      Action    = ["s3:GetObject", "s3:PutObject"]
      Resource  = "arn:aws:s3:::bench-combo/*"
    }]
  })
}
""",
        (
            Fault("aws_s3_bucket_policy.data", "security", "critical", "Anyone can read and write objects", "CWE-284",
                  ("aws_s3_bucket.data", "aws_s3_bucket_public_access_block.data")),  # fmt: skip
            Fault("aws_s3_bucket.data", "reliability", "medium", "Production bucket without versioning"),
        ),
    ),
    # -- clean cases (no seeded fault)
    Spec("clean-s3", bucket("clean", "prod") + bucket_hardening()),
    Spec("clean-sg", sg("internal", "prod", 443, 443, "tcp", "10.0.0.0/8", "HTTPS from the corporate network")),
    Spec("clean-rds", rds("prod")),
    Spec("clean-ebs", volume("prod")),
    Spec(
        "clean-iam",
        iam_policy("reader", '["s3:GetObject"]', '"arn:aws:s3:::bench-clean/*"'),
    ),
    Spec(
        "clean-kms",
        f"""resource "aws_kms_key" "data" {{
  description         = "bench data key"
  enable_key_rotation = true
{tags("prod")}
}}
""",
    ),
    Spec(
        "clean-dynamodb",
        f"""resource "aws_dynamodb_table" "orders" {{
  name         = "bench-orders"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "id"

  attribute {{
    name = "id"
    type = "S"
  }}

  point_in_time_recovery {{
    enabled = true
  }}
{tags("prod")}
}}
""",
    ),
    Spec("clean-instance", instance("tool", "dev")),
    Spec(
        "clean-log-group",
        f"""resource "aws_cloudwatch_log_group" "app" {{
  name              = "/bench/app"
  retention_in_days = 30
{tags("prod")}
}}
""",
    ),
    Spec(
        "clean-ssm",
        f"""resource "aws_ssm_parameter" "api_url" {{
  name  = "/bench/api_url"
  type  = "String"
  value = "https://api.example.com"
{tags("prod")}
}}
""",
    ),
    Spec(
        "clean-sqs",
        f"""resource "aws_sqs_queue" "jobs" {{
  name                    = "bench-jobs"
  sqs_managed_sse_enabled = true
{tags("prod")}
}}
""",
    ),
    Spec(
        "clean-vpc",
        f"""resource "aws_vpc" "main" {{
  cidr_block = "10.40.0.0/16"
{tags("prod")}
}}

resource "aws_flow_log" "main" {{
  vpc_id               = aws_vpc.main.id
  traffic_type         = "ALL"
  log_destination_type = "s3"
  log_destination      = "arn:aws:s3:::bench-flow-logs"
{tags("prod")}
}}
""",
    ),
    Spec(
        "clean-ecr",
        f"""resource "aws_ecr_repository" "app" {{
  name                 = "bench-app"
  image_tag_mutability = "IMMUTABLE"

  image_scanning_configuration {{
    scan_on_push = true
  }}
{tags("prod")}
}}
""",
    ),
]


# ---- writing -----------------------------------------------------------------------------------------------------


def labels_document() -> dict[str, object]:
    return {
        "version": "1.0.0",
        "description": "Seeded faults of the IaC benchmark (experiment E3). Regenerate with build.py; do not edit.",
        "cases": [
            {
                "id": spec.id,
                "dir": f"cases/{spec.id}",
                "plan": f"cases/{spec.id}/plan.json",
                "expected": [
                    {
                        "resource": f.resource,
                        "category": f.category,
                        "severity": f.severity,
                        "description": f.description,
                        **({"cwe": f.cwe} if f.cwe else {}),
                        **({"also": list(f.also)} if f.also else {}),
                    }
                    for f in spec.faults
                ],
            }
            for spec in SPECS
        ],
    }


def write_sources() -> None:
    ids = [s.id for s in SPECS]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate case ids")
    for spec in SPECS:
        case = CASES / spec.id
        case.mkdir(parents=True, exist_ok=True)
        (case / "versions.tf").write_text(VERSIONS, encoding="utf-8")
        (case / "main.tf").write_text(spec.hcl.strip() + "\n", encoding="utf-8")
    (HERE / "labels.json").write_text(json.dumps(labels_document(), indent=2) + "\n", encoding="utf-8")


def write_plans(only: list[str]) -> None:
    """terraform init -backend=false / plan -refresh=false / show -json, in a temporary copy of each case."""
    terraform = shutil.which("terraform")
    if terraform is None:
        raise SystemExit("terraform is not in PATH")
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "TF_PLUGIN_CACHE_DIR": os.environ.get("TF_PLUGIN_CACHE_DIR", str(Path.home() / ".terraform.d/plugin-cache")),
        "TF_IN_AUTOMATION": "1",
        # Dummy credentials (AWS documentation example values): nothing is applied and no API is called.
        "AWS_ACCESS_KEY_ID": "AKIAIOSFODNN7EXAMPLE",
        "AWS_SECRET_ACCESS_KEY": "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY",
        "AWS_EC2_METADATA_DISABLED": "true",
    }
    Path(env["TF_PLUGIN_CACHE_DIR"]).mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="bench-") as tmp:
        # One shared working directory: every case uses the same provider, so `init` runs once (Terraform ignores
        # the plugin cache without a lock file and would download the AWS provider again for every case).
        work = Path(tmp)

        def tf(case_id: str, *args: str) -> str:
            proc = subprocess.run(  # noqa: S603 - fixed terraform argv: init/plan/show only, never apply
                [terraform, *args], cwd=work, env=env, capture_output=True, text=True, check=False
            )
            if proc.returncode != 0:
                raise SystemExit(f"{case_id}: terraform {args[0]} failed:\n{proc.stderr or proc.stdout}")
            return proc.stdout

        shutil.copy(CASES / SPECS[0].id / "versions.tf", work / "versions.tf")
        tf("init", "init", "-backend=false", "-input=false", "-no-color")
        for spec in SPECS:
            if only and spec.id not in only:
                continue
            shutil.copy(CASES / spec.id / "main.tf", work / "main.tf")
            shutil.copy(CASES / spec.id / "versions.tf", work / "versions.tf")
            tf(spec.id, "plan", "-input=false", "-no-color", "-lock=false", "-refresh=false", "-out=plan.out")
            plan = json.loads(tf(spec.id, "show", "-json", "plan.out"))
            (CASES / spec.id / "plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
            print(f"plan: {spec.id}", flush=True)


if __name__ == "__main__":
    write_sources()
    if "--plans" in sys.argv:
        write_plans([a for a in sys.argv[1:] if not a.startswith("--")])
    faults = sum(len(s.faults) for s in SPECS)
    print(f"{len(SPECS)} casos ({sum(1 for s in SPECS if not s.faults)} limpios), {faults} fallas etiquetadas")
