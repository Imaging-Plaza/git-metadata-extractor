from __future__ import annotations

import asyncio
from dataclasses import dataclass, field
from urllib.parse import urlparse
from typing import Any

from src.v2.agents import LLMLinkVeracityAgentV2, ProviderSet

HTTP_SCHEMES = {"http", "https"}


@dataclass(slots=True)
class LinkVeracityStageResult:
    records: list[dict[str, Any]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    checked_count: int = 0
    supported_count: int = 0
    unsupported_count: int = 0
    failed_count: int = 0


def _is_http_url(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if not candidate:
        return False
    parsed = urlparse(candidate)
    return parsed.scheme in HTTP_SCHEMES and bool(parsed.netloc)


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


async def run_link_veracity_stage(
    *,
    jsonld_payload: dict[str, Any],
    source_url: str,
    providers: ProviderSet,
    max_concurrency: int = 3,
    llm_call_timeout_seconds: float = 120.0,
) -> LinkVeracityStageResult:
    # TODO(graph-cache): Cache link-veracity verdicts by normalized link + model + relation context.
    # TODO(graph-cache): Define cache invalidation strategy tied to source entity and graph changes.
    # TODO(graph-cache): Integrate link-veracity lookups with future graph-cache read-through/write-through policy.
    link_contexts = collect_unique_http_link_contexts(jsonld_payload)
    checked_count = len(link_contexts)
    if checked_count == 0:
        return LinkVeracityStageResult(checked_count=0)

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
    for record in records:
        status = record.get("status")
        link = record.get("link")
        if status != "ok":
            failed_count += 1
            warnings.append(
                f"Link veracity check failed: link={link}, error={record.get('error')}",
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
    )
