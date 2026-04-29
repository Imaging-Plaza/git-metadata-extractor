from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlparse

from src.v2.agents import LLMLinkVeracityAgentV2, ProviderSet
from src.v2.pipeline.stages.models import AssembledOutput

HTTP_SCHEMES = {"http", "https"}
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
    return parsed.scheme in HTTP_SCHEMES and bool(parsed.netloc)


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
            if isinstance(at_id, str):
                _record_link(
                    link=at_id,
                    source_entity_id=source_entity_id,
                    predicate=predicate,
                )
            for key, nested in value.items():
                if key == "@id":
                    continue
                next_predicate = predicate if predicate else key
                _walk(
                    nested,
                    source_entity_id=source_entity_id,
                    predicate=next_predicate,
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
            for key, nested in value.items():
                _walk(
                    nested,
                    source_entity_id=source_entity_id,
                    predicate=key if predicate is None else predicate,
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
