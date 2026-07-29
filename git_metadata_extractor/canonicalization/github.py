"""Shared GitHub canonical-URL helpers.

Three identifier flavours, all rooted at ``https://github.com/``:

  - user:   ``https://github.com/<username>``
  - org:    ``https://github.com/<handle>`` (same shape as user; the
            User/Organization distinction is in GitHub's `type` field,
            not the URL — so `github_user_iri` and `github_org_iri`
            produce identical IRIs given identical input. The two
            helpers exist for call-site readability.)
  - repo:   ``https://github.com/<owner>/<repo>``

Every helper accepts any reasonable input shape (bare handle, URL,
trailing slash, leading `@`, whitespace) and returns the canonical
URL form (or ``None`` on malformed input). Idempotent on canonical
input.

GitHub handle validation rule (per github.com docs): up to 39 chars,
alphanumeric and hyphens, no leading/trailing hyphen, no consecutive
hyphens. We enforce this via regex in the bare-handle path; URL-form
input is left to schema-level validation downstream (the URL host
already constrains shape).
"""

from __future__ import annotations

import re

_BASE = "https://github.com/"

# GitHub username/org-handle rule: 1-39 chars, alphanumeric or hyphens,
# can't start or end with hyphen, no double hyphens. Lenient: we just
# require alphanumeric + single hyphens, max 39 chars.
_HANDLE_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9])){0,38}$")

# Repo name rule: 1-100 chars, alphanumeric, hyphen, underscore, dot.
# Real GitHub also allows other chars but these cover the >99% case.
_REPO_NAME_RE = re.compile(r"^[A-Za-z0-9_.][A-Za-z0-9_.-]{0,99}$")


def _normalise_url_input(value: str) -> str:
    """Strip leading whitespace, the optional `@` prefix many
    profile-style copies carry, and trailing slash."""
    s = value.strip()
    if s.startswith("@"):
        s = s[1:].strip()
    return s.rstrip("/")


def github_user_iri(value: str | None) -> str | None:
    """Canonical GitHub user URL. Accepts bare handle (`caviri`) or
    already-canonical URL. Returns None on malformed input.

    Org and user URLs are identical in shape — see module docstring;
    use `github_org_iri` at the call site if you want to express
    organization intent in the code."""
    if not isinstance(value, str):
        return None
    s = _normalise_url_input(value)
    if not s:
        return None
    if s.lower().startswith(_BASE):
        rest = s[len(_BASE):]
        # Reject anything that looks like a repo (contains `/`).
        if "/" in rest:
            return None
        if not _HANDLE_RE.fullmatch(rest):
            return None
        return _BASE + rest
    # Bare handle.
    if "/" in s:
        return None
    if not _HANDLE_RE.fullmatch(s):
        return None
    return _BASE + s


def github_org_iri(value: str | None) -> str | None:
    """Canonical GitHub org URL. Functionally equivalent to
    `github_user_iri` (same URL shape); separate name for caller-side
    readability."""
    return github_user_iri(value)


def github_repo_iri(value: str | None) -> str | None:
    """Canonical GitHub repository URL. Accepts bare `owner/repo`,
    `@owner/repo`, or already-canonical URL. Returns None on
    malformed input."""
    if not isinstance(value, str):
        return None
    s = _normalise_url_input(value)
    if not s:
        return None
    if s.lower().startswith(_BASE):
        rest = s[len(_BASE):]
    else:
        rest = s
    parts = rest.split("/")
    if len(parts) != 2:
        return None
    owner, repo = parts
    if not _HANDLE_RE.fullmatch(owner):
        return None
    if not _REPO_NAME_RE.fullmatch(repo):
        return None
    return f"{_BASE}{owner}/{repo}"


def parse_github_user_iri(iri: str | None) -> str | None:
    """Inverse of `github_user_iri` — return the bare handle, or None."""
    canonical = github_user_iri(iri)
    if canonical is None:
        return None
    return canonical[len(_BASE):]


def parse_github_org_iri(iri: str | None) -> str | None:
    """Alias of `parse_github_user_iri` — same URL shape."""
    return parse_github_user_iri(iri)


def parse_github_repo_iri(iri: str | None) -> tuple[str, str] | None:
    """Inverse — return ``(owner, repo)`` tuple, or None."""
    canonical = github_repo_iri(iri)
    if canonical is None:
        return None
    rest = canonical[len(_BASE):]
    parts = rest.split("/", 1)
    if len(parts) != 2:
        return None
    return parts[0], parts[1]


__all__ = [
    "github_org_iri",
    "github_repo_iri",
    "github_user_iri",
    "parse_github_org_iri",
    "parse_github_repo_iri",
    "parse_github_user_iri",
]
