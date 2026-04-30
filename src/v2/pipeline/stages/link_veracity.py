from __future__ import annotations

import asyncio
import re
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from src.v2.agents import LLMLinkVeracityAgentV2, ProviderSet
from src.v2.canonicalization.id_resolution import (
    resolve_article_id,
    resolve_organization_id,
    resolve_person_id,
    resolve_repository_id,
)
from src.v2.ingest.cache import ProviderCache
from src.v2.pipeline.stages.models import AssembledOutput

HTTP_SCHEMES = {"http", "https"}
IDENTITY_KEYS = {"id", "@id", "type", "@type"}
_NESTED_URL_AFTER_UNDERSCORE = re.compile(r"_https?://", flags=re.IGNORECASE)

_ID_RESOLVER_BY_TYPE: dict[str, Any] = {
    "schema:Person": resolve_person_id,
    "org:Organization": resolve_organization_id,
    "schema:SoftwareSourceCode": resolve_repository_id,
    "schema:ScholarlyArticle": resolve_article_id,
}

# When promoting past a failed id source, also clear the field that produced it
# so the priority ladder picks the next rung instead of the same one.
_ID_SOURCE_TO_FIELD_KEYS: dict[str, tuple[str, ...]] = {
    "pulse:orcid": ("pulse:orcid", "pulse:orcidIdentifier", "orcid", "orcidIdentifier"),
    "pulse:infosciencePersonIdentifier": (
        "pulse:infosciencePersonIdentifier",
        "infosciencePersonIdentifier",
    ),
    "pulse:githubUsername": ("pulse:githubUsername", "githubUsername"),
    "pulse:ror": ("pulse:ror", "ror"),
    "pulse:infoscienceOrganizationIdentifier": (
        "pulse:infoscienceOrganizationIdentifier",
        "infoscienceOrganizationIdentifier",
    ),
    "pulse:githubOrganizationHandle": (
        "pulse:githubOrganizationHandle",
        "githubOrganizationHandle",
    ),
    "pulse:githubRepositoryHandle": (
        "pulse:githubRepositoryHandle",
        "githubRepositoryHandle",
    ),
    "schema:identifier": ("schema:identifier", "doi"),
    "schema:citation": ("schema:citation",),
    "pulse:infoscienceArticleIdentifier": (
        "pulse:infoscienceArticleIdentifier",
        "infoscienceArticleIdentifier",
    ),
}
DOI_BASE_URI = "https://doi.org/"
DOI_PREFIXES = (
    "https://doi.org/",
    "http://doi.org/",
    "https://dx.doi.org/",
    "http://dx.doi.org/",
)
ENTITY_TYPE_TO_SINGULAR = {
    "schema:SoftwareSourceCode": "repository",
    "schema:Person": "person",
    "org:Organization": "organization",
    "schema:ScholarlyArticle": "article",
    "org:Membership": "membership",
    "pulse:Contribution": "contribution",
}
_DROP = object()

# Predicates whose evidence lives on the SOURCE side, not the link target.
# Fetching the link target and asking "does this page support
# `<source> <predicate> <link>`?" produces false negatives because the target
# page never mentions the source. We skip these to avoid wasted Selenium
# fetches + LLM calls and noisy warnings.
_ASYMMETRIC_PREDICATES: frozenset[str] = frozenset(
    {
        "schema:author",
        "schema:contributor",
        "schema:memberOf",
        "pulse:owns",
        "pulse:contributionTo",
        "pulse:firstContributionDate",
        "pulse:lastContributionDate",
        "org:hasUnit",
        "org:unitOf",
        "org:organization",
        "org:role",
        "time:hasBeginning",
        "time:hasEnd",
    },
)


def _is_supportable_predicate(predicate: Any) -> bool:
    """Predicates whose evidence plausibly lives on the link target's page."""

    if not isinstance(predicate, str) or not predicate:
        return False
    return predicate not in _ASYMMETRIC_PREDICATES


@dataclass(slots=True)
class LinkVeracityStageResult:
    records: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked_count: int = 0
    supported_count: int = 0
    unsupported_count: int = 0
    failed_count: int = 0
    invalid_links: list[str] = field(default_factory=list)
    entity_link_map: dict[str, list[str]] = field(default_factory=dict)
    article_identifier_link_map: dict[str, str] = field(default_factory=dict)


def _is_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if not candidate:
        return False
    parsed = urlparse(candidate)
    if parsed.scheme not in HTTP_SCHEMES or not parsed.netloc:
        return False
    if _NESTED_URL_AFTER_UNDERSCORE.search(candidate):
        return False
    return True


def _normalize_doi(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    candidate = value.strip()
    if not candidate:
        return None

    lowered = candidate.lower()
    for prefix in DOI_PREFIXES:
        if lowered.startswith(prefix):
            candidate = candidate[len(prefix) :]
            break

    if not candidate or not candidate.startswith("10.") or "/" not in candidate:
        return None
    return candidate


def collect_unique_http_link_contexts(
    jsonld_payload: dict[str, Any],
) -> list[dict[str, Any]]:
    graph = jsonld_payload.get("@graph")
    if not isinstance(graph, list):
        return []

    link_contexts: dict[str, dict[str, Any]] = {}

    def _record_link(
        *,
        link: str,
        source_entity_id: str | None,
        predicate: str | None,
    ) -> None:
        normalized_link = link.strip()
        if not normalized_link or not _is_http_url(normalized_link):
            return
        if not _is_supportable_predicate(predicate):
            return
        if isinstance(source_entity_id, str) and source_entity_id == normalized_link:
            # Self-reference: identifying URL recorded against the entity it identifies.
            return

        context = link_contexts.setdefault(
            normalized_link,
            {
                "link": normalized_link,
                "source_entity_id": source_entity_id,
                "predicate": predicate,
                "relationships": [],
            },
        )
        relationship = {
            "source_entity_id": source_entity_id,
            "predicate": predicate,
        }
        if relationship not in context["relationships"]:
            context["relationships"].append(relationship)

    def _walk(
        value: Any,
        *,
        source_entity_id: str | None,
        predicate: str | None,
    ) -> None:
        if isinstance(value, str):
            _record_link(
                link=value,
                source_entity_id=source_entity_id,
                predicate=predicate,
            )
            return

        if isinstance(value, dict):
            at_id = value.get("@id")
            if isinstance(at_id, str) and predicate is not None:
                _record_link(
                    link=at_id,
                    source_entity_id=source_entity_id,
                    predicate=predicate,
                )
                return
            for key, nested in value.items():
                if key == "@id":
                    continue
                _walk(
                    nested,
                    source_entity_id=source_entity_id,
                    predicate=key,
                )
            return

        if isinstance(value, list):
            for nested in value:
                _walk(
                    nested,
                    source_entity_id=source_entity_id,
                    predicate=predicate,
                )

    for node in graph:
        if not isinstance(node, dict):
            continue
        source_entity_id = node.get("@id") if isinstance(node.get("@id"), str) else None
        for key, value in node.items():
            if key == "@id":
                continue
            _walk(
                value,
                source_entity_id=source_entity_id,
                predicate=key,
            )

    return [link_contexts[link] for link in sorted(link_contexts)]


def _scan_entity_links(
    entities: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], dict[str, set[str]], dict[str, str]]:
    link_contexts: dict[str, dict[str, Any]] = {}
    entity_link_map: dict[str, set[str]] = {}
    article_identifier_link_map: dict[str, str] = {}

    def _record_link(
        *,
        link: str,
        source_entity_id: str,
        predicate: str | None,
    ) -> None:
        normalized_link = link.strip()
        if not normalized_link or not _is_http_url(normalized_link):
            return

        entity_link_map.setdefault(source_entity_id, set()).add(normalized_link)

        if not _is_supportable_predicate(predicate):
            return
        if source_entity_id == normalized_link:
            # Self-reference: identifying URL recorded against the entity it identifies.
            return

        context = link_contexts.setdefault(
            normalized_link,
            {
                "link": normalized_link,
                "source_entity_id": source_entity_id,
                "predicate": predicate,
                "relationships": [],
            },
        )
        relationship = {
            "source_entity_id": source_entity_id,
            "predicate": predicate,
        }
        if relationship not in context["relationships"]:
            context["relationships"].append(relationship)

    def _walk(
        value: Any,
        *,
        source_entity_id: str,
        predicate: str | None,
    ) -> None:
        if isinstance(value, str):
            _record_link(
                link=value,
                source_entity_id=source_entity_id,
                predicate=predicate,
            )
            return
        if isinstance(value, list):
            for nested in value:
                _walk(
                    nested,
                    source_entity_id=source_entity_id,
                    predicate=predicate,
                )
            return
        if isinstance(value, dict):
            at_id = value.get("@id")
            if isinstance(at_id, str) and predicate is not None:
                _record_link(
                    link=at_id,
                    source_entity_id=source_entity_id,
                    predicate=predicate,
                )
                return
            for key, nested in value.items():
                if key in IDENTITY_KEYS:
                    continue
                _walk(
                    nested,
                    source_entity_id=source_entity_id,
                    predicate=key,
                )

    for index, entity in enumerate(entities):
        if not isinstance(entity, dict):
            continue
        raw_entity_id = entity.get("id")
        if isinstance(raw_entity_id, str) and raw_entity_id:
            source_entity_id = raw_entity_id
        else:
            source_entity_id = f"urn:pulse:entity-{index}"
        entity_link_map.setdefault(source_entity_id, set())

        entity_type = entity.get("type")
        if entity_type == "schema:ScholarlyArticle":
            raw_identifier = entity.get("schema:identifier")
            if not isinstance(raw_identifier, str):
                identifiers = entity.get("identifiers")
                if isinstance(identifiers, dict):
                    nested_identifier = identifiers.get("schema:identifier")
                    if isinstance(nested_identifier, str):
                        raw_identifier = nested_identifier
            normalized_doi = _normalize_doi(raw_identifier)
            if normalized_doi is not None:
                doi_link = (
                    raw_identifier.strip()
                    if isinstance(raw_identifier, str) and _is_http_url(raw_identifier)
                    else f"{DOI_BASE_URI}{normalized_doi}"
                )
                article_identifier_link_map[source_entity_id] = doi_link
                _record_link(
                    link=doi_link,
                    source_entity_id=source_entity_id,
                    predicate="schema:identifier",
                )

        for key, value in entity.items():
            if key in IDENTITY_KEYS:
                continue
            _walk(
                value,
                source_entity_id=source_entity_id,
                predicate=key,
            )

    return (
        [link_contexts[link] for link in sorted(link_contexts)],
        entity_link_map,
        article_identifier_link_map,
    )


def _clean_invalid_http_values(value: Any, invalid_links: set[str]) -> Any:
    if isinstance(value, str):
        candidate = value.strip()
        if _is_http_url(candidate) and candidate in invalid_links:
            return _DROP
        return value
    if isinstance(value, list):
        cleaned: list[Any] = []
        for item in value:
            cleaned_item = _clean_invalid_http_values(item, invalid_links)
            if cleaned_item is _DROP:
                continue
            cleaned.append(cleaned_item)
        return cleaned
    if isinstance(value, dict):
        cleaned_dict: dict[str, Any] = {}
        for key, item in value.items():
            cleaned_item = _clean_invalid_http_values(item, invalid_links)
            if cleaned_item is _DROP:
                cleaned_dict[key] = None
                continue
            cleaned_dict[key] = cleaned_item
        return cleaned_dict
    return value


def _entity_type_singular(entity: dict[str, Any]) -> str:
    entity_type = entity.get("type")
    if isinstance(entity_type, str):
        return ENTITY_TYPE_TO_SINGULAR.get(entity_type, "entity")
    return "entity"


def _apply_id_rewrites(value: Any, rewrites: dict[str, str]) -> Any:
    """Replace every occurrence of a rewritten old id anywhere in `value`."""
    if isinstance(value, str):
        return rewrites.get(value, value)
    if isinstance(value, list):
        return [_apply_id_rewrites(item, rewrites) for item in value]
    if isinstance(value, dict):
        return {key: _apply_id_rewrites(item, rewrites) for key, item in value.items()}
    return value


def _entity_with_demoted_source(
    entity: dict[str, Any],
    failing_source: str | None,
) -> dict[str, Any]:
    """Return a copy of `entity` with the failing identifier and id stripped."""
    demoted = dict(entity)
    demoted["id"] = None
    demoted["idSource"] = None
    if isinstance(failing_source, str):
        for field in _ID_SOURCE_TO_FIELD_KEYS.get(failing_source, ()):
            if field in demoted:
                demoted[field] = None
        identifiers = demoted.get("identifiers")
        if isinstance(identifiers, dict):
            cloned_identifiers = dict(identifiers)
            for field in _ID_SOURCE_TO_FIELD_KEYS.get(failing_source, ()):
                if field in cloned_identifiers:
                    cloned_identifiers[field] = None
            demoted["identifiers"] = cloned_identifiers
    return demoted


def promote_failed_id_entities(
    *,
    assembled: AssembledOutput,
    invalid_links: set[str],
) -> tuple[AssembledOutput, dict[str, str], list[str]]:
    """Re-resolve entity ids whose URL failed link veracity.

    For each Person/Org/Repo/Article entity whose `id` is in `invalid_links`,
    strip the failing identifier source and re-run the priority resolver. If
    the new id differs, record the rewrite and propagate it across every
    `@id`/`id` reference and every string-id occurrence in the assembled
    output.

    Composite entities (Membership, Contribution) have no resolver entry and
    are left unchanged.

    Returns the (possibly mutated) assembled output, the `old_id -> new_id`
    rewrite map, and human-readable warnings to surface on the response.
    """
    if not invalid_links:
        return assembled, {}, []

    rewrites: dict[str, str] = {}
    warnings: list[str] = []
    candidates: list[dict[str, Any]] = []
    if isinstance(assembled.root_entity, dict):
        candidates.append(assembled.root_entity)
    candidates.extend(e for e in assembled.related_entities if isinstance(e, dict))

    for entity in candidates:
        old_id = entity.get("id")
        if not isinstance(old_id, str) or old_id not in invalid_links:
            continue
        entity_type = entity.get("type")
        resolver = _ID_RESOLVER_BY_TYPE.get(entity_type) if isinstance(entity_type, str) else None
        if resolver is None:
            continue

        old_id_source = entity.get("idSource") if isinstance(entity.get("idSource"), str) else None
        demoted = _entity_with_demoted_source(entity, old_id_source)
        try:
            new_id, new_id_source = resolver(demoted)
        except Exception:  # noqa: BLE001
            continue
        if not isinstance(new_id, str) or new_id == old_id:
            continue

        entity["id"] = new_id
        entity["idSource"] = new_id_source
        rewrites[old_id] = new_id
        warnings.append(
            f"Promoted entity id {old_id} → {new_id} "
            f"(failed source: {old_id_source}, new source: {new_id_source})",
        )

    if not rewrites:
        return assembled, {}, []

    new_root = (
        _apply_id_rewrites(assembled.root_entity, rewrites)
        if isinstance(assembled.root_entity, dict)
        else assembled.root_entity
    )
    new_related = [_apply_id_rewrites(entity, rewrites) for entity in assembled.related_entities]
    new_excluded = [_apply_id_rewrites(entity, rewrites) for entity in assembled.excluded_entities]

    promoted = AssembledOutput(
        root_entity=new_root if isinstance(new_root, dict) else assembled.root_entity,
        related_entities=new_related,
        excluded_entities=new_excluded,
        warnings=list(assembled.warnings),
    )
    return promoted, rewrites, warnings


def apply_link_pruning_to_assembled_output(
    *,
    assembled: AssembledOutput,
    invalid_links: set[str],
    entity_link_map: dict[str, list[str]],
    article_identifier_link_map: dict[str, str],
) -> tuple[AssembledOutput, list[str]]:
    if not invalid_links:
        return assembled, []

    warnings: list[str] = []
    excluded_entities = list(assembled.excluded_entities)

    kept_root: dict[str, Any] | None = None
    kept_related: list[dict[str, Any]] = []
    entities: list[tuple[bool, dict[str, Any]]] = []
    if isinstance(assembled.root_entity, dict):
        entities.append((True, assembled.root_entity))
    entities.extend((False, entity) for entity in assembled.related_entities if isinstance(entity, dict))

    for is_root, entity in entities:
        entity_id = entity.get("id") if isinstance(entity.get("id"), str) else None
        if not isinstance(entity_id, str) or not entity_id:
            if is_root:
                kept_root = entity
            else:
                kept_related.append(entity)
            continue

        entity_links = set(entity_link_map.get(entity_id, []))
        valid_links = entity_links - invalid_links
        invalid_for_entity = sorted(entity_links & invalid_links)
        doi_link = article_identifier_link_map.get(entity_id)

        drop_reason: str | None = None
        if entity_id in invalid_links:
            drop_reason = "Entity identifier URL failed link validation"
        elif isinstance(doi_link, str) and doi_link in invalid_links:
            drop_reason = "Article DOI URL failed link validation"
        elif entity_links and not valid_links:
            drop_reason = "No valid URLs remaining after link validation"

        if drop_reason is not None:
            excluded_entities.append(
                {
                    "entity_type": _entity_type_singular(entity),
                    "entity": entity,
                    "reason": [
                        {
                            "path": "<root>",
                            "message": "link_veracity_pruned",
                            "constraint": "link_veracity",
                            "expected": drop_reason,
                        },
                    ],
                },
            )
            warnings.append(
                (
                    f"Removed {_entity_type_singular(entity)} entity '{entity_id}' after link validation: "
                    f"{drop_reason}"
                ),
            )
            continue

        cleaned_entity = _clean_invalid_http_values(entity, invalid_links)
        if invalid_for_entity:
            warnings.append(
                (
                    f"Removed invalid link(s) from entity '{entity_id}': "
                    + ", ".join(invalid_for_entity[:5])
                    + (f", +{len(invalid_for_entity) - 5} more" if len(invalid_for_entity) > 5 else "")
                ),
            )
        if is_root:
            kept_root = cleaned_entity
        else:
            kept_related.append(cleaned_entity)

    updated = AssembledOutput(
        root_entity=kept_root,
        related_entities=kept_related,
        excluded_entities=excluded_entities,
        warnings=list(assembled.warnings),
    )
    return updated, warnings


async def run_link_veracity_stage(
    *,
    source_url: str,
    providers: ProviderSet,
    max_concurrency: int = 3,
    llm_call_timeout_seconds: float = 120.0,
    jsonld_payload: dict[str, Any] | None = None,
    entities: list[dict[str, Any]] | None = None,
    cache: ProviderCache | None = None,
) -> LinkVeracityStageResult:
    entity_link_map: dict[str, set[str]] = {}
    article_identifier_link_map: dict[str, str] = {}
    if entities is not None:
        link_contexts, entity_link_map, article_identifier_link_map = _scan_entity_links(entities)
    else:
        if not isinstance(jsonld_payload, dict):
            message = "run_link_veracity_stage requires either entities or jsonld_payload"
            raise ValueError(message)
        link_contexts = collect_unique_http_link_contexts(jsonld_payload)
        for context in link_contexts:
            source_entity_id = context.get("source_entity_id")
            link = context.get("link")
            if not isinstance(source_entity_id, str) or not isinstance(link, str):
                continue
            entity_link_map.setdefault(source_entity_id, set()).add(link)

    checked_count = len(link_contexts)
    if checked_count == 0:
        return LinkVeracityStageResult(
            checked_count=0,
            entity_link_map={key: sorted(value) for key, value in entity_link_map.items()},
            article_identifier_link_map=article_identifier_link_map,
        )

    resolved_concurrency = max(1, int(max_concurrency))
    semaphore = asyncio.Semaphore(resolved_concurrency)
    verifier = LLMLinkVeracityAgentV2(
        llm_call_timeout_seconds=llm_call_timeout_seconds,
        cache=cache,
    )

    async def _run_link_context(link_context: dict[str, Any]) -> dict[str, Any]:
        link = link_context.get("link")
        if not isinstance(link, str) or not link:
            return {
                "link": "<unknown>",
                "status": "error",
                "error": "Missing link value",
                "source_entity_id": link_context.get("source_entity_id"),
                "predicate": link_context.get("predicate"),
                "relationships": link_context.get("relationships", []),
            }

        context = {**link_context, "source_url": source_url}
        async with semaphore:
            try:
                result = await verifier.run(context, providers)
            except Exception as exc:  # noqa: BLE001
                return {
                    "link": link,
                    "status": "error",
                    "error": str(exc),
                    "source_entity_id": link_context.get("source_entity_id"),
                    "predicate": link_context.get("predicate"),
                    "relationships": link_context.get("relationships", []),
                }

        payload = result.data if isinstance(result.data, dict) else {}
        relationship_supported = bool(payload.get("relationship_supported"))
        relationship_summary = payload.get("relationship_summary")
        fetched_successfully = payload.get("fetched_successfully")
        return {
            "link": link,
            "status": "ok",
            "relationship_supported": relationship_supported,
            "relationship_summary": (
                relationship_summary if isinstance(relationship_summary, str) else None
            ),
            "fetched_successfully": (
                fetched_successfully if isinstance(fetched_successfully, bool) else None
            ),
            "source_entity_id": link_context.get("source_entity_id"),
            "predicate": link_context.get("predicate"),
            "relationships": link_context.get("relationships", []),
            "model": result.model,
            "provider": result.provider,
            "tokens_prompt": result.tokens_prompt,
            "tokens_completion": result.tokens_completion,
        }

    gathered_records = await asyncio.gather(*(_run_link_context(context) for context in link_contexts))
    records = sorted(gathered_records, key=lambda record: str(record.get("link", "")))

    warnings: list[str] = []
    supported_count = 0
    unsupported_count = 0
    failed_count = 0
    invalid_links: set[str] = set()
    for record in records:
        status = record.get("status")
        link = record.get("link")
        if status != "ok":
            failed_count += 1
            warnings.append(
                f"Link veracity check failed: link={link}, error={record.get('error')}",
            )
            continue

        if record.get("fetched_successfully") is False:
            failed_count += 1
            if isinstance(link, str):
                invalid_links.add(link)
            warnings.append(
                f"Link veracity fetch failed: link={link}",
            )
            continue

        if bool(record.get("relationship_supported")):
            supported_count += 1
            continue

        unsupported_count += 1
        warnings.append(
            (
                "Link veracity unsupported relationship: "
                f"link={link}, source={record.get('source_entity_id')}, "
                f"predicate={record.get('predicate')}"
            ),
        )

    return LinkVeracityStageResult(
        records=records,
        warnings=warnings,
        checked_count=checked_count,
        supported_count=supported_count,
        unsupported_count=unsupported_count,
        failed_count=failed_count,
        invalid_links=sorted(invalid_links),
        entity_link_map={key: sorted(value) for key, value in entity_link_map.items()},
        article_identifier_link_map=article_identifier_link_map,
    )
