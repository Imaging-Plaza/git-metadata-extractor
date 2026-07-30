"""Validate `@handle`-style organization names against the GitHub API.

Some agents emit Organization entities whose `schema:name` is a bare
GitHub mention (e.g. `@10xGenomics`, `@stripe`) extracted from README or
prompt text but never resolved to a real handle. These entities have no
identifiers populated and fail strict validation (which requires at
least one of `schema:identifier`, `pulse:githubOrganizationHandle`, or
`pulse:infoscienceOrganizationIdentifier`).

This stage:
1. Iterates `org:Organization` entities whose `pulse:githubOrganizationHandle`
   is unset and whose `schema:name` matches `^@[A-Za-z0-9](-?[A-Za-z0-9]){0,38}$`.
2. Probes `providers.github.get_user(handle)` (cached).
3. If the handle resolves to a real GitHub `Organization` →
   stamps `pulse:githubOrganizationHandle`, the matching identifiers
   entry, and rewrites `schema:name` to drop the leading `@`.
4. If the handle is a `User` or 404 → drops the entity (it's a
   hallucinated org from a markdown @-mention). The drop is recorded
   as a warning and the entity is removed from the reconciled bucket.

Runs between `guarantee_repo_author` and `strict_validation`.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from git_metadata_extractor.agents.models import ProviderSet
from git_metadata_extractor.canonicalization.github import github_org_iri
from git_metadata_extractor.pipeline.stages.models import ReconciledEntities

logger = logging.getLogger(__name__)

ORG_TYPE = "org:Organization"
HANDLE_KEY = "pulse:githubOrganizationHandle"
ORG_BUCKET = "organizations"

# GitHub login pattern (mirrors the orchestrator's GITHUB_LOGIN_PATTERN).
_AT_HANDLE_PATTERN = re.compile(r"^@([A-Za-z\d](?:[A-Za-z\d]|-(?=[A-Za-z\d])){0,38})$")


def _extract_at_handle(name: Any) -> str | None:
    if not isinstance(name, str):
        return None
    match = _AT_HANDLE_PATTERN.match(name.strip())
    return match.group(1) if match else None


def _existing_handle(entity: dict[str, Any]) -> str | None:
    direct = entity.get(HANDLE_KEY)
    if isinstance(direct, str) and direct.strip():
        return direct.strip()
    identifiers = entity.get("identifiers")
    if isinstance(identifiers, dict):
        nested = identifiers.get(HANDLE_KEY)
        if isinstance(nested, str) and nested.strip():
            return nested.strip()
    return None


def _stamp_handle(entity: dict[str, Any], handle: str) -> None:
    # v3.0.0: pulse:githubOrganizationHandle is the canonical
    # `https://github.com/<handle>` URL — the strict schema enforces that
    # pattern, so stamping the bare handle here gets the org excluded.
    canonical = github_org_iri(handle) or handle
    entity[HANDLE_KEY] = canonical
    identifiers = entity.get("identifiers")
    if not isinstance(identifiers, dict):
        identifiers = {}
        entity["identifiers"] = identifiers
    identifiers[HANDLE_KEY] = canonical
    name = entity.get("schema:name")
    if isinstance(name, str) and name.strip().startswith("@"):
        entity["schema:name"] = name.strip().lstrip("@")


def _label(entity: dict[str, Any]) -> str:
    return (
        entity.get("schema:name")
        or entity.get("id")
        or entity.get("@id")
        or "<unknown organization>"
    )


def validate_org_github_handles(
    reconciled: ReconciledEntities,
    providers: ProviderSet,
) -> tuple[ReconciledEntities, list[str]]:
    """Stamp or drop `@handle`-named organizations using GitHub.

    Returns the same ``reconciled`` (mutated in place — the dataclass is
    not frozen) plus the list of warnings emitted by this stage.
    """
    warnings: list[str] = []
    organizations = reconciled.entities.get(ORG_BUCKET)
    if not isinstance(organizations, list) or not organizations:
        return reconciled, warnings

    github_provider = getattr(providers, "github", None)
    if github_provider is None:
        return reconciled, warnings

    kept: list[dict[str, Any]] = []
    for entity in organizations:
        if not isinstance(entity, dict):
            kept.append(entity)
            continue
        if entity.get("type") != ORG_TYPE:
            kept.append(entity)
            continue
        if _existing_handle(entity) is not None:
            kept.append(entity)
            continue
        candidate = _extract_at_handle(entity.get("schema:name"))
        if candidate is None:
            kept.append(entity)
            continue

        try:
            github_user = github_provider.get_user(candidate)
        except Exception as exc:  # noqa: BLE001 — provider failures are non-fatal
            logger.warning(
                "validate_org_github_handles: github lookup for '@%s' failed: %s",
                candidate,
                exc,
            )
            kept.append(entity)
            continue

        raw_type = (
            github_user.get("type") or github_user.get("account_type")
            if isinstance(github_user, dict)
            else None
        )
        normalized_type = (
            raw_type.lower() if isinstance(raw_type, str) and raw_type else None
        )
        if normalized_type == "organization":
            _stamp_handle(entity, candidate)
            warnings.append(
                f"Stamped pulse:githubOrganizationHandle='{candidate}' on "
                f"{_label(entity)} (validated against GitHub).",
            )
            kept.append(entity)
        elif normalized_type == "user" or not github_user:
            warnings.append(
                f"Dropped hallucinated organization '@{candidate}' "
                f"({_label(entity)}): GitHub login resolves to "
                f"{normalized_type or 'no account'}, not an organization.",
            )
        else:
            # Unknown shape — keep the entity rather than guess.
            kept.append(entity)

    reconciled.entities[ORG_BUCKET] = kept
    return reconciled, warnings
