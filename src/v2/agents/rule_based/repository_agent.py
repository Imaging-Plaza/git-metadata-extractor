from __future__ import annotations

import re
from copy import deepcopy
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable
from urllib.parse import urlparse

from src.v2.agents.models import (
    AgentResult,
    ProviderSet,
    generate_uuid,
    validate_permissive,
)
from src.v2.agents.rule_based._repo_signals import (
    parse_docker_hub_url,
    parse_test_coverage,
    summarize_packages,
    summarize_registry_package,
    summarize_releases,
)
from src.v2.canonicalization.github import github_repo_iri, github_user_iri
from src.v2.parsers.citation_cff import parse_citation_cff
from src.v2.parsers.publiccode import parse_publiccode

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


# Case-insensitive lookups against the repo-root aux file list. Keys are
# lowercased filename matchers; values map to the `_*_url` internal
# field that should be stamped when the matcher hits. Order within a
# tuple is preference order — we use the first match. The provider
# (`get_repository_aux_files`) is responsible for fetching these; the
# agent only stamps a URL pointer so downstream stages (LLM refiners,
# graph consumers) can quote the source.
_REPO_AUX_FILE_LOOKUPS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("_citation_cff_url",  ("citation.cff",)),
    ("_authors_url",       ("authors", "authors.md", "authors.rst", "authors.txt")),
    # CONTRIBUTING is the standard spelling; CONTRIBUTION is rarer but
    # ships in a handful of older research projects.
    ("_contributing_url",  ("contributing.md", "contribution.md")),
    ("_publiccode_url",    ("publiccode.yml", "publiccode.yaml")),
    # SECURITY.md — GitHub's first-class security policy file. Surfacing
    # this lets dashboards flag repos with explicit vulnerability-
    # reporting guidance separately from undocumented ones, and gives
    # LLM agents a place to read for embargo / disclosure timelines.
    ("_security_url",      ("security.md",)),
)


def _resolve_publiccode_payload(aux_files: Any) -> dict[str, Any] | None:
    """Locate publiccode.yml / publiccode.yaml (case-insensitive) in
    the gathered ``aux_files`` and return the parsed payload, or None
    when none is present / parseable."""
    if not isinstance(aux_files, dict):
        return None
    for filename, content in aux_files.items():
        if not isinstance(filename, str) or not isinstance(content, str):
            continue
        lower = filename.lower()
        if lower in ("publiccode.yml", "publiccode.yaml"):
            return parse_publiccode(content)
    return None


def _resolve_citation_cff_payload(aux_files: Any) -> dict[str, Any] | None:
    """Locate CITATION.cff (any case) in the gathered ``aux_files`` and
    return the parsed payload, or None when none is present / parseable.

    Mirrors `_resolve_publiccode_payload` — Layer 1 of the citation
    integration: pure parse, no enrichment of the Repository's
    `schema:*` fields. Downstream consumers (LLM refiners, dashboards,
    a future `enrich_repo_from_citation_cff` stage) read the typed
    payload from `_citation_cff` instead of re-parsing YAML."""
    if not isinstance(aux_files, dict):
        return None
    for filename, content in aux_files.items():
        if not isinstance(filename, str) or not isinstance(content, str):
            continue
        if filename.lower() == "citation.cff":
            return parse_citation_cff(content)
    return None


def _resolve_aux_file_urls(
    aux_files: Any,
    *,
    full_name: str,
) -> dict[str, str | None]:
    """Return ``{field: url_or_None}`` for the supplementary repo-root
    files we expose as internal fields. URL form is canonical
    (`https://github.com/<owner>/<repo>/blob/HEAD/<filename>`), matching
    the project-wide "everything is a URL" convention. None is emitted
    when no case-insensitive match for that field's allowed filenames
    is in ``aux_files``.
    """
    if not isinstance(aux_files, dict) or not full_name:
        return {field: None for field, _ in _REPO_AUX_FILE_LOOKUPS}
    # `aux_files` keys come back from GitHub with their original
    # casing; build a once-only lookup so each field-level scan is O(1).
    lower_to_original = {
        str(name).lower(): name
        for name in aux_files
        if isinstance(name, str)
    }
    out: dict[str, str | None] = {}
    for field, candidates in _REPO_AUX_FILE_LOOKUPS:
        matched: str | None = None
        for candidate in candidates:
            original = lower_to_original.get(candidate)
            if original is not None:
                matched = original
                break
        out[field] = (
            f"https://github.com/{full_name}/blob/HEAD/{matched}"
            if matched
            else None
        )
    return out


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
            account_type = contributor.get("type")
            if isinstance(account_type, str) and account_type.lower() == "organization":
                continue
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


def _resolve_fork_parent_url(repository: dict[str, Any]) -> str | None:
    """Return the upstream repo URL when ``repository`` is a fork, else ``None``.

    GitHub's ``GET /repos/{owner}/{repo}`` payload carries two related
    nested fields for forks: ``parent`` (immediate upstream) and
    ``source`` (root ancestor of the network). For repos forked
    directly from an original both fields point at the same record;
    for chained forks they diverge. Prefer ``parent`` as it's the
    direct lineage edge — that's what downstream agents (article
    linkage, contribution attribution) want to walk first. Use the
    ``html_url`` form so the value matches every other repository IRI
    in the ontology (https://github.com/owner/repo).

    Returns ``None`` for non-forks and for forks whose upstream has
    been deleted (`parent` field absent or unusably sparse).
    """
    if not repository.get("fork"):
        return None
    parent = repository.get("parent")
    if isinstance(parent, dict):
        candidate = parent.get("html_url")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    source = repository.get("source")
    if isinstance(source, dict):
        candidate = source.get("html_url")
        if isinstance(candidate, str) and candidate.strip():
            return candidate.strip()
    return None


async def _maybe_await(value: Any) -> Any:
    if hasattr(value, "__await__"):
        return await value
    return value




# Keyword sets for `pulse:repositoryType` classification. Order matters: the
# first matching set wins, so the more-specific categories come first.
_REPO_TYPE_KEYWORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "pulse:EducationalResource",
        ("course", "tutorial", "training", "workshop", "exercise", "lesson"),
    ),
    (
        "pulse:Documentation",
        (
            "documentation",
            "best-practice-documentation",
            "best practice",
            "user guide",
            "handbook",
            "spec",
            "specification",
            "rfc",
        ),
    ),
    (
        "pulse:Data",
        ("dataset", "data-archive", "corpus", "benchmark"),
    ),
)
# Languages that, on their own, indicate a documentation-shaped repo.
_DOC_ONLY_LANGUAGES: frozenset[str] = frozenset(
    {"markdown", "rst", "text", "asciidoc", "tex", "html"},
)


def _classify_repository_type(*, language_names: list[str], haystack: str) -> str:
    """Pick a `pulse:repositoryType` value from language + name/description signals.

    Priority:
    1. Keyword match in repo name/description (most specific first).
    2. Language-only signal: if every detected language is a doc-shaped one,
       call it Documentation.
    3. Default: Software.
    """

    for repo_type, keywords in _REPO_TYPE_KEYWORDS:
        for keyword in keywords:
            if keyword in haystack:
                return repo_type
    if language_names and all(
        language in _DOC_ONLY_LANGUAGES for language in language_names
    ):
        return "pulse:Documentation"
    return "pulse:Software"


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

        aux_files: dict[str, str]
        readme_content: str = ""
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

            aux_files_candidate = repository_context.get("aux_files")
            aux_files = aux_files_candidate if isinstance(aux_files_candidate, dict) else {}

            readme_candidate = repository_context.get("readme_content")
            if isinstance(readme_candidate, str):
                readme_content = readme_candidate
        else:
            repository = providers.github.get_repository(full_name)
            contributors = providers.github.get_contributors(full_name)
            languages = providers.github.get_languages(full_name)
            aux_files = {}

        return {
            "full_name": full_name,
            "repository": repository,
            "contributors": contributors,
            "languages": languages,
            "aux_files": aux_files,
            "readme_content": readme_content,
        }

    async def _default_structured_output(  # noqa: C901
        self,
        context: dict[str, Any],
        _providers: ProviderSet,
        compiled_context: dict[str, Any],
    ) -> dict[str, Any]:
        repository = compiled_context["repository"]
        full_name = str(repository.get("full_name") or compiled_context["full_name"])
        readme_content: str = compiled_context.get("readme_content") or ""
        contributors = compiled_context.get("contributors", [])
        languages = compiled_context.get("languages", {})

        author_ids = []
        if isinstance(contributors, list):
            for contributor in contributors:
                if not isinstance(contributor, dict):
                    continue
                contributor_type = contributor.get("type")
                if isinstance(contributor_type, str) and contributor_type.lower() == "organization":
                    continue
                login = contributor.get("login")
                if isinstance(login, str) and login:
                    author_ids.append(login)
        if not author_ids:
            owner_login = repository.get("owner", {}).get("login")
            owner_type = repository.get("owner", {}).get("type")
            if (
                isinstance(owner_login, str)
                and owner_login
                and not (
                    isinstance(owner_type, str)
                    and owner_type.lower() == "organization"
                )
            ):
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
            uuid_value = generate_uuid()

        # v3.0.0: pulse:githubRepositoryHandle is the canonical
        # `https://github.com/<owner>/<repo>` URL; `pulse:ownedBy`
        # references the owning user/org via its canonical github URL.
        github_repo_url = github_repo_iri(full_name)
        owner_handle = repository.get("owner", {}).get("login")
        owner_url = github_user_iri(owner_handle) if owner_handle else None

        return {
            "id": github_repo_url or full_name,
            "type": "schema:SoftwareSourceCode",
            "shacl": "pulse:RepositoryShape",
            "identifiers": {
                "pulse:githubRepositoryHandle": github_repo_url,
                "schema:citation": doi_value,
                "uuid": uuid_value,
            },
            "idSource": "pulse:githubRepositoryHandle",
            "schema:name": repository.get("name") or full_name.split("/", maxsplit=1)[-1],
            "pulse:githubRepositoryHandle": github_repo_url,
            "schema:author": author_ids,
            "pulse:githubRepoStars": repository.get("stargazers_count"),
            "pulse:githubRepoForks": repository.get("forks_count"),
            "schema:dateCreated": _normalize_created_at(repository.get("created_at")),
            "schema:license": _normalize_license_url(
                repository.get("license", {}).get("spdx_id"),
            ),
            "schema:citation": f"https://doi.org/{doi_value}" if doi_value else None,
            "schema:programmingLanguage": programming_languages,
            "pulse:ownedBy": owner_url or owner_handle,
            "pulse:isForkOf": _resolve_fork_parent_url(repository),
            # Internal-only fields (`_` prefix is stripped before the
            # SHACL gate and JSON-LD output by default; surfaced when
            # the caller passes `?include_internal_fields=true`). They
            # preserve signal the v2.1.2 ontology can't express
            # (`pulse:RepositoryShape` is `sh:closed true`), so
            # downstream consumers — the LLM hybrid refiner especially
            # — get the full GitHub + gimie metadata for context
            # without violating the ontology contract. When the
            # ontology adds these paths we can promote them to
            # canonical SHACL fields in one place.
            "_description": repository.get("description") or None,
            "_keywords": [
                t for t in (repository.get("topics") or []) if isinstance(t, str) and t
            ] or None,
            "_homepage": repository.get("homepage") or None,
            "_default_branch": repository.get("default_branch") or None,
            "_primary_language": repository.get("language") or None,
            "_size_kb": repository.get("size"),
            "_archived": repository.get("archived"),
            "_disabled": repository.get("disabled"),
            "_pushed_at": repository.get("pushed_at"),
            "_updated_at": repository.get("updated_at"),
            "_open_issues_count": repository.get("open_issues_count"),
            "_watchers_count": repository.get("watchers_count"),
            "_subscribers_count": repository.get("subscribers_count"),
            "_network_count": repository.get("network_count"),
            "_has_wiki": repository.get("has_wiki"),
            "_has_pages": repository.get("has_pages"),
            "_has_discussions": repository.get("has_discussions"),
            "_has_issues": repository.get("has_issues"),
            "_has_projects": repository.get("has_projects"),
            "_license_name": (repository.get("license") or {}).get("name"),
            "_license_url": (repository.get("license") or {}).get("url"),
            "_avatar_url": (repository.get("owner") or {}).get("avatar_url"),
            "_visibility": repository.get("visibility"),
            # Pointers to supplementary metadata files at the repo root
            # (CITATION.cff, AUTHORS, CONTRIBUTING.md, publiccode.yml).
            # The provider already fetches the *contents* into the
            # `aux_files` slice of compiled_context for the LLM
            # refiners; we surface a URL pointer here so non-LLM
            # consumers (graph queries, dashboards) can link out to
            # the source-of-truth file even when they don't have the
            # content in hand. Each value is None when no case-
            # insensitive match for that field's filename is present.
            **_resolve_aux_file_urls(
                compiled_context.get("aux_files"),
                full_name=full_name,
            ),
            # Parsed publiccode.yml payload (when present). Keys are the
            # camelCase publiccode v0.4 field names — caller-side
            # consumers don't have to re-parse YAML. Future work
            # (separate PR): promote selected fields (license, repoOwner,
            # softwareType, developmentStatus) to first-class
            # ontology terms via a `publiccode:` JSON-LD prefix.
            "_publiccode": _resolve_publiccode_payload(
                compiled_context.get("aux_files"),
            ),
            # Parsed CITATION.cff payload (when present). Same Layer-1
            # contract as `_publiccode` — pure data, no automatic
            # enrichment of `schema:*` fields. A future stage
            # (`enrich_repo_from_citation_cff`) is the right place to
            # backfill `schema:citation` / `schema:dateCreated` /
            # `schema:author` from the parsed payload; that's an
            # opinionated policy (does CITATION.cff trump what an
            # agent emitted?) and lives separately.
            "_citation_cff": _resolve_citation_cff_payload(
                compiled_context.get("aux_files"),
            ),
            # Published releases (raw, newest-first) + GHCR container
            # (Docker) images. Layer-1 internal fields until a v3.0.0
            # enrichment stage promotes them to canonical `schema:`/`pulse:`
            # terms. `_latest_version` is the newest release's tag.
            "_releases": (repository.get("releases") or None),
            "_latest_version": (
                (repository.get("releases") or [{}])[0].get("tag_name")
                if isinstance(repository.get("releases"), list) and repository["releases"]
                else None
            ),
            # Flat, RDF-friendly release scalars for "Release Frequency".
            # The raw `_releases` list-of-objects collapses to empty blank
            # nodes on JSON-LD expansion (inner keys unmapped in @context),
            # so these single-value `gme-internal:release_count /
            # first_release_date / latest_release_date` triples are what
            # downstream consumers actually query. Always present (None when
            # no release data), like `_latest_version`.
            **{
                f"_{k}": v
                for k, v in summarize_releases(repository.get("releases")).items()
            },
            "_container_images": (repository.get("container_images") or None),
            # Flat, RDF-friendly package scalars for "Container distribution".
            # The raw `_container_images` list-of-objects collapses to empty
            # blank nodes on JSON-LD expansion (inner keys unmapped in
            # @context), so these single-value `gme-internal:package_count /
            # package_names / package_image_refs / package_tags /
            # latest_package_updated_at` triples are what consumers query.
            # Always present (None when no package data). GHCR scope only —
            # needs the `read:packages` token scope, else reads None.
            **{
                f"_{k}": v
                for k, v in summarize_packages(
                    repository.get("container_images"),
                ).items()
            },
            # Published npm / PyPI packages — discovered by context_gather
            # from the manifest name + public-registry back-reference, stored
            # as `npm_package` / `pypi_package`. Flat, RDF-friendly scalars
            # (`gme-internal:npm_* / pypi_*`): package / latest_version /
            # versions / latest_release_date / registry_url / link. Always
            # present (all None when no manifest / provider disabled / drop).
            **{
                f"_npm_{k}": v
                for k, v in summarize_registry_package(
                    repository.get("npm_package"),
                ).items()
            },
            **{
                f"_pypi_{k}": v
                for k, v in summarize_registry_package(
                    repository.get("pypi_package"),
                ).items()
            },
            # README badges — parsed from the raw README by context_gather
            # into a list of {label, image_url, link_url} records, stored as
            # repository_metadata["badges"]. `_badges` is the raw list (or
            # None); `_badge_count` is the count (or None when no badges).
            "_badges": (repository.get("badges") or None),
            "_badge_count": (
                len(repository["badges"])
                if isinstance(repository.get("badges"), list)
                else None
            ),
            # Published conda / crates.io / RubyGems packages — discovered by
            # context_gather from README badge coordinates (+ cargo.toml for
            # crates) and the public-registry back-reference, stored as
            # `conda_package` / `crates_package` / `rubygems_package`. Same
            # flat `gme-internal:*` scalars as `_npm_*` / `_pypi_*`. conda
            # also emits `_conda_channel` (the Anaconda channel). Always
            # present (all None when no badge / provider disabled / drop).
            **{
                f"_conda_{k}": v
                for k, v in summarize_registry_package(
                    repository.get("conda_package"),
                ).items()
            },
            "_conda_channel": (repository.get("conda_package") or {}).get("channel"),
            **{
                f"_crates_{k}": v
                for k, v in summarize_registry_package(
                    repository.get("crates_package"),
                ).items()
            },
            **{
                f"_rubygems_{k}": v
                for k, v in summarize_registry_package(
                    repository.get("rubygems_package"),
                ).items()
            },
            # Go module — discovered by context_gather from `go.mod`'s
            # `module` directive, queried against the Go module proxy, and
            # verified by the module path back-referencing this repo. Same
            # flat scalars as the others; `_go_package` carries the module
            # path (e.g. github.com/owner/repo[/v2]).
            **{
                f"_go_{k}": v
                for k, v in summarize_registry_package(
                    repository.get("go_module"),
                ).items()
            },
            # Maven Central artifact — discovered from `pom.xml`
            # (`groupId:artifactId`) or a maven-central badge. `_maven_package`
            # is the `group:artifact` coordinate; `_maven_group_id` /
            # `_maven_artifact_id` carry the split coordinates.
            **{
                f"_maven_{k}": v
                for k, v in summarize_registry_package(
                    repository.get("maven_package"),
                ).items()
            },
            "_maven_group_id": (repository.get("maven_package") or {}).get("group_id"),
            "_maven_artifact_id": (
                (repository.get("maven_package") or {}).get("artifact_id")
            ),
            # CI presence — detected from the repo root listing by
            # context_gather and stored in repository_metadata["has_ci"].
            # None when the listing was unavailable.
            "_has_ci": repository.get("has_ci"),
            # Test-coverage percentage parsed from the README (regex only;
            # LLM fallback is a separate later phase). None when not found.
            "_test_coverage": parse_test_coverage(readme_content),
            # Docker Hub URL parsed from README + aux-files. None when
            # no confident match is found.
            "_docker_hub_url": parse_docker_hub_url(
                readme_content,
                compiled_context.get("aux_files"),
            ),
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

        repository = compiled_context.get("repository") or {}
        repo_handle = compiled_context.get("full_name") or ""
        repo_name = (
            (repository.get("name") if isinstance(repository, dict) else None)
            or repo_handle.split("/", maxsplit=1)[-1]
            or ""
        )
        repo_description = (
            repository.get("description") if isinstance(repository, dict) else None
        ) or ""
        # Lowercased haystack we'll match keyword heuristics against.
        haystack = f"{repo_name} {repo_description}".lower()

        repository_type = _classify_repository_type(
            language_names=language_names,
            haystack=haystack,
        )

        disciplines = _to_list_of_strings(context.get("disciplines"))
        # Empty list is now the honest default — the SHACL
        # `pulse:DisciplineShape` has no `sh:minCount`, so `[]` validates.
        # The previous catch-all (`wd:Q428691`, computer engineering /
        # "software") was hiding the absence of a real domain signal:
        # in a 441-repo production batch 77% landed with the catch-all
        # ONLY, drowning honest per-domain aggregation.

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
        # Derivation breadcrumbs carry the bare `owner/repo` form for
        # human readability; the canonical URL lives on the property
        # `pulse:githubRepositoryHandle`.
        from src.v2.canonicalization.github import parse_github_repo_iri

        canonical_repo_url = validated_payload.get("pulse:githubRepositoryHandle")
        if isinstance(canonical_repo_url, str):
            parts = parse_github_repo_iri(canonical_repo_url)
            bare_repo_full_name = (
                f"{parts[0]}/{parts[1]}" if parts else canonical_repo_url
            )
        else:
            bare_repo_full_name = None

        derivation_stats = {
            "repository_full_name": bare_repo_full_name,
            "source_repositories": [bare_repo_full_name]
            if isinstance(bare_repo_full_name, str)
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
