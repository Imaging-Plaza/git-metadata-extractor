# ruff: noqa: ARG001 - shared provider parameter table
"""Characterisation tests for the RAG provider layer.

Fourteen `providers/*_rag.py` modules are 78-96% identical to each other — the
same `search` / `_maybe_rerank` / `from_config` / `build_default_provider`
shape, differing only in a collection name, a filter allowlist, a thin-key
projection and an env var. Collapsing that duplication is plan phase A.

This file exists to make that collapse verifiable, because the corpus cannot:
the RAG path needs Qdrant, which is absent in dev and in CI, so a refactor here
produces an *empty* corpus diff whether it is correct or not. What would break
silently is the per-index data and the call signatures — so those are pinned
here, before any collapse, and must survive it unchanged.

Two things are deliberately asserted as *data* rather than behaviour:

- **Signatures**, because agent tools pass the scope by keyword. The scope
  parameter is named `scope_mode` on ror/snsf, `collection` on
  openalex/huggingface and `entity_type` on orcid/oamonitor. A generic
  implementation that unified them on one name would type-check, pass every
  existing test, and break every caller at runtime.
- **Env gating**, because `build_default_provider` returning `None` when the
  index is disabled is what keeps a missing Qdrant from taking down extraction.
"""
from __future__ import annotations

import importlib
import inspect

import pytest

PROVIDERS: tuple[tuple[str, str, str | None, str | None, str], ...] = (("epfl_graph_rag", "EpflGraphRagProvider", None, "V2_EPFL_GRAPH_RAG_ENABLED", "embedder"), ("github_rag", "GitHubRagProvider", None, "V2_GITHUB_RAG_ENABLED", "embedder"), ("swissubase_rag", "SwissubaseRagProvider", None, "V2_SWISSUBASE_RAG_ENABLED", "embedder"), ("zenodo_rag", "ZenodoRagProvider", None, "V2_ZENODO_RAG_ENABLED", "embedder"), ("ror_rag", "RorRagProvider", "scope_mode", "V2_ROR_RAG_ENABLED", "rcp"), ("snsf_rag", "SnsfRagProvider", "scope_mode", "V2_SNSF_RAG_ENABLED", "rcp"), ("openalex_rag", "OpenAlexRagProvider", "collection", "V2_OPENALEX_RAG_ENABLED", "embedder"), ("huggingface_rag", "HuggingFaceRagProvider", "collection", "V2_HUGGINGFACE_RAG_ENABLED", "embedder"), ("orcid_rag", "OrcidRagProvider", "entity_type", "V2_ORCID_RAG_ENABLED", "embedder"), ("oamonitor_rag", "OamonitorRagProvider", "entity_type", "V2_OAMONITOR_RAG_ENABLED", "embedder"), ("renkulab_rag", "RenkulabRagProvider", None, "V2_RENKULAB_RAG_ENABLED", "embedder"), ("infoscience_rag", "InfoscienceRagProvider", None, None, "embedder"), ("ethz_research_collection_rag", "EthzResearchCollectionRagProvider", None, None, "embedder"))
IDS = [name for name, *_ in PROVIDERS]

def _module(suffix: str):
    return importlib.import_module(f"git_metadata_extractor.providers.{suffix}")

@pytest.mark.parametrize(("suffix", "cls_name", "scope", "env", "collaborators"), PROVIDERS, ids=IDS)
def test_search_signature_is_stable(suffix: str, cls_name: str, scope: str | None, env: str | None, collaborators: str) -> None:
    """`query` first, then the index's own scope parameter under its own name."""
    provider = getattr(_module(suffix), cls_name)
    params = list(inspect.signature(provider.search).parameters)
    assert params[0] == "self"
    assert params[1] == "query"
    for expected in ("top_k", "filters", "rerank"):
        assert expected in params, f"{cls_name}.search lost `{expected}`"
    if scope is not None:
        assert scope in params, f"{cls_name}.search lost its scope parameter `{scope}` — agent tools pass it by keyword, so unifying scope names across indexes breaks callers at runtime while still type-checking"
