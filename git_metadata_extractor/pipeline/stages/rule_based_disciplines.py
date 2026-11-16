"""Deterministic discipline selection from remote EPFL Graph source facts.

The sources service owns semantic search and category ancestry. This stage
keeps ontology policy: allowed DisciplineV2 values, score floor, granular-first
ancestor order, deduplication and an explicit cap. No local index is opened.
"""

from __future__ import annotations

import logging
import os
from copy import deepcopy
from typing import Any

from git_metadata_extractor.api_models.enums import DisciplineV2
from git_metadata_extractor.pipeline.stages.models import AssembledOutput
from git_metadata_extractor.providers.sources_service import (
    SourcesClient,
    current_sources_client,
)

logger = logging.getLogger(__name__)

REPOSITORY_TYPE = "schema:SoftwareSourceCode"
# Bumped from 3 to 5: the walk now collects the FULL allowed-ancestor
# chain per hit (granular → broad). One semantic hit can contribute
# both `information-engineering` AND `applied-sciences`, so the cap
# must be generous enough to leave room for ~2 hits worth of chains.
DEFAULT_MAX = 5
DEFAULT_MIN_SCORE = 0.4
DEFAULT_TOP_K = 10
README_CHARS_CAP = 4000

_ALLOWED_QIDS: frozenset[str] = frozenset(d.value for d in DisciplineV2)


def _resolve_int_env(name: str, default: int) -> int:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(1, int(raw.strip()))
    except (ValueError, TypeError):
        return default


def _resolve_float_env(name: str, default: float) -> float:
    raw = os.getenv(name)
    if raw is None:
        return default
    try:
        return max(0.0, float(raw.strip()))
    except (ValueError, TypeError):
        return default


def _strip_markdown_for_query(text: str) -> str:
    """Reuse the concept_tagging stripper so HTML/image/badge markup
    doesn't pollute the embedding."""
    try:
        from git_metadata_extractor.pipeline.stages.concept_tagging import (  # noqa: PLC0415
            _strip_markdown,
        )
    except Exception:  # noqa: BLE001
        return text
    return _strip_markdown(text)


def _build_query(
    repo: dict[str, Any],
    readme: str | None,
    *,
    description_override: str | None = None,
) -> str:
    """Compose the embedding query from real signal only.

    deeplabcut README starts with ~1000 chars of `<img>` badges before
    the actual content; without stripping, the embedder gets a soup of
    markup that semantically matches "density-estimation" instead of
    the repo's real topic. We strip markdown/HTML, then concat
    name + description + cleaned README excerpt.
    """
    parts: list[str] = []
    name = repo.get("schema:name")
    if isinstance(name, str) and name.strip():
        parts.append(name.strip())
    desc_candidates = (
        repo.get("schema:description"),
        description_override,
    )
    for desc in desc_candidates:
        if isinstance(desc, str) and desc.strip():
            parts.append(desc.strip())
            break
    if isinstance(readme, str) and readme.strip():
        cleaned_readme = _strip_markdown_for_query(readme).strip()
        # Collapse whitespace so the cap counts text, not boilerplate
        # spaces and blank lines.
        cleaned_readme = " ".join(cleaned_readme.split())
        if cleaned_readme:
            parts.append(cleaned_readme[:README_CHARS_CAP])
    return "\n\n".join(parts)


def _is_repository(entity: Any) -> bool:
    if not isinstance(entity, dict):
        return False
    t = entity.get("type") or entity.get("@type")
    types = t if isinstance(t, list) else [t] if isinstance(t, str) else []
    return any("SoftwareSourceCode" in str(item) for item in types)


def _walk_collect_allowed_ancestors(
    category_id: str,
    qid_by_category: dict[str, tuple[str | None, str | None]],
    *,
    max_steps: int = 8,
) -> tuple[list[str], list[str]]:
    """Walk the parent_id chain and collect EVERY category whose
    wikidata_qid is in DisciplineV2.

    Returns `(allowed_qids_granular_first, trail_for_audit)`.

    Why we collect the whole chain instead of stopping at the first
    match: the enum is a finite set of broad disciplines (Mathematics,
    Statistics, Information Engineering, Applied Sciences, ...). A
    granular EPFL Graph hit like `graphical-models` walks through
    `data-science → information-engineering → applied-sciences`. Both
    `information-engineering` (Q1254373) and `applied-sciences`
    (Q7112556) are valid enum members. Keeping only the first one
    found loses the broader-discipline context that ontology consumers
    expect to be present (a paper that is Information Engineering IS
    also Applied Sciences by subclass relation).

    The caller dedupes across hits so the final `pulse:discipline`
    list reflects granular → broad with no repeats.
    """
    trail: list[str] = []
    matches: list[str] = []
    current = category_id
    for _ in range(max_steps):
        if current not in qid_by_category:
            break
        qid, parent = qid_by_category[current]
        trail.append(current)
        if isinstance(qid, str):
            normalized = f"wd:{qid}"
            if normalized in _ALLOWED_QIDS:
                matches.append(normalized)
        if not isinstance(parent, str) or not parent or parent == current:
            break
        current = parent
    return matches, trail


async def _fetch_category_chain_qids(
    category_ids: list[str], client: SourcesClient,
) -> dict[str, tuple[str | None, str | None]]:
    """Fetch source taxonomy facts; ontology selection remains in this stage."""
    if not category_ids:
        return {}
    result = await client.operation(
        "category-ancestors", {"category_ids": category_ids, "max_depth": 8},
    )
    if result.status != "ok":
        message = f"sources taxonomy lookup {result.status}"
        raise RuntimeError(message)
    rows = result.data.get("categories")
    if not isinstance(rows, list):
        message = "sources taxonomy response has no categories"
        raise TypeError(message)
    return {
        row["category_id"]: (row.get("wikidata_qid"), row.get("parent_id"))
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("category_id"), str)
    }


async def tag_disciplines(
    assembled: AssembledOutput,
    *,
    readme_text: str | None,
    github_description: str | None = None,
) -> tuple[AssembledOutput, list[str]]:
    """Stamp `pulse:discipline` on the root repository entity from
    semantic search hits over the EPFL Graph disciplines RAG."""

    warnings: list[str] = []
    if not isinstance(assembled.root_entity, dict) or not _is_repository(
        assembled.root_entity,
    ):
        return assembled, warnings

    # If a discipline list is already present and non-empty, don't
    # overwrite — respect upstream agent output.
    existing = assembled.root_entity.get("pulse:discipline")
    if isinstance(existing, list) and existing:
        return assembled, warnings

    try:
        from git_metadata_extractor.providers.epfl_graph_rag import (  # noqa: PLC0415
            build_default_provider,
        )
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"rule_based_disciplines: backend unavailable — {exc}")
        return assembled, warnings

    client = current_sources_client()
    if client is None:
        warnings.append("rule_based_disciplines: sources service unavailable")
        return assembled, warnings
    provider = build_default_provider(client=client)
    if provider is None:
        # V2_EPFL_GRAPH_RAG_ENABLED is false or config missing; nothing to do.
        return assembled, warnings

    query = _build_query(
        assembled.root_entity,
        readme_text,
        description_override=github_description,
    )
    if not query.strip():
        return assembled, warnings

    top_k = _resolve_int_env("V2_DISCIPLINE_TAGGER_TOP_K", DEFAULT_TOP_K)
    max_disciplines = _resolve_int_env("V2_DISCIPLINE_TAGGER_MAX", DEFAULT_MAX)
    min_score = _resolve_float_env("V2_DISCIPLINE_TAGGER_MIN_SCORE", DEFAULT_MIN_SCORE)

    try:
        hits = await provider.search(query[:4000], top_k=min(top_k, 200), rerank=True)
    except Exception as exc:  # noqa: BLE001
        warnings.append(f"rule_based_disciplines: sources search failed — {exc}")
        return assembled, warnings

    if not hits:
        return assembled, warnings

    hit_category_ids = [
        h.get("category_id") for h in hits
        if isinstance(h.get("category_id"), str)
    ]
    try:
        qid_by_category = await _fetch_category_chain_qids(hit_category_ids, client)
    except Exception as exc:  # noqa: BLE001 - optional enrichment fails open
        warnings.append(f"rule_based_disciplines: source taxonomy unavailable: {exc}")
        return assembled, warnings
    if not qid_by_category:
        return assembled, warnings

    new_root = deepcopy(assembled.root_entity)
    selected: list[str] = []  # order = granular-first across hits
    seen_qids: set[str] = set()
    for hit in hits:
        score = hit.get("score")
        if not isinstance(score, (int, float)) or float(score) < min_score:
            continue
        category_id = hit.get("category_id")
        if not isinstance(category_id, str):
            continue
        chain_qids, trail = _walk_collect_allowed_ancestors(
            category_id, qid_by_category,
        )
        if not chain_qids:
            continue
        # `chain_qids` is granular-first; preserve that order on insertion
        # so the final pulse:discipline list reads from most specific to
        # broadest by ontology depth.
        for qid in chain_qids:
            if qid in seen_qids:
                continue
            if len(selected) >= max_disciplines:
                break
            seen_qids.add(qid)
            selected.append(qid)
            warnings.append(
                f"Inferred pulse:discipline={qid} from EPFL Graph hit "
                f"{category_id!r} (score={float(score):.3f}, walk={trail}).",
            )
        if len(selected) >= max_disciplines:
            break

    if selected:
        new_root["pulse:discipline"] = selected
    else:
        warnings.append(
            "rule_based_disciplines: no hit produced a discipline in the "
            "DisciplineV2 enum after walking parent chain.",
        )

    return (
        AssembledOutput(
            root_entity=new_root,
            related_entities=list(assembled.related_entities),
            excluded_entities=list(assembled.excluded_entities),
            warnings=list(assembled.warnings),
        ),
        warnings,
    )


__all__ = ["tag_disciplines"]
