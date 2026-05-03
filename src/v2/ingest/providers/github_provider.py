from __future__ import annotations

import itertools
import logging
import os
import re
import threading
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import urlparse

import requests

from src.v2.ingest.cache import ProviderCache
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


# Per-process round-robin over comma-separated tokens in GITHUB_TOKEN. With
# multiple gunicorn workers the rotation is independent per worker, which is
# fine — overall calls split roughly evenly across tokens and the effective
# rate-limit ceiling is N * 5000/h for N tokens.
_GITHUB_TOKEN_LOCK = threading.Lock()
_GITHUB_TOKEN_CYCLE: itertools.cycle | None = None
_GITHUB_TOKEN_SOURCE: str | None = None


def _parse_github_tokens(raw: str) -> list[str]:
    return [t.strip() for t in raw.split(",") if t.strip()]


def _next_github_token() -> str:
    """Return the next GitHub token (round-robin), or '' if none configured.

    Prefers `GITHUB_TOKEN_POOL` (comma-separated, set by the api startup
    normalization) over `GITHUB_TOKEN`. Falls back to `GITHUB_TOKEN` when no
    pool is configured.
    """
    global _GITHUB_TOKEN_CYCLE, _GITHUB_TOKEN_SOURCE
    raw = os.environ.get("GITHUB_TOKEN_POOL", "") or os.environ.get("GITHUB_TOKEN", "")
    with _GITHUB_TOKEN_LOCK:
        if raw != _GITHUB_TOKEN_SOURCE or _GITHUB_TOKEN_CYCLE is None:
            tokens = _parse_github_tokens(raw)
            _GITHUB_TOKEN_CYCLE = itertools.cycle(tokens) if tokens else None
            _GITHUB_TOKEN_SOURCE = raw
        if _GITHUB_TOKEN_CYCLE is None:
            return ""
        return next(_GITHUB_TOKEN_CYCLE)


def _github_auth_headers(extra: dict[str, str] | None = None) -> dict[str, str]:
    """Build GitHub request headers with a rotated bearer token."""
    headers: dict[str, str] = {"Accept": "application/vnd.github+json"}
    if extra:
        headers.update(extra)
    token = _next_github_token()
    if token:
        headers["Authorization"] = f"token {token}"
    return headers


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


def _parse_purl(purl: str) -> dict[str, str | None] | None:
    """Parse a Package URL (purl) into ecosystem/name/version.

    Spec: https://github.com/package-url/purl-spec. We only need a thin
    extractor — the SPDX SBOM uses purl as the canonical reference and we
    care about the three fields the LLM will reason over. Returns ``None``
    when the input is not a valid purl.

    Handles npm-style scoped names where the namespace contains an ``@``
    (e.g. ``pkg:npm/@scope/name@1.0.0``) by splitting on the LAST ``@``.
    """
    if not isinstance(purl, str) or not purl.startswith("pkg:"):
        return None
    body = purl[4:].split("?", 1)[0].split("#", 1)[0]
    if "/" not in body:
        return None
    ecosystem, rest = body.split("/", 1)
    ecosystem = ecosystem.strip().lower() or None
    if not ecosystem or not rest:
        return None
    if "@" in rest:
        name, version = rest.rsplit("@", 1)
        version = version.strip() or None
    else:
        name, version = rest, None
    name = name.strip() or None
    if not name:
        return None
    return {"ecosystem": ecosystem, "name": name, "version": version}


def _normalize_sbom(spdx_payload: Any) -> list[dict[str, Any]]:
    """Reduce a GitHub SPDX SBOM payload to a flat dependency list.

    GitHub returns ``{"sbom": {"packages": [...]}}``. Each package's
    ``externalRefs`` may include a ``purl`` reference; that's the canonical
    source of ecosystem/name/version. The first package in the SBOM is the
    repository itself — skipped via the missing-purl check (the root has no
    purl) rather than positional indexing, which is fragile.
    """
    if not isinstance(spdx_payload, dict):
        return []
    sbom = spdx_payload.get("sbom") if "sbom" in spdx_payload else spdx_payload
    if not isinstance(sbom, dict):
        return []
    packages = sbom.get("packages")
    if not isinstance(packages, list):
        return []

    dependencies: list[dict[str, Any]] = []
    for package in packages:
        if not isinstance(package, dict):
            continue
        external_refs = package.get("externalRefs")
        if not isinstance(external_refs, list):
            continue
        purl_value: str | None = None
        for ref in external_refs:
            if not isinstance(ref, dict):
                continue
            if str(ref.get("referenceType", "")).lower() != "purl":
                continue
            locator = ref.get("referenceLocator")
            if isinstance(locator, str) and locator.strip():
                purl_value = locator.strip()
                break
        if purl_value is None:
            continue
        parsed = _parse_purl(purl_value)
        if parsed is None:
            continue
        spdx_id = package.get("SPDXID")
        dependencies.append(
            {
                "name": parsed["name"],
                "ecosystem": parsed["ecosystem"],
                "version": parsed["version"]
                or _first_non_empty_string(package.get("versionInfo")),
                "spdxId": spdx_id if isinstance(spdx_id, str) else None,
            },
        )
    return dependencies


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
        cache: ProviderCache | None = None,
    ) -> None:
        super().__init__(provider_name="github", rate_limiter=rate_limiter)
        self._include_user_repositories = include_user_repositories
        self._include_organization_repositories = include_organization_repositories
        self._gimie_extractor = gimie_extractor
        self._user_lookup = user_lookup
        self._organization_lookup = organization_lookup
        self._cache = cache

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
        from src.v1.gimie_utils.gimie_methods import extract_gimie  # noqa: PLC0415

        self._gimie_extractor = extract_gimie
        return extract_gimie

    def _get_or_fetch_gimie_payload(self, repository_url: str) -> Any:
        """Return the GIMIE JSON-LD payload, fetching once across cache layers.

        Order: in-memory dict → persistent `ProviderCache` → live GIMIE call.
        Both layers are populated on a fresh fetch.
        """
        payload = self._gimie_payload_cache.get(repository_url)
        if payload is not None:
            return payload

        cache_key: str | None = None
        if self._cache is not None:
            cache_key = ProviderCache.make_key(
                "github",
                "gimie_payload",
                repository_url=repository_url,
            )
            cached = self._cache.get(cache_key)
            if cached is not None:
                logger.info(
                    "provider cache hit: github.gimie_payload(%s)",
                    repository_url,
                )
                self._gimie_payload_cache[repository_url] = cached
                return cached

        payload = self._run_with_rate_limit(
            lambda: self._resolve_gimie_extractor()(repository_url, "json-ld"),
        )
        if payload is not None:
            self._gimie_payload_cache[repository_url] = payload
            if self._cache is not None and cache_key is not None:
                self._cache.set(cache_key, payload)
        return payload

    def _get_repository_node(self, full_name: str) -> JSONMapping:
        repository_url = _normalize_repo_url(full_name)
        gimie_payload = self._get_or_fetch_gimie_payload(repository_url)
        return _extract_repository_node(gimie_payload)

    def get_repository_jsonld(self, full_name: str) -> dict[str, Any]:
        """Return the raw GIMIE JSON-LD payload, or an empty dict."""
        repository_url = _normalize_repo_url(full_name)
        payload = self._gimie_payload_cache.get(repository_url)
        if payload is None:
            payload = self._get_or_fetch_gimie_payload(repository_url)
        return payload if isinstance(payload, dict) else {}

    def _resolve_user_lookup(self) -> UserLookup:
        if self._user_lookup is not None:
            return self._user_lookup

        if self._users_parser is None:
            from src.v1.parsers.users_parser import GitHubUsersParser  # noqa: PLC0415

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
            from src.v1.parsers.orgs_parser import (  # noqa: PLC0415
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

        # Stars/forks/created_at are not reliably present in gimie's JSON-LD
        # output. Fetch them deterministically from the GitHub REST API
        # (cached) and merge in.
        rest_metadata = self._get_repository_rest_metadata(normalized_full_name)
        stargazers_count = rest_metadata.get("stargazers_count")
        if stargazers_count is None:
            stargazers_count = node.get("pulse:githubRepoStars")
        forks_count = rest_metadata.get("forks_count")
        if forks_count is None:
            forks_count = node.get("pulse:githubRepoForks")
        created_at = rest_metadata.get("created_at") or _first_non_empty_string(
            node.get("schema:dateCreated"),
            node.get("http://schema.org/dateCreated"),
        )

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
            "stargazers_count": stargazers_count,
            "forks_count": forks_count,
            "created_at": created_at,
            "license": {
                "spdx_id": _extract_spdx_id(node),
            },
            "fork": bool(node.get("pulse:isForkOf")),
            "source": {
                "full_name": _first_non_empty_string(node.get("pulse:isForkOf")),
            },
            "topics": [],
        }

    def _get_repository_rest_metadata(self, full_name: str) -> dict[str, Any]:
        """Fetch stars/forks/created_at from the GitHub REST API (cached).

        Returns an empty dict on any error so the caller falls back to
        gimie-derived values. Cached via the standard provider cache so
        repeated requests within TTL don't re-hit GitHub.
        """

        def _fetch() -> dict[str, Any]:
            url = f"https://api.github.com/repos/{full_name}"
            try:
                response = self._run_with_rate_limit(
                    lambda: requests.get(url, headers=_github_auth_headers(), timeout=15),
                )
            except Exception:  # noqa: BLE001
                logger.exception("github REST repo fetch failed: %s", full_name)
                return {}
            if response.status_code != 200:
                logger.warning(
                    "github REST repo fetch returned %d for %s",
                    response.status_code,
                    full_name,
                )
                return {}
            try:
                payload = response.json()
            except ValueError:
                logger.exception("github REST repo response not JSON: %s", full_name)
                return {}
            if not isinstance(payload, dict):
                return {}
            return {
                "stargazers_count": payload.get("stargazers_count"),
                "forks_count": payload.get("forks_count"),
                "created_at": payload.get("created_at"),
            }

        if self._cache is None:
            return _fetch()
        key = ProviderCache.make_key("github", "get_repository_rest", full_name=full_name)
        return self._cache.get_or_set(
            key,
            _fetch,
            label=f"github.get_repository_rest({full_name})",
        )

    def get_repository_sbom(self, full_name: str) -> list[dict[str, Any]] | None:
        """Fetch the SPDX SBOM from GitHub and return a flat dependency list.

        Endpoint: ``GET /repos/{owner}/{repo}/dependency-graph/sbom``.
        Returns ``None`` when the repo has no SBOM (404), when dependency
        graph access is denied (403 — disabled or private without ``repo``
        scope), or when the response shape is unexpected. Other failures
        (timeouts, network errors) also collapse to ``None`` — the LLM tool
        treats absence as "no data" rather than a hard error.
        """

        def _fetch() -> list[dict[str, Any]] | None:
            url = f"https://api.github.com/repos/{full_name}/dependency-graph/sbom"
            try:
                response = self._run_with_rate_limit(
                    lambda: requests.get(url, headers=_github_auth_headers(), timeout=30),
                )
            except Exception:  # noqa: BLE001
                logger.exception("github SBOM fetch failed: %s", full_name)
                return None
            if response.status_code in (403, 404):
                logger.info(
                    "github SBOM not available (%d) for %s",
                    response.status_code,
                    full_name,
                )
                return None
            if response.status_code != 200:
                logger.warning(
                    "github SBOM fetch returned %d for %s",
                    response.status_code,
                    full_name,
                )
                return None
            try:
                payload = response.json()
            except ValueError:
                logger.exception("github SBOM response not JSON: %s", full_name)
                return None
            return _normalize_sbom(payload)

        if self._cache is None:
            return _fetch()
        key = ProviderCache.make_key("github", "get_repository_sbom", full_name=full_name)
        return self._cache.get_or_set(
            key,
            _fetch,
            label=f"github.get_repository_sbom({full_name})",
        )

    def get_user(self, username: str) -> dict[str, Any]:
        def _fetch() -> dict[str, Any]:
            try:
                return self._run_with_rate_limit(
                    lambda: self._resolve_user_lookup()(username),
                )
            except ValueError as exc:
                raise ProviderNotFoundError(str(exc)) from exc

        if self._cache is None:
            return _fetch()
        key = ProviderCache.make_key(
            "github",
            "get_user",
            username=username,
            include_repositories=self._include_user_repositories,
        )
        return self._cache.get_or_set(
            key,
            _fetch,
            label=f"github.get_user({username})",
        )

    def get_organization(self, org_name: str) -> dict[str, Any]:
        def _fetch() -> dict[str, Any]:
            try:
                return self._run_with_rate_limit(
                    lambda: self._resolve_organization_lookup()(org_name),
                )
            except ValueError as exc:
                raise ProviderNotFoundError(str(exc)) from exc

        if self._cache is None:
            return _fetch()
        key = ProviderCache.make_key(
            "github",
            "get_organization",
            org_name=org_name,
            include_repositories=self._include_organization_repositories,
        )
        return self._cache.get_or_set(
            key,
            _fetch,
            label=f"github.get_organization({org_name})",
        )

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
