from __future__ import annotations

import base64
import json
import logging
import os
import re
import time
from typing import TYPE_CHECKING, Any, Callable
from urllib.parse import quote, urlparse

import requests

from src.utils.github_token_pool import (
    github_auth_headers as _github_auth_headers,
)
from src.utils.github_token_pool import (
    next_github_token as _next_github_token,
)
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

# Docker Compose filenames (basename), e.g. docker-compose.yml,
# docker-compose.prod.yaml, compose.yml — anywhere in the tree.
_COMPOSE_NAME_RE = re.compile(
    r"^(?:docker-)?compose(?:\.[\w.-]+)?\.ya?ml$", re.IGNORECASE,
)


def _is_compose_path(path: str) -> bool:
    return bool(_COMPOSE_NAME_RE.match(path.rsplit("/", maxsplit=1)[-1]))


def _resolve_max_github_repo_retries() -> int:
    """Read V2_GITHUB_REPO_MAX_RETRIES (default 3).

    Bounds transient retries when gimie returns malformed JSON (typically
    GitHub responding with an empty body / 502 HTML during rate-limit or
    momentary outages). Returns 0 to disable retries entirely.
    """
    raw = os.getenv("V2_GITHUB_REPO_MAX_RETRIES", "3")
    try:
        return max(0, int(raw))
    except (ValueError, TypeError):
        return 3


def _is_json_decode_error(exc: BaseException) -> bool:
    """Return True if the exception (or its cause chain) is a JSON parse error.

    gimie wraps the underlying ``json.JSONDecodeError`` in its own
    exception class, so we walk ``__cause__`` / ``__context__`` and also
    fall back to a substring match against the error message.
    """
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, json.JSONDecodeError):
            return True
        message = str(current)
        if "Expecting value" in message and "line 1 column 1" in message:
            return True
        current = current.__cause__ or current.__context__
    return False


def _is_unicode_decode_error(exc: BaseException) -> bool:
    """Return True if a UnicodeDecodeError appears anywhere in the cause chain."""
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, UnicodeDecodeError):
            return True
        message = str(current).lower()
        if "codec can't decode" in message or "'utf-8' codec" in message:
            return True
        current = current.__cause__ or current.__context__
    return False


_LINK_LAST_PAGE_RE = re.compile(r"[?&]page=(\d+)")


def _parse_link_last_page(link_header: str) -> int:
    """Return the `?page=N` value from the segment of `Link:` tagged
    `rel="last"`, or 0 if absent / unparseable.
    """
    if not link_header:
        return 0
    for part in link_header.split(","):
        chunk = part.strip()
        if 'rel="last"' not in chunk:
            continue
        match = _LINK_LAST_PAGE_RE.search(chunk)
        if match:
            try:
                return int(match.group(1))
            except ValueError:
                return 0
    return 0


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
        # `extract_gimie` is the single intermediate: it calls the gimie-api
        # sidecar when GIMIE_API_URL is set, else in-process gimie. Routing lives
        # there so v1 and v2 share one path (and gimie can leave our tree).
        from src.v2.ingest.providers.gimie_extract import extract_gimie  # noqa: PLC0415

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

        # Fetch with bounded retries for transient JSON-decode errors (GitHub
        # returning empty body / 502 HTML during rate-limit) and a graceful
        # fallback to a minimal payload on UnicodeDecodeError (non-UTF8 README
        # content). Tunable via `V2_GITHUB_REPO_MAX_RETRIES` (default 3).
        max_retries = _resolve_max_github_repo_retries()
        payload = None
        last_exc: BaseException | None = None
        for attempt in range(max_retries + 1):
            try:
                payload = self._run_with_rate_limit(
                    lambda: self._resolve_gimie_extractor()(repository_url, "json-ld"),
                )
                break
            except Exception as exc:  # noqa: BLE001 — broad catch needed; we re-raise non-retryable errors below
                last_exc = exc
                if _is_unicode_decode_error(exc):
                    logger.warning(
                        "github gimie hit UnicodeDecodeError for %s; falling back to "
                        "minimal payload (REST metadata still applies downstream): %s",
                        repository_url,
                        exc,
                    )
                    payload = {"@graph": []}
                    break
                if _is_json_decode_error(exc) and attempt < max_retries:
                    backoff_seconds = (2**attempt) * 0.5
                    logger.warning(
                        "github gimie returned non-JSON for %s (attempt %d/%d); "
                        "retrying in %.1fs",
                        repository_url,
                        attempt + 1,
                        max_retries + 1,
                        backoff_seconds,
                    )
                    time.sleep(backoff_seconds)
                    continue
                raise
        if payload is None:
            # Retries exhausted on a JSONDecodeError — re-raise the last exception
            # so the caller surfaces the failure (same behaviour as before this fix).
            if last_exc is not None:
                raise last_exc
            return None
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
            from src.v2.ingest.github_accounts.users_parser import GitHubUsersParser  # noqa: PLC0415

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
            from src.v2.ingest.github_accounts.orgs_parser import (  # noqa: PLC0415
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

        # REST API is authoritative for fork-status + parent (gimie's
        # `pulse:isForkOf` is unreliable — empty on most forks observed in
        # the audit). Fall back to gimie data only when REST returned nothing.
        rest_fork = rest_metadata.get("fork")
        rest_parent_url = rest_metadata.get("parent_html_url")
        rest_parent_full = rest_metadata.get("parent_full_name")
        rest_source_url = rest_metadata.get("source_html_url")
        rest_source_full = rest_metadata.get("source_full_name")
        gimie_fork_target = _first_non_empty_string(node.get("pulse:isForkOf"))
        is_fork = bool(rest_fork) if rest_fork is not None else bool(gimie_fork_target)
        parent_payload: dict[str, Any] | None = None
        if rest_parent_url or rest_parent_full:
            parent_payload = {
                "html_url": rest_parent_url,
                "full_name": rest_parent_full,
            }
        source_payload = {
            "html_url": rest_source_url,
            "full_name": rest_source_full or gimie_fork_target,
        }

        # Merge GitHub REST extras into the response. The repo_agent
        # surfaces these via `_`-prefixed internal-only fields (homepage,
        # default_branch, language, size, archived, pushed_at, etc.).
        rest_license = rest_metadata.get("license") or {}
        rest_owner = rest_metadata.get("owner") or {}
        return {
            "name": node_name or repository_name,
            "full_name": normalized_full_name,
            "html_url": repository_url,
            "owner": {
                "login": owner_name,
                "type": rest_owner.get("type") or "Organization",
                "avatar_url": rest_owner.get("avatar_url"),
                "html_url": rest_owner.get("html_url"),
            },
            "description": _first_non_empty_string(
                node.get("schema:description"),
                node.get("http://schema.org/description"),
            ),
            "stargazers_count": stargazers_count,
            "forks_count": forks_count,
            "created_at": created_at,
            "license": {
                "spdx_id": rest_license.get("spdx_id") or _extract_spdx_id(node),
                "name": rest_license.get("name"),
                "url": rest_license.get("url"),
            },
            "fork": is_fork,
            "parent": parent_payload,
            "source": source_payload,
            "topics": rest_metadata.get("topics") or [],
            # Extra REST fields preserved for the repo_agent's `_`-prefixed
            # internal-only output (gated by `?include_internal_fields=true`).
            "homepage": rest_metadata.get("homepage"),
            "default_branch": rest_metadata.get("default_branch"),
            "language": rest_metadata.get("language"),
            "size": rest_metadata.get("size"),
            "archived": rest_metadata.get("archived"),
            "disabled": rest_metadata.get("disabled"),
            "pushed_at": rest_metadata.get("pushed_at"),
            "updated_at": rest_metadata.get("updated_at"),
            "open_issues_count": rest_metadata.get("open_issues_count"),
            "watchers_count": rest_metadata.get("watchers_count"),
            "subscribers_count": rest_metadata.get("subscribers_count"),
            "network_count": rest_metadata.get("network_count"),
            "has_wiki": rest_metadata.get("has_wiki"),
            "has_pages": rest_metadata.get("has_pages"),
            "has_discussions": rest_metadata.get("has_discussions"),
            "has_issues": rest_metadata.get("has_issues"),
            "has_projects": rest_metadata.get("has_projects"),
            "visibility": rest_metadata.get("visibility"),
        }

    def _get_repository_rest_metadata(self, full_name: str) -> dict[str, Any]:
        """Fetch stars/forks/created_at + fork-status from the GitHub REST
        API (cached).

        Returns an empty dict on any error so the caller falls back to
        gimie-derived values. Cached via the standard provider cache so
        repeated requests within TTL don't re-hit GitHub.

        Also extracts `fork` (bool) and `parent.html_url` / `source.html_url`
        which gimie's JSON-LD does not surface reliably. Without this the
        downstream `pulse:isForkOf` predicate is always null (issue
        observed across cmdoret/renku, gabyx/detect-libc,
        SwissDataScienceCenter/python-future in the batch10 audit).
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
            parent = payload.get("parent") if isinstance(payload.get("parent"), dict) else None
            source = payload.get("source") if isinstance(payload.get("source"), dict) else None
            license_block = (
                payload.get("license") if isinstance(payload.get("license"), dict) else {}
            )
            owner_block = (
                payload.get("owner") if isinstance(payload.get("owner"), dict) else {}
            )
            return {
                "stargazers_count": payload.get("stargazers_count"),
                "forks_count": payload.get("forks_count"),
                "created_at": payload.get("created_at"),
                "fork": bool(payload.get("fork")),
                "parent_html_url": parent.get("html_url") if parent else None,
                "parent_full_name": parent.get("full_name") if parent else None,
                "source_html_url": source.get("html_url") if source else None,
                "source_full_name": source.get("full_name") if source else None,
                # Rich GitHub REST fields preserved for `_`-prefixed
                # internal-only output (surfaced when the caller passes
                # `?include_internal_fields=true`). The ontology doesn't
                # model these yet so they ride along under the
                # underscore convention without breaking SHACL.
                "homepage": payload.get("homepage"),
                "default_branch": payload.get("default_branch"),
                "language": payload.get("language"),
                "size": payload.get("size"),
                "archived": payload.get("archived"),
                "disabled": payload.get("disabled"),
                "pushed_at": payload.get("pushed_at"),
                "updated_at": payload.get("updated_at"),
                "open_issues_count": payload.get("open_issues_count"),
                "watchers_count": payload.get("watchers_count"),
                "subscribers_count": payload.get("subscribers_count"),
                "network_count": payload.get("network_count"),
                "has_wiki": payload.get("has_wiki"),
                "has_pages": payload.get("has_pages"),
                "has_discussions": payload.get("has_discussions"),
                "has_issues": payload.get("has_issues"),
                "has_projects": payload.get("has_projects"),
                "visibility": payload.get("visibility"),
                "license": {
                    "spdx_id": license_block.get("spdx_id"),
                    "name": license_block.get("name"),
                    "url": license_block.get("url"),
                },
                "topics": payload.get("topics"),
                "owner": {
                    "login": owner_block.get("login"),
                    "type": owner_block.get("type"),
                    "avatar_url": owner_block.get("avatar_url"),
                    "html_url": owner_block.get("html_url"),
                },
            }

        if self._cache is None:
            return _fetch()
        # Cache key bumped to v3 when the richer GitHub REST fields
        # (homepage / default_branch / size / archived / *_count / etc.)
        # were added so the old `_v2` entries (which lacked them) don't
        # shadow the new response shape.
        key = ProviderCache.make_key("github", "get_repository_rest_v3", full_name=full_name)
        return self._cache.get_or_set(
            key,
            _fetch,
            label=f"github.get_repository_rest({full_name})",
        )

    def get_repository_releases(self, full_name: str) -> list[dict[str, Any]]:
        """Fetch published releases for ``owner/repo`` via the REST API
        (`/repos/{owner}/{repo}/releases`), newest first, cached.

        Public endpoint — no extra token scope. Capped at the first page
        (100); repos with more releases are rare and the newest are what
        downstream consumers care about. Returns a thinned list; empty on
        404 / non-200 / transport error so extraction never breaks here.
        """

        def _thin(release: dict[str, Any]) -> dict[str, Any]:
            assets_raw = release.get("assets")
            assets = assets_raw if isinstance(assets_raw, list) else []
            return {
                "tag_name": release.get("tag_name"),
                "name": release.get("name"),
                "draft": bool(release.get("draft")),
                "prerelease": bool(release.get("prerelease")),
                "published_at": release.get("published_at"),
                "created_at": release.get("created_at"),
                "html_url": release.get("html_url"),
                "tarball_url": release.get("tarball_url"),
                "zipball_url": release.get("zipball_url"),
                "assets": [
                    {
                        "name": a.get("name"),
                        "browser_download_url": a.get("browser_download_url"),
                        "content_type": a.get("content_type"),
                        "size": a.get("size"),
                        "download_count": a.get("download_count"),
                    }
                    for a in assets
                    if isinstance(a, dict)
                ],
            }

        def _fetch() -> list[dict[str, Any]]:
            url = f"https://api.github.com/repos/{full_name}/releases?per_page=100"
            try:
                response = self._run_with_rate_limit(
                    lambda: requests.get(url, headers=_github_auth_headers(), timeout=15),
                )
            except Exception:  # noqa: BLE001
                logger.exception("github releases fetch failed: %s", full_name)
                return []
            if response.status_code == 404:
                return []
            if response.status_code != 200:
                logger.info(
                    "github releases fetch returned %d for %s",
                    response.status_code, full_name,
                )
                return []
            try:
                payload = response.json()
            except ValueError:
                logger.exception("github releases response not JSON: %s", full_name)
                return []
            if not isinstance(payload, list):
                return []
            return [_thin(r) for r in payload if isinstance(r, dict)]

        if self._cache is None:
            return _fetch()
        key = ProviderCache.make_key("github", "get_releases_v1", full_name=full_name)
        return self._cache.get_or_set(
            key, _fetch, label=f"github.get_releases({full_name})",
        )

    def get_repository_community_profile(self, full_name: str) -> dict[str, Any] | None:
        """Fetch GitHub's community health profile for ``owner/repo`` via
        `/repos/{owner}/{repo}/community/profile` (public, cached). Returns a
        thinned dict (health %, documentation URL, file-presence booleans), or
        None on 404 / non-200 / transport error."""

        def _fetch() -> dict[str, Any] | None:
            url = f"https://api.github.com/repos/{full_name}/community/profile"
            try:
                response = self._run_with_rate_limit(
                    lambda: requests.get(url, headers=_github_auth_headers(), timeout=15),
                )
            except Exception:  # noqa: BLE001
                logger.exception("github community profile fetch failed: %s", full_name)
                return None
            if response.status_code == 404:
                return None
            if response.status_code != 200:
                logger.info(
                    "github community profile returned %d for %s",
                    response.status_code, full_name,
                )
                return None
            try:
                payload = response.json()
            except ValueError:
                logger.exception("github community profile not JSON: %s", full_name)
                return None
            if not isinstance(payload, dict):
                return None
            files = payload.get("files") if isinstance(payload.get("files"), dict) else {}
            return {
                "health_percentage": payload.get("health_percentage"),
                "documentation": payload.get("documentation"),
                "has_code_of_conduct": files.get("code_of_conduct") is not None,
                "has_contributing": files.get("contributing") is not None,
                "has_issue_template": files.get("issue_template") is not None,
                "has_pull_request_template": (
                    files.get("pull_request_template") is not None
                ),
            }

        if self._cache is None:
            return _fetch()
        key = ProviderCache.make_key(
            "github", "get_community_profile_v1", full_name=full_name,
        )
        return self._cache.get_or_set(
            key, _fetch, label=f"github.get_community_profile({full_name})",
        )

    def get_repository_tags(self, full_name: str) -> list[str]:
        """Fetch git tag names for ``owner/repo`` via `/repos/.../tags`
        (public, cached, first page of 100, newest first). Empty on
        404 / non-200 / transport error."""

        def _fetch() -> list[str]:
            url = f"https://api.github.com/repos/{full_name}/tags?per_page=100"
            try:
                response = self._run_with_rate_limit(
                    lambda: requests.get(url, headers=_github_auth_headers(), timeout=15),
                )
            except Exception:  # noqa: BLE001
                logger.exception("github tags fetch failed: %s", full_name)
                return []
            if response.status_code != 200:
                return []
            try:
                payload = response.json()
            except ValueError:
                return []
            if not isinstance(payload, list):
                return []
            return [
                t["name"] for t in payload
                if isinstance(t, dict) and isinstance(t.get("name"), str) and t["name"]
            ]

        if self._cache is None:
            return _fetch()
        key = ProviderCache.make_key("github", "get_tags_v1", full_name=full_name)
        return self._cache.get_or_set(
            key, _fetch, label=f"github.get_tags({full_name})",
        )

    def _fetch_file_text(self, full_name: str, path: str) -> str | None:
        """Fetch a single repo file's text via the contents API (base64), capped
        at 100 KB. None on any failure."""
        url = f"https://api.github.com/repos/{full_name}/contents/{quote(path)}"
        try:
            resp = self._run_with_rate_limit(
                lambda: requests.get(url, headers=_github_auth_headers(), timeout=15),
            )
        except Exception:  # noqa: BLE001
            logger.exception("github file fetch failed: %s/%s", full_name, path)
            return None
        if resp.status_code != 200:
            return None
        try:
            encoded = resp.json().get("content")
        except (ValueError, AttributeError):
            return None
        if not isinstance(encoded, str):
            return None
        try:
            return base64.b64decode(encoded)[:100_000].decode("utf-8", errors="replace")
        except (ValueError, UnicodeDecodeError):
            return None

    def get_repository_compose_files(self, full_name: str) -> list[dict[str, Any]]:
        """Fetch Docker Compose files anywhere in ``owner/repo`` (public,
        cached). One recursive-tree call finds the files, then each is fetched
        (base64 contents, capped). Returns ``[{path, html_url, content}]``;
        empty on any failure. Capped at the first 20 matches."""

        max_files = 20

        def _fetch() -> list[dict[str, Any]]:
            url = f"https://api.github.com/repos/{full_name}/git/trees/HEAD?recursive=1"
            try:
                resp = self._run_with_rate_limit(
                    lambda: requests.get(url, headers=_github_auth_headers(), timeout=20),
                )
            except Exception:  # noqa: BLE001
                logger.exception("github tree fetch failed: %s", full_name)
                return []
            if resp.status_code != 200:
                return []
            try:
                tree = resp.json().get("tree")
            except (ValueError, AttributeError):
                return []
            paths = [
                t["path"] for t in tree
                if isinstance(t, dict) and t.get("type") == "blob"
                and isinstance(t.get("path"), str) and _is_compose_path(t["path"])
            ][:max_files] if isinstance(tree, list) else []
            out: list[dict[str, Any]] = []
            for path in paths:
                content = self._fetch_file_text(full_name, path)
                if content is not None:
                    out.append({
                        "path": path,
                        "html_url": f"https://github.com/{full_name}/blob/HEAD/{path}",
                        "content": content,
                    })
            return out

        if self._cache is None:
            return _fetch()
        key = ProviderCache.make_key("github", "get_compose_files_v1", full_name=full_name)
        return self._cache.get_or_set(
            key, _fetch, label=f"github.get_compose_files({full_name})",
        )

    def _list_owner_container_packages(self, owner: str) -> dict[str, Any]:
        """List an owner's GHCR container packages, cached by owner.

        GitHub has no per-repo packages endpoint, so we enumerate per
        owner and the caller filters. Tries the org endpoint first and
        falls back to the user endpoint on 404 (owner is a user, not an
        org). Returns ``{"scope": "orgs"|"users"|None, "packages": [...]}``;
        ``scope`` records which endpoint won so version lookups hit the
        same one. On 401/403 (token lacks ``read:packages``) returns an
        empty result — releases and the rest of extraction are
        unaffected.
        """

        def _request(scope: str) -> requests.Response | None:
            url = (
                f"https://api.github.com/{scope}/{owner}/packages"
                "?package_type=container&per_page=100"
            )
            try:
                return self._run_with_rate_limit(
                    lambda: requests.get(url, headers=_github_auth_headers(), timeout=15),
                )
            except Exception:  # noqa: BLE001
                logger.exception("github packages list failed: %s (%s)", owner, scope)
                return None

        def _fetch() -> dict[str, Any]:
            for scope in ("orgs", "users"):
                response = _request(scope)
                if response is None:
                    continue
                if response.status_code in (401, 403):
                    logger.warning(
                        "github packages list %d for %s — GitHub token lacks the "
                        "'read:packages' scope; skipping container-image extraction. "
                        "Add read:packages to GME_GITHUB_TOKEN to enable it.",
                        response.status_code, owner,
                    )
                    return {"scope": None, "packages": []}
                if response.status_code == 404:
                    # Not this owner-kind; try the next scope.
                    continue
                if response.status_code != 200:
                    logger.info(
                        "github packages list returned %d for %s (%s)",
                        response.status_code, owner, scope,
                    )
                    continue
                try:
                    payload = response.json()
                except ValueError:
                    return {"scope": None, "packages": []}
                if isinstance(payload, list):
                    return {
                        "scope": scope,
                        "packages": [p for p in payload if isinstance(p, dict)],
                    }
            return {"scope": None, "packages": []}

        if self._cache is None:
            return _fetch()
        key = ProviderCache.make_key("github", "list_container_packages_v1", owner=owner)
        return self._cache.get_or_set(
            key, _fetch, label=f"github.list_container_packages({owner})",
        )

    def _list_container_package_tags(
        self, owner: str, scope: str, package_name: str,
    ) -> list[str]:
        """Collect the tag strings across all versions of one container
        package, cached. Package names can contain ``/`` so they are
        URL-encoded. Empty list on error."""

        def _fetch() -> list[str]:
            encoded = quote(package_name, safe="")
            url = (
                f"https://api.github.com/{scope}/{owner}"
                f"/packages/container/{encoded}/versions?per_page=100"
            )
            try:
                response = self._run_with_rate_limit(
                    lambda: requests.get(url, headers=_github_auth_headers(), timeout=15),
                )
            except Exception:  # noqa: BLE001
                logger.exception("github package versions failed: %s/%s", owner, package_name)
                return []
            if response.status_code != 200:
                return []
            try:
                versions = response.json()
            except ValueError:
                return []
            if not isinstance(versions, list):
                return []
            tags: list[str] = []
            for version in versions:
                if not isinstance(version, dict):
                    continue
                container = (version.get("metadata") or {}).get("container") or {}
                for tag in container.get("tags") or []:
                    if isinstance(tag, str) and tag not in tags:
                        tags.append(tag)
            return tags

        if self._cache is None:
            return _fetch()
        key = ProviderCache.make_key(
            "github", "container_pkg_tags_v1", owner=owner, package=package_name,
        )
        return self._cache.get_or_set(
            key, _fetch, label=f"github.container_pkg_tags({owner}/{package_name})",
        )

    def get_repository_container_images(self, full_name: str) -> list[dict[str, Any]]:
        """Fetch GHCR container (Docker) images published from ``owner/repo``.

        Lists the owner's container packages (cached per owner) and keeps
        those whose `repository.full_name` matches, with a name-convention
        fallback (`package.name == <repo>`). For each match the version
        tags are collected. Requires ``read:packages``; without it the
        owner listing returns empty and so does this. The repo→image link
        is only as reliable as the image's `org.opencontainers.image.source`
        label — name-convention matches are flagged as such.
        """
        if "/" not in full_name:
            return []
        owner, repo_short = full_name.split("/", maxsplit=1)
        listing = self._list_owner_container_packages(owner)
        scope = listing.get("scope")
        packages = listing.get("packages") or []
        if not scope or not packages:
            return []

        images: list[dict[str, Any]] = []
        for pkg in packages:
            name = pkg.get("name")
            if not isinstance(name, str) or not name:
                continue
            repo_block = pkg.get("repository") if isinstance(pkg.get("repository"), dict) else {}
            linked_full = repo_block.get("full_name")
            linked_match = isinstance(linked_full, str) and linked_full.lower() == full_name.lower()
            name_match = name.lower() == repo_short.lower()
            if not (linked_match or name_match):
                continue
            images.append(
                {
                    "name": name,
                    "image": f"ghcr.io/{owner}/{name}",
                    "visibility": pkg.get("visibility"),
                    "linked_repository": linked_full if isinstance(linked_full, str) else None,
                    "match": "repository" if linked_match else "name_convention",
                    "tags": self._list_container_package_tags(owner, scope, name),
                    "updated_at": pkg.get("updated_at"),
                    "created_at": pkg.get("created_at"),
                    "html_url": pkg.get("html_url"),
                },
            )
        return images

    def get_repository_readme(self, full_name: str) -> str:
        """Fetch the README content for a repository via the GitHub REST
        API (`/repos/{owner}/{repo}/readme`).

        The endpoint returns the README in whatever location/casing the
        repo uses (`README.md`, `README.rst`, `readme.txt`, …) and the
        content as base64 by default. We decode, cap at 100KB, and
        cache. Empty string when the repo has no README or the call
        fails — callers (context_gather) fall back to repo description.

        Why this isn't part of `get_repository`: the upstream gimie
        JSON-LD that `get_repository` consumes does NOT carry the README
        body, only `schema:description` (the repo's short tagline). For
        a while the refiners were running on that 138-byte tagline
        thinking it was the README. Surface it as its own field so
        callers can pull it explicitly.
        """

        max_bytes = 100_000

        def _fetch() -> str:
            url = f"https://api.github.com/repos/{full_name}/readme"
            try:
                response = self._run_with_rate_limit(
                    lambda: requests.get(url, headers=_github_auth_headers(), timeout=15),
                )
            except Exception:  # noqa: BLE001
                logger.exception("github README fetch failed: %s", full_name)
                return ""
            if response.status_code == 404:
                return ""
            if response.status_code != 200:
                logger.info(
                    "github README fetch returned %d for %s",
                    response.status_code, full_name,
                )
                return ""
            try:
                payload = response.json()
            except ValueError:
                logger.exception("github README response not JSON: %s", full_name)
                return ""
            if not isinstance(payload, dict):
                return ""
            content_b64 = payload.get("content")
            encoding = payload.get("encoding")
            if not isinstance(content_b64, str) or encoding != "base64":
                return ""
            try:
                decoded = base64.b64decode(content_b64).decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                logger.exception("github README base64-decode failed: %s", full_name)
                return ""
            return decoded[:max_bytes]

        if self._cache is None:
            return _fetch()
        key = ProviderCache.make_key("github", "get_repository_readme", full_name=full_name)
        return self._cache.get_or_set(
            key,
            _fetch,
            label=f"github.get_repository_readme({full_name})",
        )

    def get_profile_readme(self, owner: str, *, is_organization: bool) -> str:
        """Fetch the GitHub *profile* README — the markdown shown on the
        account's profile page.

        - **User** ``<owner>``: the README of the special ``<owner>/<owner>``
          repository (`GET /repos/{owner}/{owner}/readme`).
        - **Organization** ``<owner>``: ``profile/README.md`` inside the
          ``<owner>/.github`` repository
          (`GET /repos/{owner}/.github/readme/profile`).

        Returns an empty string when no profile README exists (the common
        case — most accounts don't have one) or the call fails. The body is
        a rich "what is this account" description ideal as agent context.
        """

        owner = (owner or "").strip().strip("/")
        if not owner:
            return ""
        if is_organization:
            url = f"https://api.github.com/repos/{owner}/.github/readme/profile"
            subject = f"{owner}/.github:profile"
        else:
            url = f"https://api.github.com/repos/{owner}/{owner}/readme"
            subject = f"{owner}/{owner}"

        max_bytes = 100_000

        def _fetch() -> str:
            try:
                response = self._run_with_rate_limit(
                    lambda: requests.get(url, headers=_github_auth_headers(), timeout=15),
                )
            except Exception:  # noqa: BLE001
                logger.exception("github profile README fetch failed: %s", subject)
                return ""
            if response.status_code == 404:
                return ""
            if response.status_code != 200:
                logger.info(
                    "github profile README fetch returned %d for %s",
                    response.status_code, subject,
                )
                return ""
            try:
                payload = response.json()
            except ValueError:
                logger.exception("github profile README response not JSON: %s", subject)
                return ""
            if not isinstance(payload, dict):
                return ""
            content_b64 = payload.get("content")
            if not isinstance(content_b64, str) or payload.get("encoding") != "base64":
                return ""
            try:
                decoded = base64.b64decode(content_b64).decode("utf-8", errors="replace")
            except Exception:  # noqa: BLE001
                logger.exception("github profile README base64-decode failed: %s", subject)
                return ""
            return decoded[:max_bytes]

        if self._cache is None:
            return _fetch()
        key = ProviderCache.make_key("github", "get_profile_readme", subject=subject)
        return self._cache.get_or_set(
            key,
            _fetch,
            label=f"github.get_profile_readme({subject})",
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

    # Curated set of repo-root files that often carry attribution /
    # team / funding info the rest of the pipeline can't recover from
    # ORCID or Infoscience. Lowercased keys are matched case-
    # insensitively against the directory listing.
    _AUX_FILE_PATTERNS: frozenset[str] = frozenset({
        # Author / maintainer / contributor lists
        "authors", "authors.md", "authors.rst", "authors.txt",
        "contributors", "contributors.md", "contributors.txt",
        "maintainers", "maintainers.md", "maintainers.yml", "maintainers.yaml",
        "owners", "owners.yaml", "owners.yml",
        # Citation / bibliographic self-description
        "citation", "citation.cff", "citation.md", "citation.bib",
        "publications.md", "papers.md",
        # Apache-style attribution / acknowledgments
        "notice", "notice.md", "notice.txt", "notice.yml", "notice.yaml",
        "acknowledgments.md", "acknowledgements.md",
        # Governance / contribution / security / funding
        "contributing.md", "code_of_conduct.md", "governance.md",
        "security.md", "funding.md",
        # Language / ecosystem manifests with author / contributor fields
        "pyproject.toml", "setup.cfg", "setup.py",
        "package.json", "cargo.toml", "composer.json",
        "go.mod", "package.swift", "project.toml", "pom.xml",
        "description",  # R package manifest (Author / Maintainer fields)
        # Research-software & public-sector metadata standards
        ".zenodo.json", "codemeta.json", "publiccode.yml", "publiccode.yaml",
        # Some projects spell it "CONTRIBUTION.md" instead of CONTRIBUTING.md.
        "contribution.md",
    })

    # Suffix-based matches for ecosystems that name files <project>.ext
    # (e.g. CocoaPods `MyLib.podspec`, RubyGems `my_gem.gemspec`).
    _AUX_FILE_SUFFIXES: tuple[str, ...] = (
        ".gemspec",
        ".podspec",
    )

    # Files inside `.github/` that carry maintainer / funding signal.
    # CODEOWNERS lives there by convention (also valid at root or in
    # `docs/`, but `.github/` is the canonical and most-common spot).
    # FUNDING.yml is GitHub Sponsors / Open Collective / Patreon /
    # custom funding URLs.
    _AUX_DOTGITHUB_FILE_PATTERNS: frozenset[str] = frozenset({
        "codeowners",
        "funding.yml",
        "funding.yaml",
    })

    def get_repository_aux_files(self, full_name: str) -> dict[str, str]:
        """Fetch repo-root evidence files (AUTHORS, CITATION.cff,
        NOTICE, pyproject.toml, etc.) that complement the README with
        attribution / funding / governance information.

        Two-phase fetch to keep the API budget honest:

        1. ONE call to `GET /repos/{owner}/{repo}/contents/` to list
           repo-root entries and pick which of our curated filenames
           actually exist (same idea gimie's `parsers/__init__.py`
           uses to gate its CITATION/LICENSE parsers).
        2. One raw-content fetch per surviving filename, capped at
           50 KB each.

        Returns `{filename: content}` for files found at the repo
        root. Empty dict when none match. Cached at the per-repo level
        with the same TTL as the rest of the github provider so a
        re-extract within the window is free.
        """

        max_bytes = 50_000

        def _list_dir_files(path: str) -> set[str]:
            url = f"https://api.github.com/repos/{full_name}/contents/{path}"
            try:
                response = self._run_with_rate_limit(
                    lambda: requests.get(url, headers=_github_auth_headers(), timeout=15),
                )
            except Exception:  # noqa: BLE001
                logger.exception(
                    "github contents listing failed: %s /%s",
                    full_name, path,
                )
                return set()
            if response.status_code == 404:
                # Directory absent — common for `.github/`; not an error.
                return set()
            if response.status_code != 200:
                logger.info(
                    "github contents listing returned %d for %s /%s",
                    response.status_code, full_name, path,
                )
                return set()
            try:
                payload = response.json()
            except ValueError:
                logger.exception(
                    "github contents listing not JSON: %s /%s", full_name, path,
                )
                return set()
            if not isinstance(payload, list):
                return set()
            return {
                item["name"]
                for item in payload
                if isinstance(item, dict)
                and item.get("type") == "file"
                and isinstance(item.get("name"), str)
            }

        def _fetch_raw(path: str) -> str | None:
            url = f"https://raw.githubusercontent.com/{full_name}/HEAD/{path}"
            try:
                response = self._run_with_rate_limit(
                    lambda: requests.get(url, headers=_github_auth_headers(), timeout=15),
                )
            except Exception:  # noqa: BLE001
                return None
            if response.status_code != 200:
                return None
            content = response.text or ""
            return content[:max_bytes] if content else None

        def _fetch() -> dict[str, str]:
            out: dict[str, str] = {}

            root_names = _list_dir_files("")
            interesting = [
                name for name in root_names
                if name.lower() in self._AUX_FILE_PATTERNS
                or name.lower().endswith(self._AUX_FILE_SUFFIXES)
            ]
            for name in interesting:
                content = _fetch_raw(name)
                if content:
                    out[name] = content

            # `.github/` carries the canonical CODEOWNERS / FUNDING.yml.
            # One extra listing call per extract — skipped silently when
            # the directory doesn't exist.
            dotgithub_names = _list_dir_files(".github")
            dotgithub_interesting = [
                name for name in dotgithub_names
                if name.lower() in self._AUX_DOTGITHUB_FILE_PATTERNS
            ]
            for name in dotgithub_interesting:
                content = _fetch_raw(f".github/{name}")
                if content:
                    out[f".github/{name}"] = content

            return out

        if self._cache is None:
            return _fetch()
        key = ProviderCache.make_key("github", "get_repository_aux_files", full_name=full_name)
        return self._cache.get_or_set(
            key,
            _fetch,
            label=f"github.get_repository_aux_files({full_name})",
        )

    def get_repository_root_entries(self, full_name: str) -> list[str] | None:
        """Return the names of ALL entries (files and directories) at the
        repository root, or ``None`` on failure.

        Uses the same ``GET /repos/{owner}/{repo}/contents/`` endpoint as
        ``get_repository_aux_files`` but returns the raw name list instead
        of fetching file contents.  The result is cached with the same TTL
        as other per-repo calls.
        """

        def _fetch() -> list[str] | None:
            url = f"https://api.github.com/repos/{full_name}/contents/"
            try:
                response = self._run_with_rate_limit(
                    lambda: requests.get(url, headers=_github_auth_headers(), timeout=15),
                )
            except Exception:  # noqa: BLE001
                logger.exception("github root-listing failed for %s", full_name)
                return None
            if response.status_code != 200:
                logger.info(
                    "github root-listing returned %d for %s",
                    response.status_code,
                    full_name,
                )
                return None
            try:
                payload = response.json()
            except ValueError:
                logger.exception("github root-listing not JSON for %s", full_name)
                return None
            if not isinstance(payload, list):
                return None
            return [
                item["name"]
                for item in payload
                if isinstance(item, dict) and isinstance(item.get("name"), str)
            ]

        if self._cache is None:
            return _fetch()
        key = ProviderCache.make_key(
            "github", "get_repository_root_entries", full_name=full_name
        )
        return self._cache.get_or_set(
            key,
            _fetch,
            label=f"github.get_repository_root_entries({full_name})",
        )

    def get_commit_bookends(
        self,
        full_name: str,
        login: str,
    ) -> dict[str, Any]:
        """Return `(first, last)` commit dates and total count for `login`.

        Always issues two API calls (regardless of repo age) so we
        capture the exact first and last commit dates rather than the
        52-week approximation from `/stats/contributors`:

        1. ``GET /repos/{full_name}/commits?author={login}&per_page=1``
           — the most recent commit (`last_date`). The `Link: ...
           rel="last"` header carries `?page=N` where `N` is the total
           number of commits by this author.
        2. ``GET /repos/{full_name}/commits?author={login}&per_page=1&page={N}``
           — the oldest commit (`first_date`).

        When the repo has only one commit by this author, both calls
        return the same record (`first_date == last_date`, `count == 1`).

        Failures (network, non-200, malformed response) collapse to
        `{first_date: None, last_date: None, count: 0}`. Result is
        cached per the standard `V2_PROVIDER_CACHE_TTL_DAYS` TTL — a
        miss won't re-hit GitHub until the cache entry expires (one
        month by default), so transient failures don't trigger
        per-request retry storms.
        """

        def _fetch() -> dict[str, Any]:
            last_date, count = self._fetch_commit_page(
                full_name, login, page=1,
            )
            if count <= 0 or last_date is None:
                return {"first_date": None, "last_date": None, "count": 0}
            if count == 1:
                return {
                    "first_date": last_date,
                    "last_date": last_date,
                    "count": 1,
                }
            first_date, _ = self._fetch_commit_page(
                full_name, login, page=count,
            )
            return {
                "first_date": first_date,
                "last_date": last_date,
                "count": count,
            }

        if self._cache is None:
            return _fetch()
        key = ProviderCache.make_key(
            "github",
            "get_commit_bookends",
            full_name=full_name,
            login=login,
        )
        return self._cache.get_or_set(
            key,
            _fetch,
            label=f"github.get_commit_bookends({full_name},{login})",
        )

    def _fetch_commit_page(
        self,
        full_name: str,
        login: str,
        *,
        page: int,
    ) -> tuple[str | None, int]:
        """Return ``(commit_date_iso, total_count)`` for one page of size 1.

        ``total_count`` is parsed from the ``Link: rel="last"`` header
        (page index when ``per_page=1``). If the header is absent the
        result set fits in one page, so we return ``len(body)``.
        """
        url = f"https://api.github.com/repos/{full_name}/commits"
        params = {"author": login, "per_page": "1", "page": str(page)}
        try:
            response = self._run_with_rate_limit(
                lambda: requests.get(
                    url,
                    params=params,
                    headers=_github_auth_headers(),
                    timeout=15,
                ),
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "github commit page fetch failed: %s author=%s page=%d",
                full_name,
                login,
                page,
            )
            return None, 0
        if response.status_code != 200:
            logger.warning(
                "github commit page returned %d for %s author=%s page=%d",
                response.status_code,
                full_name,
                login,
                page,
            )
            return None, 0
        try:
            payload = response.json()
        except ValueError:
            logger.exception(
                "github commit page response not JSON: %s author=%s",
                full_name,
                login,
            )
            return None, 0
        if not isinstance(payload, list) or not payload:
            return None, 0
        commit = payload[0].get("commit") if isinstance(payload[0], dict) else None
        date: str | None = None
        if isinstance(commit, dict):
            committer = commit.get("committer") if isinstance(commit.get("committer"), dict) else {}
            author = commit.get("author") if isinstance(commit.get("author"), dict) else {}
            date = committer.get("date") or author.get("date")
        last_page = _parse_link_last_page(response.headers.get("Link", ""))
        count = last_page if last_page > 0 else len(payload)
        return date if isinstance(date, str) else None, count

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
