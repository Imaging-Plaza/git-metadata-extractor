from __future__ import annotations

import asyncio
import logging
import re
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Any, Awaitable, Callable

from src.v2.providers.base import (
    GitHubProvider,
    ProviderNotFoundError,
)

if TYPE_CHECKING:
    from src.v2.providers.rate_limiter import RateLimiter

JSONMapping = dict[str, Any]
GimieExtractor = Callable[[str, str], Any]
UserLookup = Callable[[str], JSONMapping]
OrganizationLookup = Callable[[str], JSONMapping]
RepositoryContextLoader = Callable[[str], Awaitable[JSONMapping] | JSONMapping]
GITHUB_LOGIN_PATTERN = re.compile(r"^[A-Za-z\d](?:[A-Za-z\d]|-(?=[A-Za-z\d])){0,38}$")
GITHUB_NOREPLY_PATTERN = re.compile(
    r"^(?:\d+\+)?([A-Za-z\d-]{1,39})@users\.noreply\.github\.com$",
    flags=re.IGNORECASE,
)
logger = logging.getLogger(__name__)


def _first_non_empty_string(value: Any) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    if isinstance(value, list):
        for item in value:
            normalized = _first_non_empty_string(item)
            if normalized:
                return normalized
    if isinstance(value, dict):
        for key in ("@value", "value", "name"):
            nested = _first_non_empty_string(value.get(key))
            if nested:
                return nested
    return None


def _normalize_repo_url(full_name: str) -> str:
    candidate = full_name.strip()
    if candidate.startswith(("http://", "https://")):
        return candidate
    return f"https://github.com/{candidate}"


def _normalize_full_name(full_name: str, repository_payload: JSONMapping) -> str:
    if "/" in full_name:
        return full_name

    candidates = [
        repository_payload.get("full_name"),
        repository_payload.get("nameWithOwner"),
        repository_payload.get("schema:codeRepository"),
    ]
    for candidate in candidates:
        candidate_value = _first_non_empty_string(candidate)
        if not candidate_value:
            continue
        if "github.com/" in candidate_value:
            return candidate_value.split("github.com/")[-1].strip("/")
        if "/" in candidate_value:
            return candidate_value
    return full_name


def _normalize_github_login(candidate: Any) -> str | None:
    if not isinstance(candidate, str):
        return None
    normalized = candidate.strip().lstrip("@")
    if not normalized:
        return None
    return normalized if GITHUB_LOGIN_PATTERN.fullmatch(normalized) else None


def _extract_login_from_email(email: Any) -> str | None:
    if not isinstance(email, str):
        return None
    match = GITHUB_NOREPLY_PATTERN.fullmatch(email.strip())
    if not match:
        return None
    return _normalize_github_login(match.group(1))


def _resolve_contributor_login(author: Any) -> str | None:
    author_id = getattr(author, "id", None)
    login_from_id = _normalize_github_login(author_id)
    if login_from_id:
        return login_from_id

    login_from_email = _extract_login_from_email(getattr(author, "email", None))
    if login_from_email:
        return login_from_email

    return _normalize_github_login(getattr(author, "name", None))


def _extract_repository_node(gimie_payload: Any) -> JSONMapping:
    if isinstance(gimie_payload, dict):
        graph = gimie_payload.get("@graph")
        if isinstance(graph, list):
            for node in graph:
                if not isinstance(node, dict):
                    continue
                node_type = node.get("@type")
                if node_type == "schema:SoftwareSourceCode":
                    return node
                if isinstance(node_type, list) and any(
                    "SoftwareSourceCode" in str(item) for item in node_type
                ):
                    return node
        return gimie_payload
    return {}


def _extract_languages_from_node(node: JSONMapping) -> dict[str, int]:
    language_values = node.get("schema:programmingLanguage") or node.get(
        "programmingLanguage",
    )
    languages: dict[str, int] = {}

    if isinstance(language_values, str):
        languages[language_values] = 1
        return languages

    if isinstance(language_values, list):
        for item in language_values:
            name = _first_non_empty_string(item)
            if name:
                languages[name] = languages.get(name, 0) + 1
    return languages


def _run_async(sync_or_async: Awaitable[Any] | Any) -> Any:
    if not asyncio.iscoroutine(sync_or_async):
        return sync_or_async
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(sync_or_async)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(asyncio.run, sync_or_async)
        return future.result()


class RealGitHubProvider(GitHubProvider):
    """Production GitHub provider backed by existing GIMIE and v1 parser utilities."""

    def __init__(  # noqa: PLR0913
        self,
        *,
        force_refresh: bool = False,
        include_user_repositories: bool = True,
        include_organization_repositories: bool = True,
        gimie_extractor: GimieExtractor | None = None,
        user_lookup: UserLookup | None = None,
        organization_lookup: OrganizationLookup | None = None,
        repository_context_loader: RepositoryContextLoader | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        super().__init__(provider_name="github", rate_limiter=rate_limiter)
        self._force_refresh = force_refresh
        self._include_user_repositories = include_user_repositories
        self._include_organization_repositories = include_organization_repositories
        self._gimie_extractor = gimie_extractor
        self._user_lookup = user_lookup
        self._organization_lookup = organization_lookup
        self._repository_context_loader = repository_context_loader

        self._cached_users_parser: Any | None = None
        self._cached_orgs_parser: Any | None = None

    @property
    def force_refresh(self) -> bool:
        return self._force_refresh

    @property
    def include_user_repositories(self) -> bool:
        return self._include_user_repositories

    @property
    def include_organization_repositories(self) -> bool:
        return self._include_organization_repositories

    @staticmethod
    def _model_dump(value: Any) -> JSONMapping:
        if hasattr(value, "model_dump"):
            dumped = value.model_dump()
            if isinstance(dumped, dict):
                return dumped
        if isinstance(value, dict):
            return value
        raise TypeError

    def _resolve_gimie_extractor(self) -> GimieExtractor:
        if self._gimie_extractor is not None:
            return self._gimie_extractor
        from src.gimie_utils.gimie_methods import extract_gimie  # noqa: PLC0415

        self._gimie_extractor = extract_gimie
        return extract_gimie

    def _resolve_context_loader(self) -> RepositoryContextLoader:
        if self._repository_context_loader is not None:
            return self._repository_context_loader
        from src.context.repository import prepare_repository_context  # noqa: PLC0415

        self._repository_context_loader = prepare_repository_context
        return prepare_repository_context

    def _resolve_user_lookup(self) -> UserLookup:
        if self._user_lookup is not None:
            return self._user_lookup

        if self._cached_users_parser is None:
            from src.cache.cached_parsers import (  # noqa: PLC0415
                CachedGitHubUsersParser,
            )

            self._cached_users_parser = CachedGitHubUsersParser()
        parser = self._cached_users_parser
        assert parser is not None

        def _lookup(username: str) -> JSONMapping:
            user = parser.get_user_metadata_cached(
                username,
                force_refresh=self._force_refresh,
                include_repositories=self._include_user_repositories,
            )
            return self._model_dump(user)

        self._user_lookup = _lookup
        return _lookup

    def _resolve_organization_lookup(self) -> OrganizationLookup:
        if self._organization_lookup is not None:
            return self._organization_lookup

        if self._cached_orgs_parser is None:
            from src.cache.cached_parsers import (  # noqa: PLC0415
                CachedGitHubOrganizationsParser,
            )

            self._cached_orgs_parser = CachedGitHubOrganizationsParser()
        parser = self._cached_orgs_parser
        assert parser is not None

        def _lookup(org_name: str) -> JSONMapping:
            org = parser.get_organization_metadata_cached(
                org_name,
                force_refresh=self._force_refresh,
                include_repositories=self._include_organization_repositories,
            )
            return self._model_dump(org)

        self._organization_lookup = _lookup
        return _lookup

    def get_repository(self, full_name: str) -> dict[str, Any]:
        repository_url = _normalize_repo_url(full_name)
        gimie_payload = self._run_with_rate_limit(
            lambda: self._resolve_gimie_extractor()(repository_url, "json-ld"),
        )
        node = _extract_repository_node(gimie_payload)

        normalized_full_name = _normalize_full_name(full_name, node)
        if "/" not in normalized_full_name:
            message = (
                "Repository metadata is missing owner/repository handle: "
                f"{full_name}"
            )
            raise ProviderNotFoundError(
                message,
            )
        owner_name = normalized_full_name.split("/", maxsplit=1)[0]

        return {
            "name": _first_non_empty_string(node.get("schema:name"))
            or normalized_full_name.split("/", maxsplit=1)[-1],
            "full_name": normalized_full_name,
            "html_url": repository_url,
            "owner": {
                "login": owner_name,
                "type": "Organization",
            },
            "description": _first_non_empty_string(node.get("schema:description")),
            "stargazers_count": node.get("pulse:githubRepoStars"),
            "forks_count": node.get("pulse:githubRepoForks"),
            "created_at": _first_non_empty_string(node.get("schema:dateCreated")),
            "license": {
                "spdx_id": _first_non_empty_string(node.get("schema:license")),
            },
            "fork": bool(node.get("pulse:isForkOf")),
            "source": {
                "full_name": _first_non_empty_string(node.get("pulse:isForkOf")),
            },
            "topics": [],
        }

    def get_user(self, username: str) -> dict[str, Any]:
        try:
            return self._run_with_rate_limit(
                lambda: self._resolve_user_lookup()(username),
            )
        except ValueError as exc:
            raise ProviderNotFoundError(str(exc)) from exc

    def get_organization(self, org_name: str) -> dict[str, Any]:
        try:
            return self._run_with_rate_limit(
                lambda: self._resolve_organization_lookup()(org_name),
            )
        except ValueError as exc:
            raise ProviderNotFoundError(str(exc)) from exc

    def get_contributors(self, full_name: str) -> list[dict[str, Any]]:
        context_loader = self._resolve_context_loader()
        repository_url = _normalize_repo_url(full_name)
        context_result = self._run_with_rate_limit(
            lambda: _run_async(context_loader(repository_url)),
        )

        if not isinstance(context_result, dict):
            return []

        git_authors = context_result.get("git_authors", [])
        contributors: list[dict[str, Any]] = []
        for author in git_authors:
            commits = getattr(author, "commits", None)
            total_commits = getattr(commits, "total", None)
            login = _resolve_contributor_login(author)
            if login is None:
                logger.debug(
                    "Contributor login unresolved; skipping GitHub username fanout "
                    "(author_id=%s, name=%s, email=%s)",
                    getattr(author, "id", None),
                    getattr(author, "name", None),
                    getattr(author, "email", None),
                )
            contributor = {
                "login": login,
                "name": getattr(author, "name", None),
                "email": getattr(author, "email", None),
                "contributions": total_commits,
            }
            contributors.append(contributor)

        return contributors

    def get_languages(self, full_name: str) -> dict[str, int]:
        repository_url = _normalize_repo_url(full_name)
        gimie_payload = self._run_with_rate_limit(
            lambda: self._resolve_gimie_extractor()(repository_url, "json-ld"),
        )
        node = _extract_repository_node(gimie_payload)
        return _extract_languages_from_node(node)
