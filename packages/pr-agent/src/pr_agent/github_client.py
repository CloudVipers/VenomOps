"""GitHub access limited to what pr-agent needs: read the default branch and OPEN a pull request.

There is deliberately no merge/approve/close/delete method anywhere: a human reviews and merges.
"""

from __future__ import annotations

import os
import re
from typing import Protocol

from github import Auth, Github

_REMOTE_RE = re.compile(r"(?:github\.com[:/])(?P<owner>[\w.-]+)/(?P<repo>[\w.-]+?)(?:\.git)?/?$")


class PullRequestClient(Protocol):
    """What the workflow needs from GitHub (mockable in tests)."""

    def default_branch(self, slug: str) -> str: ...

    def open_pull_request(self, slug: str, *, title: str, body: str, head: str, base: str) -> str: ...


def parse_remote(url: str) -> str:
    """``https://github.com/o/r.git`` or ``git@github.com:o/r.git`` -> ``o/r``."""
    m = _REMOTE_RE.search(url.strip())
    if not m:
        raise ValueError(f"cannot derive a GitHub owner/repo from remote {url!r}; pass --github-repo")
    return f"{m.group('owner')}/{m.group('repo')}"


class PyGithubClient:
    """:class:`PullRequestClient` backed by PyGithub; the token comes from ``GITHUB_TOKEN``."""

    def __init__(self, token: str | None = None) -> None:
        token = token or os.environ.get("GITHUB_TOKEN")
        if not token:
            raise RuntimeError("GITHUB_TOKEN is not set (needed to open the pull request)")
        self._gh = Github(auth=Auth.Token(token))

    def default_branch(self, slug: str) -> str:
        return str(self._gh.get_repo(slug).default_branch)

    def open_pull_request(self, slug: str, *, title: str, body: str, head: str, base: str) -> str:
        pr = self._gh.get_repo(slug).create_pull(title=title, body=body, head=head, base=base, draft=False)
        return str(pr.html_url)
