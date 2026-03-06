from __future__ import annotations

from typing import TYPE_CHECKING, Any

from src.v2.pipeline.stages.models import ContextBundle

if TYPE_CHECKING:
    from src.v2.agents.models import ProviderSet
    from src.v2.detection.models import GitHubURLClassification


class RequiredProviderUnavailableError(RuntimeError):
    """Raised when a required provider call fails for the current extract mode."""

    def __init__(self, *, provider: str, operation: str, cause: Exception) -> None:
        self.provider = provider
        self.operation = operation
        self.cause = cause
        super().__init__(
            f"Required provider '{provider}' failed during {operation}: {cause}",
        )


def _first_non_empty_string(*candidates: Any) -> str | None:
    for candidate in candidates:
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


def _coerce_repositories(candidate: Any) -> list[str]:
    if not isinstance(candidate, list):
        return []
    repositories: list[str] = []
    for item in candidate:
        if isinstance(item, str) and item.strip():
            repositories.append(item.strip())
            continue
        if isinstance(item, dict):
            name = _first_non_empty_string(
                item.get("full_name"),
                item.get("name"),
                item.get("repository"),
            )
            if name:
                repositories.append(name)
    return repositories


def _coerce_members(candidate: Any) -> list[str]:
    if not isinstance(candidate, list):
        return []
    members: list[str] = []
    for item in candidate:
        if isinstance(item, str) and item.strip():
            members.append(item.strip())
            continue
        if isinstance(item, dict):
            login = _first_non_empty_string(item.get("login"), item.get("username"))
            if login:
                members.append(login)
    return members


def _coerce_repository_files(candidate: Any) -> list[dict[str, str]]:
    if isinstance(candidate, dict):
        rows: list[dict[str, str]] = []
        for path, content in candidate.items():
            if isinstance(path, str) and path.strip() and isinstance(content, str) and content.strip():
                rows.append({"path": path.strip(), "content": content.strip()})
        return rows

    if not isinstance(candidate, list):
        return []

    rows = []
    for item in candidate:
        if not isinstance(item, dict):
            continue
        path = _first_non_empty_string(item.get("path"), item.get("file_path"), item.get("name"))
        content = _first_non_empty_string(item.get("content"), item.get("text"), item.get("body"))
        if path and content:
            rows.append({"path": path, "content": content})
    return rows


def _normalize_owned_repo_full_name(owner: str, repo: str) -> str:
    if "/" in repo:
        return repo
    return f"{owner}/{repo}"


def _optional_repository_context(
    *,
    full_name: str,
    providers: ProviderSet,
    warnings: list[str],
) -> dict[str, Any] | None:
    try:
        repository_metadata = providers.github.get_repository(full_name)
    except Exception as exc:  # noqa: BLE001
        warnings.append(
            f"Repository metadata lookup failed for {full_name}: {exc}",
        )
        return None

    try:
        contributors = providers.github.get_contributors(full_name)
    except Exception as exc:  # noqa: BLE001
        warnings.append(
            f"Repository contributors lookup failed for {full_name}: {exc}",
        )
        contributors = []

    try:
        languages = providers.github.get_languages(full_name)
    except Exception as exc:  # noqa: BLE001
        warnings.append(
            f"Repository languages lookup failed for {full_name}: {exc}",
        )
        languages = {}

    readme_content = _first_non_empty_string(
        repository_metadata.get("readme"),
        repository_metadata.get("readme_content"),
        repository_metadata.get("README"),
        repository_metadata.get("description"),
    )
    if not readme_content:
        warnings.append(f"Repository README content is not available for {full_name}")
        readme_content = ""

    gimie_jsonld: dict[str, Any] = {}
    try:
        fetched_jsonld = providers.github.get_repository_jsonld(full_name)
    except Exception as exc:  # noqa: BLE001
        warnings.append(
            f"Repository GIMIE JSON-LD lookup failed for {full_name}: {exc}",
        )
    else:
        if isinstance(fetched_jsonld, dict):
            gimie_jsonld = fetched_jsonld

    return {
        "full_name": full_name,
        "metadata": repository_metadata,
        "readme_content": readme_content,
        "contributors": contributors,
        "languages": languages,
        "gimie_jsonld": gimie_jsonld,
        "repository_files": _coerce_repository_files(
            repository_metadata.get("repository_files") or repository_metadata.get("files"),
        ),
    }


def _normalize_detected_type(detected_type: str | Any) -> str:
    if hasattr(detected_type, "value"):
        return str(detected_type.value)
    return str(detected_type)


async def gather_context(  # noqa: C901, PLR0915
    detected_type: str,
    url_info: GitHubURLClassification,
    providers: ProviderSet,
) -> ContextBundle:
    """Collect upstream provider context before agent execution."""
    normalized_type = _normalize_detected_type(detected_type)
    warnings: list[str] = []
    context: dict[str, Any] = {}

    if normalized_type == "repository":
        full_name = f"{url_info.owner}/{url_info.repo or ''}".rstrip("/")

        repository_metadata: dict[str, Any] = {}
        contributors: list[dict[str, Any]] = []
        languages: dict[str, int] = {}

        try:
            repository_metadata = providers.github.get_repository(full_name)
        except Exception as exc:  # noqa: BLE001
            raise RequiredProviderUnavailableError(
                provider="github",
                operation="repository metadata lookup",
                cause=exc,
            ) from exc

        try:
            contributors = providers.github.get_contributors(full_name)
        except Exception as exc:  # noqa: BLE001
            raise RequiredProviderUnavailableError(
                provider="github",
                operation="repository contributors lookup",
                cause=exc,
            ) from exc

        try:
            languages = providers.github.get_languages(full_name)
        except Exception as exc:  # noqa: BLE001
            raise RequiredProviderUnavailableError(
                provider="github",
                operation="repository languages lookup",
                cause=exc,
            ) from exc

        readme_content = _first_non_empty_string(
            repository_metadata.get("readme"),
            repository_metadata.get("readme_content"),
            repository_metadata.get("README"),
            repository_metadata.get("description"),
        )
        if not readme_content:
            warnings.append("Repository README content is not available")
            readme_content = ""

        gimie_jsonld = providers.github.get_repository_jsonld(full_name)

        context["repository"] = {
            "full_name": full_name,
            "metadata": repository_metadata,
            "readme_content": readme_content,
            "contributors": contributors,
            "languages": languages,
            "gimie_jsonld": gimie_jsonld,
            "repository_files": _coerce_repository_files(
                repository_metadata.get("repository_files") or repository_metadata.get("files"),
            ),
        }
        return ContextBundle(
            detected_type=normalized_type,
            context=context,
            warnings=warnings,
        )

    if normalized_type == "user":
        username = url_info.owner
        user_profile: dict[str, Any] = {}

        try:
            user_profile = providers.github.get_user(username)
        except Exception as exc:  # noqa: BLE001
            raise RequiredProviderUnavailableError(
                provider="github",
                operation="user profile lookup",
                cause=exc,
            ) from exc

        owned_repos = _coerce_repositories(
            user_profile.get("repositories")
            or user_profile.get("repos")
            or user_profile.get("owned_repos"),
        )
        repository_contexts: dict[str, dict[str, Any]] = {}
        for repo in owned_repos:
            full_name = _normalize_owned_repo_full_name(username, repo)
            repository_context = _optional_repository_context(
                full_name=full_name,
                providers=providers,
                warnings=warnings,
            )
            if isinstance(repository_context, dict):
                repository_contexts[full_name] = repository_context
        orcid_id = _first_non_empty_string(
            user_profile.get("orcid"),
            user_profile.get("orcid_id"),
            user_profile.get("orcidIdentifier"),
        )

        orcid_data: dict[str, Any] | None = None
        if providers.orcid and orcid_id:
            try:
                orcid_data = providers.orcid.get_person_by_orcid(orcid_id)
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"ORCID lookup failed: {exc}")
        else:
            warnings.append("ORCID data unavailable for this user")

        context["user"] = {
            "username": username,
            "profile": user_profile,
            "owned_repos": owned_repos,
            "repository_contexts": repository_contexts,
            "orcid_data": orcid_data,
        }
        return ContextBundle(
            detected_type=normalized_type,
            context=context,
            warnings=warnings,
        )

    if normalized_type == "organization":
        organization_name = url_info.owner
        organization_profile: dict[str, Any] = {}

        try:
            organization_profile = providers.github.get_organization(organization_name)
        except Exception as exc:  # noqa: BLE001
            raise RequiredProviderUnavailableError(
                provider="github",
                operation="organization profile lookup",
                cause=exc,
            ) from exc

        member_list = _coerce_members(organization_profile.get("members"))
        owned_repos = _coerce_repositories(
            organization_profile.get("repositories")
            or organization_profile.get("repos"),
        )
        repository_contexts: dict[str, dict[str, Any]] = {}
        for repo in owned_repos:
            full_name = _normalize_owned_repo_full_name(organization_name, repo)
            repository_context = _optional_repository_context(
                full_name=full_name,
                providers=providers,
                warnings=warnings,
            )
            if isinstance(repository_context, dict):
                repository_contexts[full_name] = repository_context

        context["organization"] = {
            "org_name": organization_name,
            "profile": organization_profile,
            "members": member_list,
            "owned_repos": owned_repos,
            "repository_contexts": repository_contexts,
        }
        return ContextBundle(
            detected_type=normalized_type,
            context=context,
            warnings=warnings,
        )

    raise ValueError
