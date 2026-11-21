"""Hard security boundaries, enforced in code (never only in the prompt).

Nothing in pr-agent may run a command or touch a path except through this module:

* ``SafeRunner`` executes only an explicit allowlist of ``terraform`` and ``git`` invocations. There is
  no ``terraform apply/destroy``, no force/delete pushes, no push to a protected branch and no merge.
* ``resolve_in_repo`` confines every file access to the target repository and refuses secrets
  (state files, tfvars) and VCS/provider internals.
"""

from __future__ import annotations

import os
import re
import subprocess  # noqa: S404 - this module is the single, audited place that spawns processes
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path


class SecurityError(Exception):
    """Base class for refused operations."""


class CommandNotAllowedError(SecurityError):
    """The requested command is not in the allowlist."""


class PathNotAllowedError(SecurityError):
    """The requested path is outside the repository or is a protected file."""


# ---------------------------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------------------------

_BLOCKED_DIRS = {".git", ".terraform"}
_BLOCKED_SUFFIXES = (".tfstate", ".tfstate.backup", ".tfvars", ".tfvars.json", ".pem", ".key")
_BLOCKED_NAMES = {".env", "terraform.tfstate", "credentials"}


def resolve_in_repo(repo: Path, relative: str) -> Path:
    """Resolve ``relative`` inside ``repo`` or raise :class:`PathNotAllowedError`.

    Rejects absolute paths, ``..`` escapes, symlinks that leave the repo, and files that may hold
    secrets or are VCS/provider internals.
    """
    if not relative or "\x00" in relative:
        raise PathNotAllowedError("empty or invalid path")
    if Path(relative).is_absolute():
        raise PathNotAllowedError(f"absolute paths are not allowed: {relative!r}")

    root = repo.resolve()
    target = (root / relative).resolve()
    if not target.is_relative_to(root):
        raise PathNotAllowedError(f"path escapes the repository: {relative!r}")

    parts = target.relative_to(root).parts
    if any(p in _BLOCKED_DIRS for p in parts):
        raise PathNotAllowedError(f"path is inside a protected directory: {relative!r}")
    name = target.name.lower()
    if name in _BLOCKED_NAMES or name.endswith(_BLOCKED_SUFFIXES):
        raise PathNotAllowedError(f"refusing to access a potential secret: {relative!r}")
    return target


# ---------------------------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------------------------

PROTECTED_BRANCHES = frozenset({"main", "master", "develop", "production", "prod", "release"})
_BRANCH_RE = re.compile(r"^fix/[a-z0-9][a-z0-9._-]{0,80}$")

_TF_FLAGS_INIT = frozenset({"-backend=false", "-input=false", "-no-color"})
_TF_FLAGS_VALIDATE = frozenset({"-no-color"})
_TF_FLAGS_PLAN = frozenset({"-input=false", "-no-color", "-lock=false", "-refresh=false"})


def _check_terraform(args: Sequence[str]) -> None:
    if not args:
        raise CommandNotAllowedError("terraform requires a subcommand")
    sub, flags = args[0], list(args[1:])
    allowed = {"init": _TF_FLAGS_INIT, "validate": _TF_FLAGS_VALIDATE, "plan": _TF_FLAGS_PLAN}.get(sub)
    if allowed is None:
        raise CommandNotAllowedError(f"terraform {sub!r} is not allowed (only init -backend=false, validate and plan)")
    bad = [f for f in flags if f not in allowed]
    if bad:
        raise CommandNotAllowedError(f"terraform {sub}: flags not allowed: {bad}")
    if sub == "init" and "-backend=false" not in flags:
        # `init` is only needed so `validate` can load providers; it must never touch remote state.
        raise CommandNotAllowedError("terraform init is only allowed with -backend=false")


def _check_branch(name: str) -> None:
    if name in PROTECTED_BRANCHES or not _BRANCH_RE.match(name):
        raise CommandNotAllowedError(
            f"branch {name!r} is not allowed (must match fix/<slug>, never a protected branch)"
        )


def _check_git(args: Sequence[str]) -> None:
    if not args:
        raise CommandNotAllowedError("git requires a subcommand")
    a = list(args)
    match a:
        case ["rev-parse", "--show-toplevel"] | ["rev-parse", "--abbrev-ref", "HEAD"]:
            return
        case ["status", "--porcelain"]:
            return
        case ["diff", *rest] if _diff_ok(rest):
            return
        case ["remote", "get-url", "origin"]:
            return
        case ["switch", "-c", branch] | ["checkout", "-b", branch]:
            _check_branch(branch)
            return
        case ["switch", branch]:
            # Returning to a branch we were on. Only `switch` (it cannot restore files, unlike
            # `checkout <path>`, which would discard local changes) and never with options.
            if branch.startswith("-"):
                raise CommandNotAllowedError(f"git switch: option not allowed: {branch}")
            return
        case ["add", "--", *paths] if paths and all(_plain_path(p) for p in paths):
            return
        case ["commit", "-m", message] if message.strip():
            return
        case ["push", "-u", "origin", branch] | ["push", "origin", branch]:
            _check_branch(branch)
            return
        case ["branch", "-D", branch]:
            _check_branch(branch)  # only our own fix/* branches, only on cleanup after an abort
            return
    raise CommandNotAllowedError(f"git command not allowed: git {' '.join(a)}")


_DIFF_OPTIONS = frozenset({"--no-color", "--stat", "--name-only", "--cached"})


def _diff_ok(rest: Sequence[str]) -> bool:
    """`git diff` is read-only: known display options, optionally followed by `-- <explicit paths>`."""
    flags, paths = (
        (list(rest[: rest.index("--")]), list(rest[rest.index("--") + 1 :])) if "--" in rest else (list(rest), [])
    )
    return all(f in _DIFF_OPTIONS for f in flags) and all(_plain_path(p) for p in paths)


def _plain_path(p: str) -> bool:
    """Explicit file paths only: no globs, no `.`/`-A` style wildcards, no options."""
    parts = Path(p).parts
    return (
        bool(p)
        and not p.startswith("-")
        and not Path(p).is_absolute()
        and ".." not in parts
        and p != "."
        and not any(c in p for c in "*?[]")
    )


_CHECKERS = {"terraform": _check_terraform, "git": _check_git}


def check_command(argv: Sequence[str]) -> None:
    """Raise :class:`CommandNotAllowedError` unless ``argv`` is an allowlisted invocation."""
    if not argv:
        raise CommandNotAllowedError("empty command")
    program = argv[0]
    checker = _CHECKERS.get(program)  # exact names only: no paths, no shells, no interpreters
    if checker is None:
        raise CommandNotAllowedError(f"program {program!r} is not allowed")
    checker(argv[1:])


@dataclass(frozen=True)
class CommandResult:
    argv: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    @property
    def output(self) -> str:
        return (self.stdout + ("\n" + self.stderr if self.stderr.strip() else "")).strip()


_ENV_PASSTHROUGH = (
    "PATH",
    "HOME",
    "LANG",
    "TMPDIR",
    "SSL_CERT_FILE",
    "HTTPS_PROXY",
    "HTTP_PROXY",
    "NO_PROXY",
)


# AWS settings that are not credentials and do not point at them: kept even when credentials are withheld.
_AWS_NON_SECRET = frozenset({"AWS_REGION", "AWS_DEFAULT_REGION"})


def _sanitized_env(extra: Mapping[str, str] | None, *, cloud_credentials: bool = True) -> dict[str, str]:
    """Minimal environment for a child process.

    With ``cloud_credentials`` the ``AWS_*`` variables pass through (``terraform plan`` configures the provider and
    reads data sources with them). Without it, no credential reaches the child: the ``AWS_*`` variables are dropped
    (except the region) and the SDK is pointed away from ``~/.aws`` and the instance metadata service, because
    ``HOME`` alone would let the provider pick up the default profile.
    """
    env = {k: os.environ[k] for k in _ENV_PASSTHROUGH if k in os.environ}
    env.update({k: v for k, v in os.environ.items() if k.startswith("TF_")})
    aws = {k: v for k, v in os.environ.items() if k.startswith("AWS_")}
    if cloud_credentials:
        env.update(aws)
    else:
        env.update({k: v for k, v in aws.items() if k in _AWS_NON_SECRET})
        env["AWS_SHARED_CREDENTIALS_FILE"] = os.devnull
        env["AWS_CONFIG_FILE"] = os.devnull
        env["AWS_EC2_METADATA_DISABLED"] = "true"
    env["TF_IN_AUTOMATION"] = "1"
    env["TF_INPUT"] = "0"
    env["GIT_TERMINAL_PROMPT"] = "0"
    if extra:
        env.update(extra)
    return env


def needs_cloud_credentials(argv: Sequence[str]) -> bool:
    """Only ``terraform plan`` may need AWS credentials; ``init -backend=false``, ``validate`` and git never do."""
    return len(argv) >= 2 and argv[0] == "terraform" and argv[1] == "plan"


class SafeRunner:
    """Runs allowlisted commands in a fixed working directory, without a shell."""

    def __init__(
        self,
        cwd: Path,
        *,
        timeout: int = 600,
        env: Mapping[str, str] | None = None,
        cloud_credentials: bool = True,
    ) -> None:
        self.cwd = cwd
        self.timeout = timeout
        self._extra_env = dict(env or {})
        # False for the LLM agent: what it wrote is evaluated by `terraform plan`, so it runs without AWS credentials.
        self.cloud_credentials = cloud_credentials

    def env_for(self, argv: Sequence[str]) -> dict[str, str]:
        """The environment ``argv`` runs with: AWS credentials only for ``terraform plan`` and only if allowed."""
        return _sanitized_env(
            self._extra_env, cloud_credentials=self.cloud_credentials and needs_cloud_credentials(argv)
        )

    def run(self, argv: Sequence[str]) -> CommandResult:
        check_command(argv)
        try:
            proc = subprocess.run(  # noqa: S603 - argv validated by check_command, no shell
                list(argv),
                cwd=self.cwd,
                env=self.env_for(argv),
                capture_output=True,
                text=True,
                timeout=self.timeout,
                check=False,
            )
        except FileNotFoundError:
            # Seen on a clean machine: the tool is simply not installed. Report it, do not crash with a traceback.
            return CommandResult(
                tuple(argv),
                127,
                "",
                f"`{argv[0]}` was not found on PATH; install it (terraform is needed to validate the fix)",
            )
        return CommandResult(tuple(argv), proc.returncode, proc.stdout, proc.stderr)
