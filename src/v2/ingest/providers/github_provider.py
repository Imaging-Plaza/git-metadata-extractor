from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import urlparse

from src.v2.ingest.providers.base import (
    GitHubProvider,
    ProviderNotFoundError,
)

if TYPE_CHECKING:
    from src.v2.ingest.providers.rate_limiter import RateLimiter

JSONMapping = dict[str, Any]
GimieExtractor = Callable[[str, str], Any]
UserLookup = Callable[[str], JSONMapping]
OrganizationLookup = Callable[[str], JSONMapping]
GITHUB_LOGIN_PATTERN = re.compile(r"^[A-Za-z\d](?:[A-Za-z\d]|-(?=[A-Za-z\d])){0,38}$")
GITHUB_NOREPLY_PATTERN = re.compile(
    r"^(?:\d+\+)?([A-Za-z\d-]{1,39})@users\.noreply\.github\.com$",
    flags=re.IGNORECASE,
)
logger = logging.getLogger(__name__)


def _first_non_empty_string(*values: Any) -> str | None:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
        if isinstance(value, list):
            for item in value:
                normalized = _first_non_empty_string(item)
                if normalized:
                    return normalized
        if isinstance(value, dict):
            for key in ("@value", "value", "name", "@id", "id", "url"):
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


def _deduplicate_preserve_order(values: list[str]) -> list[str]:
    deduplicated: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value in seen:
            continue
        deduplicated.append(value)
        seen.add(value)
    return deduplicated


def _extract_github_login_from_identifier(identifier: Any) -> str | None:
    candidate = identifier.strip() if isinstance(identifier, str) else ""
    login: str | None = None

    if not candidate:
        return None

    parsed = urlparse(candidate)
    if parsed.scheme and parsed.netloc:
        host = parsed.netloc.lower()
        if "github.com" in host:
            path_segments = [segment for segment in parsed.path.split("/") if segment]
            if path_segments:
                login = _normalize_github_login(path_segments[0])
    elif "github.com/" in candidate:
        suffix = candidate.split("github.com/", maxsplit=1)[-1].strip("/")
        if suffix:
            login = _normalize_github_login(suffix.split("/", maxsplit=1)[0])
    else:
        login = _normalize_github_login(candidate)

    return login


def _extract_login_from_reference(reference: Any) -> str | None:
    if isinstance(reference, str):
        return _extract_github_login_from_identifier(reference)
    if isinstance(reference, dict):
        identifier = None
        for key in ("@id", "id", "url", "@value", "value", "name"):
            identifier = _first_non_empty_string(reference.get(key))
            if identifier:
                break
        return _extract_github_login_from_identifier(identifier)
    return None


def _extract_graph_nodes(gimie_payload: Any) -> list[Any]:
    """Return all nodes from a GIMIE JSON-LD payload (@graph list or flat list)."""
    if isinstance(gimie_payload, list):
        return gimie_payload
    if isinstance(gimie_payload, dict):
        graph = gimie_payload.get("@graph")
        if isinstance(graph, list):
            return graph
    return []


def _classify_graph_nodes(
    gimie_payload: Any,
) -> tuple[dict[str, JSONMapping], set[str]]:
    """Classify GIMIE graph nodes by entity type.

    Returns:
        person_index: GitHub URL → Person node for all schema:Person nodes.
        org_logins: GitHub logins for all schema:Organization nodes.
    """
    person_index: dict[str, JSONMapping] = {}
    org_logins: set[str] = set()

    for node in _extract_graph_nodes(gimie_payload):
        if not isinstance(node, dict):
            continue
        node_id = node.get("@id")
        if not isinstance(node_id, str) or "github.com/" not in node_id:
            continue
        node_type = node.get("@type", [])
        types = node_type if isinstance(node_type, list) else [node_type]
        type_strs = [str(t) for t in types]
        if any("Person" in t for t in type_strs):
            person_index[node_id] = node
        elif any("Organization" in t for t in type_strs):
            login = node_id.split("github.com/")[-1].strip("/")
            if login:
                org_logins.add(login.lower())

    return person_index, org_logins


def _extract_name_from_person_node(node: JSONMapping) -> str | None:
    """Extract schema:name from a GIMIE Person node (handles @value wrapper)."""
    return _first_non_empty_string(
        node.get("schema:name"),
        node.get("http://schema.org/name"),
    )


def _extract_contributor_logins_from_node(node: JSONMapping) -> list[str]:
    references: list[Any] = []
    for key in (
        "schema:contributor",
        "http://schema.org/contributor",
        "schema:author",
        "http://schema.org/author",
    ):
        value = node.get(key)
        if isinstance(value, list):
            references.extend(value)
        elif value is not None:
            references.append(value)

    logins: list[str] = []
    for reference in references:
        login = _extract_login_from_reference(reference)
        if login:
            logins.append(login)
    return _deduplicate_preserve_order(logins)


def _extract_spdx_id(node: JSONMapping) -> str | None:
    license_candidate = _first_non_empty_string(
        node.get("schema:license"),
        node.get("http://schema.org/license"),
    )
    if not isinstance(license_candidate, str) or not license_candidate:
        return None

    if "spdx.org/licenses/" not in license_candidate:
        return license_candidate

    spdx_suffix = license_candidate.split("spdx.org/licenses/", maxsplit=1)[-1].strip("/")
    if spdx_suffix.endswith(".html"):
        spdx_suffix = spdx_suffix[:-5]
    return spdx_suffix or None


def _is_software_source_code_node(node: Any) -> bool:
    if not isinstance(node, dict):
        return False

    node_type = node.get("@type")
    if node_type == "schema:SoftwareSourceCode":
        return True
    if isinstance(node_type, str):
        return "SoftwareSourceCode" in node_type
    if isinstance(node_type, list):
        return any("SoftwareSourceCode" in str(item) for item in node_type)
    return False


def _find_software_source_code_node(nodes: list[Any]) -> JSONMapping | None:
    for node in nodes:
        if _is_software_source_code_node(node):
            return node
    return None


def _extract_repository_node(gimie_payload: Any) -> JSONMapping:
    if isinstance(gimie_payload, list):
        node = _find_software_source_code_node(gimie_payload)
        if isinstance(node, dict):
            return node
        return {}

    if isinstance(gimie_payload, dict):
        graph = gimie_payload.get("@graph")
        if isinstance(graph, list):
            node = _find_software_source_code_node(graph)
            if isinstance(node, dict):
                return node
        return gimie_payload
    return {}


def _extract_languages_from_node(node: JSONMapping) -> dict[str, int]:
    language_values = (
        node.get("schema:programmingLanguage")
        or node.get("http://schema.org/programmingLanguage")
        or node.get("programmingLanguage")
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


class RealGitHubProvider(GitHubProvider):
    """Production GitHub provider backed by existing GIMIE and v1 parser utilities."""

    def __init__(
        self,
        *,
        include_user_repositories: bool = True,
        include_organization_repositories: bool = True,
        gimie_extractor: GimieExtractor | None = None,
        user_lookup: UserLookup | None = None,
        organization_lookup: OrganizationLookup | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        super().__init__(provider_name="github", rate_limiter=rate_limiter)
        self._include_user_repositories = include_user_repositories
        self._include_organization_repositories = include_organization_repositories
        self._gimie_extractor = gimie_extractor
        self._user_lookup = user_lookup
        self._organization_lookup = organization_lookup

        self._users_parser: Any | None = None
        self._orgs_parser: Any | None = None
        self._gimie_payload_cache: dict[str, Any] = {}

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

    def _get_repository_node(self, full_name: str) -> JSONMapping:
        repository_url = _normalize_repo_url(full_name)
        gimie_payload = self._gimie_payload_cache.get(repository_url)
        if gimie_payload is None:
            gimie_payload = self._run_with_rate_limit(
                lambda: self._resolve_gimie_extractor()(repository_url, "json-ld"),
            )
            self._gimie_payload_cache[repository_url] = gimie_payload
        return _extract_repository_node(gimie_payload)

    def get_repository_jsonld(self, full_name: str) -> dict[str, Any]:
        """Return the cached raw GIMIE JSON-LD payload, or an empty dict."""
        repository_url = _normalize_repo_url(full_name)
        payload = self._gimie_payload_cache.get(repository_url)
        return payload if isinstance(payload, dict) else {}

    def _resolve_user_lookup(self) -> UserLookup:
        if self._user_lookup is not None:
            return self._user_lookup

        if self._users_parser is None:
            from src.parsers.users_parser import GitHubUsersParser  # noqa: PLC0415

            self._users_parser = GitHubUsersParser()
        parser = self._users_parser

        def _lookup(username: str) -> JSONMapping:
            user = parser.get_user_metadata(
                username,
                include_repositories=self._include_user_repositories,
            )
            return self._model_dump(user)

        self._user_lookup = _lookup
        return _lookup

    def _resolve_organization_lookup(self) -> OrganizationLookup:
        if self._organization_lookup is not None:
            return self._organization_lookup

        if self._orgs_parser is None:
            from src.parsers.orgs_parser import (  # noqa: PLC0415
                GitHubOrganizationsParser,
            )

            self._orgs_parser = GitHubOrganizationsParser()
        parser = self._orgs_parser

        def _lookup(org_name: str) -> JSONMapping:
            org = parser.get_organization_metadata(
                org_name,
                include_repositories=self._include_organization_repositories,
            )
            return self._model_dump(org)

        self._organization_lookup = _lookup
        return _lookup

    def get_repository(self, full_name: str) -> dict[str, Any]:
        repository_url = _normalize_repo_url(full_name)
        node = self._get_repository_node(full_name)

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
        repository_name = normalized_full_name.split("/", maxsplit=1)[-1]
        node_name = _first_non_empty_string(
            node.get("schema:name"),
            node.get("http://schema.org/name"),
        )
        if node_name == normalized_full_name:
            node_name = repository_name

        return {
            "name": node_name or repository_name,
            "full_name": normalized_full_name,
            "html_url": repository_url,
            "owner": {
                "login": owner_name,
                "type": "Organization",
            },
            "description": _first_non_empty_string(
                node.get("schema:description"),
                node.get("http://schema.org/description"),
            ),
            "stargazers_count": node.get("pulse:githubRepoStars"),
            "forks_count": node.get("pulse:githubRepoForks"),
            "created_at": _first_non_empty_string(
                node.get("schema:dateCreated"),
                node.get("http://schema.org/dateCreated"),
            ),
            "license": {
                "spdx_id": _extract_spdx_id(node),
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
        repository_url = _normalize_repo_url(full_name)
        gimie_payload = self._gimie_payload_cache.get(repository_url) or {}
        node = self._get_repository_node(full_name)
        gimie_contributor_logins = _extract_contributor_logins_from_node(node)
        if not gimie_contributor_logins:
            logger.debug(
                "GIMIE contributor extraction returned no GitHub logins for %s",
                full_name,
            )
            return []
        person_index, org_logins = _classify_graph_nodes(gimie_payload)
        contributors = []
        for login in gimie_contributor_logins:
            if login.lower() in org_logins:
                continue
            person_node = person_index.get(f"https://github.com/{login}", {})
            contributors.append(
                {
                    "login": login,
                    "name": _extract_name_from_person_node(person_node),
                    "email": None,
                    "contributions": None,
                }
            )
        return contributors

    def get_languages(self, full_name: str) -> dict[str, int]:
        node = self._get_repository_node(full_name)
        return _extract_languages_from_node(node)
