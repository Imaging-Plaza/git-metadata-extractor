from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse
from uuid import uuid4

from src.v2.agents.models import AgentResult, ProviderSet, validate_permissive

CompiledContextStage = Callable[[dict[str, Any], ProviderSet], dict[str, Any] | Awaitable[dict[str, Any]]]
StructuredOutputStage = Callable[
    [dict[str, Any], ProviderSet, dict[str, Any]],
    dict[str, Any] | Awaitable[dict[str, Any]],
]
RepositoryClassifierStage = Callable[
    [dict[str, Any], ProviderSet, dict[str, Any], dict[str, Any]],
    dict[str, Any] | Awaitable[dict[str, Any]],
]

MIN_REPOSITORY_SEGMENTS = 2
DATE_ONLY_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}$")
STRICT_TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


def _to_list_of_strings(value: Any) -> list[str]:
    if isinstance(value, str) and value:
        return [value]
    if isinstance(value, list):
        return [item for item in value if isinstance(item, str) and item]
    return []


def _extract_contributor_logins(contributors: Any) -> list[str]:
    if not isinstance(contributors, list):
        return []

    logins: list[str] = []
    seen: set[str] = set()
    for contributor in contributors:
        login = None
        if isinstance(contributor, dict):
            login = contributor.get("login")
        elif isinstance(contributor, str):
            login = contributor

        if not isinstance(login, str) or not login:
            continue
        normalized_login = login.strip()
        if not normalized_login or normalized_login in seen:
            continue
        seen.add(normalized_login)
        logins.append(normalized_login)
    return logins


def _ensure_repo_handle(context: dict[str, Any]) -> str:
    for key in ("full_name", "repository_handle", "github_repository_handle"):
        value = context.get(key)
        if isinstance(value, str) and "/" in value:
            return value.strip()

    source_url = context.get("source_url")
    if isinstance(source_url, str):
        parsed = urlparse(source_url if "://" in source_url else f"https://{source_url}")
        segments = [segment for segment in parsed.path.split("/") if segment]
        if len(segments) >= MIN_REPOSITORY_SEGMENTS:
            return f"{segments[0]}/{segments[1]}"

    message = "Repository context is missing a GitHub owner/repository handle"
    raise ValueError(message)


def _normalize_license_url(spdx_id: Any) -> str | None:
    if not isinstance(spdx_id, str):
        return None
    if not spdx_id or spdx_id.upper() == "NOASSERTION":
        return None
    return f"https://spdx.org/licenses/{spdx_id}.html"


def _normalize_created_at(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None
    if STRICT_TIMESTAMP_PATTERN.fullmatch(candidate):
        return candidate
    if DATE_ONLY_PATTERN.fullmatch(candidate):
        return f"{candidate}T00:00:00Z"

    normalized = candidate.replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return candidate

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value


class RepositoryAgentV2:
    """Repository agent wrapper with permissive output validation."""

    def __init__(
        self,
        *,
        context_compiler: CompiledContextStage | None = None,
        structured_output: StructuredOutputStage | None = None,
        repository_classifier: RepositoryClassifierStage | None = None,
    ) -> None:
        self._context_compiler = context_compiler or self._default_context_compiler
        self._structured_output = structured_output or self._default_structured_output
        self._repository_classifier = (
            repository_classifier or self._default_repository_classifier
        )

    async def _default_context_compiler(
        self,
        context: dict[str, Any],
        providers: ProviderSet,
    ) -> dict[str, Any]:
        full_name = _ensure_repo_handle(context)

        repository_context = context.get("repository_context")
        reuse_gathered_context = (
            isinstance(repository_context, dict)
            and repository_context.get("full_name") == full_name
        )

        repository: dict[str, Any]
        contributors: list[dict[str, Any]]
        languages: dict[str, Any]

        if reuse_gathered_context and isinstance(repository_context, dict):
            metadata_candidate = repository_context.get("metadata")
            repository = metadata_candidate if isinstance(metadata_candidate, dict) else {}

            contributors_candidate = repository_context.get("contributors")
            contributors = (
                contributors_candidate
                if isinstance(contributors_candidate, list)
                else []
            )

            languages_candidate = repository_context.get("languages")
            languages = languages_candidate if isinstance(languages_candidate, dict) else {}
        else:
            repository = providers.github.get_repository(full_name)
            contributors = providers.github.get_contributors(full_name)
            languages = providers.github.get_languages(full_name)

        return {
            "full_name": full_name,
            "repository": repository,
            "contributors": contributors,
            "languages": languages,
        }

    async def _default_structured_output(  # noqa: C901
        self,
        context: dict[str, Any],
        _providers: ProviderSet,
        compiled_context: dict[str, Any],
    ) -> dict[str, Any]:
        repository = compiled_context["repository"]
        full_name = str(repository.get("full_name") or compiled_context["full_name"])
        contributors = compiled_context.get("contributors", [])
        languages = compiled_context.get("languages", {})

        author_ids = []
        if isinstance(contributors, list):
            for contributor in contributors:
                if not isinstance(contributor, dict):
                    continue
                login = contributor.get("login")
                if isinstance(login, str) and login:
                    author_ids.append(login)
        if not author_ids:
            owner_login = repository.get("owner", {}).get("login")
            if isinstance(owner_login, str) and owner_login:
                author_ids = [owner_login]
        if not author_ids:
            author_ids = ["unknown-author"]

        programming_languages: list[str] = []
        if isinstance(languages, dict):
            languages = list(languages)
        if isinstance(languages, list):
            programming_languages = sorted(
                [language for language in languages if isinstance(language, str) and language],
            )

        doi = context.get("doi")
        doi_value = doi if isinstance(doi, str) and doi.strip() else None
        uuid_value = context.get("uuid")
        if not isinstance(uuid_value, str) or not uuid_value.strip():
            uuid_value = str(uuid4())

        return {
            "id": full_name,
            "type": "schema:SoftwareSourceCode",
            "shacl": "pulse:RepositoryShape",
            "identifiers": {
                "pulse:githubRepositoryHandle": full_name,
                "schema:identifier": doi_value,
                "uuid": uuid_value,
            },
            "idSource": "pulse:githubRepositoryHandle",
            "schema:name": repository.get("name") or full_name.split("/", maxsplit=1)[-1],
            "pulse:githubRepositoryHandle": full_name,
            "schema:author": author_ids,
            "pulse:githubRepoStars": repository.get("stargazers_count"),
            "pulse:githubRepoForks": repository.get("forks_count"),
            "schema:dateCreated": _normalize_created_at(repository.get("created_at")),
            "schema:license": _normalize_license_url(
                repository.get("license", {}).get("spdx_id"),
            ),
            "schema:citation": f"https://doi.org/{doi_value}" if doi_value else None,
            "schema:programmingLanguage": programming_languages,
            "pulse:ownedBy": repository.get("owner", {}).get("login"),
            "pulse:isForkOf": repository.get("source", {}).get("full_name")
            if repository.get("fork")
            else None,
        }

    async def _default_repository_classifier(
        self,
        context: dict[str, Any],
        _providers: ProviderSet,
        compiled_context: dict[str, Any],
        _structured_payload: dict[str, Any],
    ) -> dict[str, Any]:
        languages = compiled_context.get("languages", {})
        language_names = [
            language.lower()
            for language in languages
            if isinstance(language, str)
        ] if isinstance(languages, dict) else []

        repository_type = "pulse:Software"
        if language_names and all(language in {"markdown", "rst", "text"} for language in language_names):
            repository_type = "pulse:Documentation"

        disciplines = _to_list_of_strings(context.get("disciplines"))
        if not disciplines:
            disciplines = ["wd:Q8434"]

        return {
            "pulse:repositoryType": repository_type,
            "pulse:discipline": disciplines,
        }

    async def run(
        self,
        context: dict[str, Any],
        providers: ProviderSet,
    ) -> AgentResult:
        warnings: list[str] = []
        compiled_context = await _maybe_await(self._context_compiler(context, providers))
        structured_payload = await _maybe_await(
            self._structured_output(context, providers, compiled_context),
        )
        classification_payload = await _maybe_await(
            self._repository_classifier(
                context,
                providers,
                compiled_context,
                structured_payload,
            ),
        )

        merged_payload = {
            **structured_payload,
            **classification_payload,
        }
        overrides = context.get("agent_overrides")
        if isinstance(overrides, dict):
            merged_payload.update(overrides)

        raw_output = deepcopy(merged_payload)
        validated_payload, validation_warnings = validate_permissive(
            merged_payload,
            schema_name="repository",
        )
        warnings.extend(validation_warnings)

        repository_metadata = compiled_context.get("repository", {})
        owner = repository_metadata.get("owner") if isinstance(repository_metadata, dict) else None
        owner_login = owner.get("login") if isinstance(owner, dict) else None
        owner_type = owner.get("type") if isinstance(owner, dict) else None
        contributor_logins = _extract_contributor_logins(compiled_context.get("contributors"))
        language_names = sorted(
            [
                language
                for language in validated_payload.get("schema:programmingLanguage", [])
                if isinstance(language, str) and language
            ],
        )
        derivation_stats = {
            "repository_full_name": validated_payload.get("pulse:githubRepositoryHandle"),
            "source_repositories": [
                validated_payload.get("pulse:githubRepositoryHandle"),
            ]
            if isinstance(validated_payload.get("pulse:githubRepositoryHandle"), str)
            else [],
            "owner_login": owner_login if isinstance(owner_login, str) else None,
            "owner_type": owner_type if isinstance(owner_type, str) else None,
            "contributor_logins": contributor_logins,
            "contributors": deepcopy(compiled_context.get("contributors", []))
            if isinstance(compiled_context.get("contributors"), list)
            else [],
            "language_names": language_names,
        }

        return AgentResult(
            data=validated_payload,
            warnings=warnings,
            raw_output=raw_output,
            stats={"derivation": derivation_stats},
        )
