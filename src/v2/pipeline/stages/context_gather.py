from __future__ import annotations

import logging
import os
import re
from typing import TYPE_CHECKING, Any

from src.v2.agents.rule_based._repo_signals import (
    detect_has_ci,
    extract_registry_coords,
    parse_badges,
    parse_crates_name,
    parse_go_module,
    parse_npm_name,
    parse_pypi_name,
    repo_url_matches,
)
from src.v2.pipeline.stages.models import ContextBundle


def should_expand_owned_repos() -> bool:
    """Whether user/organization flows run the full per-repo pipeline
    (gimie context gather + materialisation) on each owned repo.

    When False (default), owned repos are kept only as `pulse:owns`
    `@id` references: context gather skips the per-repo gimie pass and
    the orchestrator skips materialisation. An org or prolific user can
    own 50-200 repos and gimie on each costs minutes of wall time. Set
    `V2_EXPAND_OWNED_REPOS=true` to restore eager expansion.

    Single source of truth for the flag — the orchestrator imports this
    so context gather and materialisation never disagree.
    """
    raw = (os.getenv("V2_EXPAND_OWNED_REPOS") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


# `_clean_readme_for_llm`: a *light* cleaner that removes only the
# high-noise / low-signal portions of a GitHub README so downstream LLM
# prompts + RAG queries don't waste tokens on badge/image/HTML soup.
#
# Unlike `concept_tagging._strip_markdown` (which removes headers,
# lists, code fences, etc. for pure-text embedding), this cleaner KEEPS
# markdown structure that a model can use to understand the document:
# headers, bullet lists, code blocks, bold/italic. It only strips:
#
#   - HTML tags entirely (drops `<img>`, `<div>`, `<center>`, `<a>` etc.)
#   - Markdown images `![alt](url)` (badges, logo banners)
#   - Markdown badge-link constructs `[![alt](badge)](link)` collapsed
#   - Bare URLs that are sitting on their own line (badge URLs)
#
# Observed savings on `deeplabcut/deeplabcut`: README first 1000 chars
# go from "~95% `<img>` markup" → "actual prose". Helps every LLM
# agent that injects `readme_content` into its prompt.
_HTML_TAG_RE = re.compile(r"<[^>]+>", re.DOTALL)
_MD_BADGE_LINK_RE = re.compile(r"\[!\[[^\]]*\]\([^)]*\)\]\([^)]*\)")
_MD_IMAGE_RE = re.compile(r"!\[[^\]]*\]\([^)]*\)")
_LONE_URL_LINE_RE = re.compile(r"^\s*https?://\S+\s*$", re.MULTILINE)
_BLANK_LINE_RUN_RE = re.compile(r"\n{3,}")


def _clean_readme_for_llm(text: str | None) -> str:
    """Strip noisy badge/image/HTML markup but keep markdown structure.

    Idempotent — running on already-cleaned text is a near no-op (the
    second pass finds nothing to remove).
    """
    if not isinstance(text, str) or not text:
        return text or ""
    cleaned = _MD_BADGE_LINK_RE.sub("", text)
    cleaned = _MD_IMAGE_RE.sub("", cleaned)
    cleaned = _HTML_TAG_RE.sub("", cleaned)
    cleaned = _LONE_URL_LINE_RE.sub("", cleaned)
    # Collapse the cascade of blank lines that the removals leave behind.
    cleaned = _BLANK_LINE_RUN_RE.sub("\n\n", cleaned)
    return cleaned.strip()

if TYPE_CHECKING:
    from src.v2.agents.models import ProviderSet
    from src.v2.ingest.detection.models import GitHubURLClassification

logger = logging.getLogger(__name__)

_BOOKENDS_TOP_N_DEFAULT = 50


def _resolve_bookends_top_n() -> int:
    """How many contributors get a `get_commit_bookends` lookup.

    GitHub returns `/repos/.../contributors` ordered by commit count
    desc, so capping to the top N keeps the heaviest contributors and
    skips the long tail. Each contributor costs up to 2 GitHub API
    calls (cached for `V2_PROVIDER_CACHE_TTL_DAYS`); without the cap a
    repo with 4 000 contributors burns through the 5 000/h auth quota
    on a single extraction.

    `0` (or negative) disables the enrichment entirely.
    """
    raw = os.getenv("V2_CONTRIBUTOR_BOOKENDS_TOP_N")
    if raw is None or raw.strip() == "":
        return _BOOKENDS_TOP_N_DEFAULT
    try:
        return int(raw)
    except ValueError:
        logger.warning(
            "Invalid V2_CONTRIBUTOR_BOOKENDS_TOP_N=%r; falling back to %d",
            raw,
            _BOOKENDS_TOP_N_DEFAULT,
        )
        return _BOOKENDS_TOP_N_DEFAULT


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


def _enrich_contributors_with_commit_bookends(
    full_name: str,
    contributors: list[dict[str, Any]],
    providers: ProviderSet,
) -> str | None:
    """Populate `firstContributionDate` / `lastContributionDate` /
    `contributions` on each contributor in place.

    Uses ``GitHubProvider.get_commit_bookends`` (cached, two API calls
    per contributor). Failures degrade silently — the contribution
    agent simply sees `None` for those fields.

    Capped to the top N contributors (`V2_CONTRIBUTOR_BOOKENDS_TOP_N`,
    default 50) to keep jumbo repos within the GitHub auth quota. The
    upstream contributor list is already ordered by commit count desc,
    so the top N captures the heaviest contributors. Returns a warning
    string when the cap dropped at least one contributor, otherwise
    `None`.
    """
    if not contributors or providers.github is None:
        return None
    fetcher = getattr(providers.github, "get_commit_bookends", None)
    if not callable(fetcher):
        return None

    top_n = _resolve_bookends_top_n()
    if top_n <= 0:
        return None

    targets = contributors[:top_n]
    for contributor in targets:
        if not isinstance(contributor, dict):
            continue
        login = contributor.get("login")
        if not isinstance(login, str) or not login:
            continue
        try:
            bookends = fetcher(full_name, login)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(bookends, dict):
            continue
        first_date = bookends.get("first_date")
        last_date = bookends.get("last_date")
        count = bookends.get("count")
        if isinstance(first_date, str) and not contributor.get("firstContributionDate"):
            contributor["firstContributionDate"] = first_date
        if isinstance(last_date, str) and not contributor.get("lastContributionDate"):
            contributor["lastContributionDate"] = last_date
        if isinstance(count, int) and count > 0 and not contributor.get("contributions"):
            contributor["contributions"] = count

    skipped = max(0, len(contributors) - top_n)
    if skipped > 0:
        return (
            f"Commit-bookend enrichment capped at top {top_n} contributors "
            f"for {full_name}; {skipped} tail contributor(s) left without "
            "first/last/contributions dates "
            "(adjust V2_CONTRIBUTOR_BOOKENDS_TOP_N to widen)."
        )
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


def _enrich_repository_metadata_with_registry_packages(  # noqa: C901, PLR0912, PLR0913, PLR0915
    *,
    full_name: str,
    aux_files: dict[str, Any] | None,
    repository_metadata: dict[str, Any],
    providers: ProviderSet,
    warnings: list[str],
    readme: str | None = None,
) -> None:
    """Discover the repo's published packages and store them on
    *repository_metadata* in place.

    Parses README badges into ``repository_metadata["badges"]`` (when any),
    then discovers packages across npm / PyPI / conda / crates.io / RubyGems:

    * npm / PyPI: manifest name (``package.json`` / ``pyproject.toml`` /
      ``setup.cfg``), falling back to badge coordinates when no manifest name.
    * conda: badge coordinates only (no repo manifest).
    * crates.io: ``cargo.toml`` ``[package].name``, else badge coordinates.
    * RubyGems: badge coordinates only (no gemspec is fetched).

    Stored as ``{npm,pypi,conda,crates,rubygems}_package``.

    Gated on ``providers.package_registry`` being available; best-effort
    (failures append a warning and never break the pipeline). Link policy
    per package:

    * registry's ``repository_url`` present AND back-references this repo →
      store with ``link="verified"``.
    * registry's ``repository_url`` present but points elsewhere → DROP
      (almost certainly a name collision with a different project's package).
    * registry's ``repository_url`` absent → store with ``link="name_only"``
      (name match only; weaker but still useful signal).

    Badge parsing runs even when ``package_registry`` is None (badges are a
    README-derived signal, not a registry call).
    """
    badges = parse_badges(readme)
    if badges:
        repository_metadata["badges"] = badges
    coords = extract_registry_coords(badges)

    registry = getattr(providers, "package_registry", None)
    if registry is None:
        return

    def _link_and_store(pkg: dict[str, Any] | None, *, key: str) -> None:
        if not isinstance(pkg, dict):
            return
        repository_url = pkg.get("repository_url")
        if isinstance(repository_url, str) and repository_url.strip():
            if repo_url_matches(repository_url, full_name):
                pkg["link"] = "verified"
            else:
                # Back-reference points at a different repo — name collision.
                return
        else:
            pkg["link"] = "name_only"
        repository_metadata[key] = pkg

    try:
        npm_name = parse_npm_name(aux_files) or coords.get("npm")
        if npm_name:
            _link_and_store(registry.get_npm_package(npm_name), key="npm_package")
    except Exception as exc:  # noqa: BLE001
        warnings.append(
            f"npm package lookup failed for {full_name}: {exc}",
        )
    try:
        pypi_name = parse_pypi_name(aux_files) or coords.get("pypi")
        if pypi_name:
            _link_and_store(registry.get_pypi_package(pypi_name), key="pypi_package")
    except Exception as exc:  # noqa: BLE001
        warnings.append(
            f"PyPI package lookup failed for {full_name}: {exc}",
        )
    try:
        conda_coord = coords.get("conda")
        if conda_coord:
            channel, conda_name = conda_coord
            _link_and_store(
                registry.get_conda_package(channel, conda_name),
                key="conda_package",
            )
    except Exception as exc:  # noqa: BLE001
        warnings.append(
            f"conda package lookup failed for {full_name}: {exc}",
        )
    try:
        crates_name = parse_crates_name(aux_files) or coords.get("crates")
        if crates_name:
            _link_and_store(
                registry.get_crates_package(crates_name), key="crates_package",
            )
    except Exception as exc:  # noqa: BLE001
        warnings.append(
            f"crates package lookup failed for {full_name}: {exc}",
        )
    try:
        rubygems_name = coords.get("rubygems")
        if rubygems_name:
            _link_and_store(
                registry.get_rubygems_package(rubygems_name),
                key="rubygems_package",
            )
    except Exception as exc:  # noqa: BLE001
        warnings.append(
            f"RubyGems package lookup failed for {full_name}: {exc}",
        )
    try:
        go_module = parse_go_module(aux_files)
        if go_module and hasattr(registry, "get_go_module"):
            _link_and_store(
                registry.get_go_module(go_module), key="go_module",
            )
    except Exception as exc:  # noqa: BLE001
        warnings.append(
            f"Go module lookup failed for {full_name}: {exc}",
        )


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
    else:
        cap_warning = _enrich_contributors_with_commit_bookends(
            full_name, contributors, providers,
        )
        if cap_warning:
            warnings.append(cap_warning)

    try:
        languages = providers.github.get_languages(full_name)
    except Exception as exc:  # noqa: BLE001
        warnings.append(
            f"Repository languages lookup failed for {full_name}: {exc}",
        )
        languages = {}

    # Prefer the dedicated REST README fetch; the gimie JSON-LD that
    # populates `repository_metadata` does NOT include the README body,
    # only `schema:description` (the repo's short tagline). Without this
    # call the LLM refiners were running on a ~138-byte tagline thinking
    # it was the README. Fall back to legacy metadata keys / description
    # only when the dedicated fetch comes back empty.
    readme_from_provider = ""
    try:
        readme_from_provider = providers.github.get_repository_readme(full_name)
    except Exception as exc:  # noqa: BLE001
        warnings.append(
            f"Repository README fetch failed for {full_name}: {exc}",
        )
    readme_content = _first_non_empty_string(
        readme_from_provider,
        repository_metadata.get("readme"),
        repository_metadata.get("readme_content"),
        repository_metadata.get("README"),
        repository_metadata.get("description"),
    )
    if not readme_content:
        warnings.append(f"Repository README content is not available for {full_name}")
        readme_content = ""
    else:
        # Light clean once at the source so every downstream consumer
        # (5 LLM agents + the RAG discipline tagger + concept_tagging)
        # gets noise-free README without each call site having to
        # re-strip. Keeps headers/lists/code intact.
        readme_content = _clean_readme_for_llm(readme_content)

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

    # Auxiliary attribution / governance files at the repo root.
    # `get_repository_aux_files` lists the root directory once, then
    # raw-fetches only the curated filenames that exist. Gives the LLM
    # refiners (rescue + discovery) signal the README alone doesn't
    # carry — AUTHORS, NOTICE.yml, CITATION.cff, pyproject.toml etc.
    aux_files: dict[str, str] = {}
    try:
        aux_files = providers.github.get_repository_aux_files(full_name)
    except Exception as exc:  # noqa: BLE001
        warnings.append(
            f"Repository aux-files lookup failed for {full_name}: {exc}",
        )

    # Releases + published container (Docker) images. Both ride along in
    # `metadata` so the repository agent can surface them as `_releases`
    # / `_container_images` internal fields (the ontology has no
    # predicate for them yet). Best-effort, like aux_files: container
    # images need the `read:packages` scope and degrade to [] without it.
    try:
        releases = providers.github.get_repository_releases(full_name)
        if isinstance(releases, list) and releases:
            repository_metadata["releases"] = releases
    except Exception as exc:  # noqa: BLE001
        warnings.append(
            f"Repository releases lookup failed for {full_name}: {exc}",
        )
    try:
        container_images = providers.github.get_repository_container_images(full_name)
        if isinstance(container_images, list) and container_images:
            repository_metadata["container_images"] = container_images
    except Exception as exc:  # noqa: BLE001
        warnings.append(
            f"Repository container-images lookup failed for {full_name}: {exc}",
        )

    # CI detection — list the repo root once and check for known CI
    # indicator files / directories. Best-effort: None on failure so the
    # pipeline never breaks over a missing listing.
    try:
        root_entries = providers.github.get_repository_root_entries(full_name)
        repository_metadata["has_ci"] = detect_has_ci(root_entries)
    except Exception as exc:  # noqa: BLE001
        warnings.append(
            f"Repository root-listing (has_ci) failed for {full_name}: {exc}",
        )

    # Published packages (npm / PyPI / conda / crates / RubyGems) + README
    # badges — discovered from the manifest name / badge coordinates +
    # public-registry back-reference. Best-effort. Badge parsing needs the
    # RAW README (the cleaned `readme_content` has had badges stripped).
    _enrich_repository_metadata_with_registry_packages(
        full_name=full_name,
        aux_files=aux_files,
        repository_metadata=repository_metadata,
        providers=providers,
        warnings=warnings,
        readme=readme_from_provider,
    )

    return {
        "full_name": full_name,
        "metadata": repository_metadata,
        "readme_content": readme_content,
        "contributors": contributors,
        "languages": languages,
        "gimie_jsonld": gimie_jsonld,
        "aux_files": aux_files,
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

        cap_warning = _enrich_contributors_with_commit_bookends(
            full_name, contributors, providers,
        )
        if cap_warning:
            warnings.append(cap_warning)

        try:
            languages = providers.github.get_languages(full_name)
        except Exception as exc:  # noqa: BLE001
            raise RequiredProviderUnavailableError(
                provider="github",
                operation="repository languages lookup",
                cause=exc,
            ) from exc

        readme_from_provider = ""
        try:
            readme_from_provider = providers.github.get_repository_readme(full_name)
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                f"Repository README fetch failed for {full_name}: {exc}",
            )
        readme_content = _first_non_empty_string(
            readme_from_provider,
            repository_metadata.get("readme"),
            repository_metadata.get("readme_content"),
            repository_metadata.get("README"),
            repository_metadata.get("description"),
        )
        if not readme_content:
            warnings.append("Repository README content is not available")
            readme_content = ""
        else:
            readme_content = _clean_readme_for_llm(readme_content)

        gimie_jsonld = providers.github.get_repository_jsonld(full_name)

        try:
            aux_files = providers.github.get_repository_aux_files(full_name)
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                f"Repository aux-files lookup failed for {full_name}: {exc}",
            )
            aux_files = {}

        # Releases + container images (best-effort; same as
        # _optional_repository_context above).
        try:
            releases = providers.github.get_repository_releases(full_name)
            if isinstance(releases, list) and releases:
                repository_metadata["releases"] = releases
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                f"Repository releases lookup failed for {full_name}: {exc}",
            )
        try:
            container_images = providers.github.get_repository_container_images(full_name)
            if isinstance(container_images, list) and container_images:
                repository_metadata["container_images"] = container_images
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                f"Repository container-images lookup failed for {full_name}: {exc}",
            )

        # CI detection (best-effort; None on provider failure).
        try:
            root_entries = providers.github.get_repository_root_entries(full_name)
            repository_metadata["has_ci"] = detect_has_ci(root_entries)
        except Exception as exc:  # noqa: BLE001
            warnings.append(
                f"Repository root-listing (has_ci) failed for {full_name}: {exc}",
            )

        # Published packages + README badges (best-effort; same as
        # _optional_repository_context above). Pass the RAW README so badge
        # parsing sees the un-stripped `![..](..)` constructs.
        _enrich_repository_metadata_with_registry_packages(
            full_name=full_name,
            aux_files=aux_files,
            repository_metadata=repository_metadata,
            providers=providers,
            warnings=warnings,
            readme=readme_from_provider,
        )

        context["repository"] = {
            "full_name": full_name,
            "metadata": repository_metadata,
            "readme_content": readme_content,
            "contributors": contributors,
            "languages": languages,
            "gimie_jsonld": gimie_jsonld,
            "aux_files": aux_files,
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
        # Per-repo gimie context is gated by V2_EXPAND_OWNED_REPOS. When
        # off (default) the owned repos survive only as `pulse:owns`
        # references — skipping the gimie pass here is what actually
        # avoids the 30+ sequential sub-extractions on a prolific user.
        repository_contexts: dict[str, dict[str, Any]] = {}
        if should_expand_owned_repos():
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
        # Gated by V2_EXPAND_OWNED_REPOS — see the user branch above.
        repository_contexts: dict[str, dict[str, Any]] = {}
        if should_expand_owned_repos():
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
