from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any
from urllib.parse import urlparse

from pydantic_ai import Tool

if TYPE_CHECKING:
    from src.v2.providers.base import GitHubProvider

logger = logging.getLogger(__name__)
MAX_LIST_ITEMS = 20


def _coerce_string_list(value: Any, *, max_items: int = MAX_LIST_ITEMS) -> list[str]:
    if not isinstance(value, list):
        return []
    output: list[str] = []
    for item in value:
        if not isinstance(item, str):
            continue
        normalized = item.strip()
        if not normalized:
            continue
        output.append(normalized)
        if len(output) >= max_items:
            break
    return output


def _normalize_org_name(raw_value: str) -> str:
    candidate = raw_value.strip()
    if not candidate:
        return ""
    if candidate.startswith("@"):
        candidate = candidate[1:]

    parsed = urlparse(candidate)
    if parsed.netloc.lower() in {"github.com", "www.github.com"}:
        path_parts = [part for part in parsed.path.split("/") if part]
        if path_parts:
            candidate = path_parts[0]

    if "/" in candidate:
        candidate = candidate.split("/", maxsplit=1)[0]

    return candidate.strip()


def _shape_organization_profile(
    org_name: str,
    organization: dict[str, Any],
) -> dict[str, Any]:
    login = organization.get("login")
    if not isinstance(login, str) or not login.strip():
        login = org_name

    html_url = organization.get("html_url")
    if not isinstance(html_url, str) or not html_url.strip():
        html_url = f"https://github.com/{login}"

    return {
        "login": login,
        "name": organization.get("name"),
        "description": organization.get("description"),
        "html_url": html_url,
        "type": organization.get("type"),
        "followers": organization.get("followers"),
        "public_repos": organization.get("public_repos"),
        "location": organization.get("location"),
        "blog": organization.get("blog"),
        "email": organization.get("email"),
        "company": organization.get("company"),
        "created_at": organization.get("created_at"),
        "updated_at": organization.get("updated_at"),
        "public_members": _coerce_string_list(organization.get("public_members")),
        "repositories": _coerce_string_list(organization.get("repositories")),
        "teams": _coerce_string_list(organization.get("teams")),
    }


def make_github_organization_metadata_tool(github_provider: GitHubProvider) -> Tool:
    """Create a GitHub organization lookup tool bound to a provider instance."""

    def get_github_organization_metadata(org_name: str) -> dict[str, Any]:
        """Fetch and normalize GitHub organization profile metadata.

        Args:
            org_name: GitHub organization login, @handle, URL, or owner/repo.

        Returns:
            Dict containing the normalized lookup name and an organization
            profile payload. If lookup fails, returns `error` with details.
        """

        normalized_org_name = _normalize_org_name(org_name)
        logger.info(
            "tool call: get_github_organization_metadata — query=%r normalized=%r",
            org_name,
            normalized_org_name,
        )
        if not normalized_org_name:
            return {
                "query": org_name,
                "normalized_org_name": "",
                "organization": None,
                "error": "empty_org_name",
            }

        try:
            payload = github_provider.get_organization(normalized_org_name)
        except Exception as exc:  # noqa: BLE001
            return {
                "query": org_name,
                "normalized_org_name": normalized_org_name,
                "organization": None,
                "error": str(exc),
            }

        if not isinstance(payload, dict):
            return {
                "query": org_name,
                "normalized_org_name": normalized_org_name,
                "organization": None,
                "error": "invalid_provider_payload",
            }

        return {
            "query": org_name,
            "normalized_org_name": normalized_org_name,
            "organization": _shape_organization_profile(normalized_org_name, payload),
        }

    return Tool(
        get_github_organization_metadata,
        name="get_github_organization_metadata",
        description=(
            "Fetch GitHub organization profile metadata by login, @handle, "
            "GitHub URL, or owner/repo string. Returns normalized profile "
            "fields such as login, name, html_url, type, followers, "
            "public_repos, location, website/blog, and members/repositories hints."
        ),
    )
