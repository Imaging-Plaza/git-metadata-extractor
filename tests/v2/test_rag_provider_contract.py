# ruff: noqa: ARG001 — every test takes the whole PROVIDERS row; most use a subset
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
from pathlib import Path

import pytest

#: One row per provider module. Every field was read off the code rather than
#: assumed — four of them contradicted the obvious guess, which is the point of
#: writing this before the refactor:
#:
#: - `OamonitorRagProvider` / `RenkulabRagProvider`, not `OaMonitor` /
#:   `RenkuLab`. Casing is API.
#: - `ror` and `snsf` take `(store, rcp)`; the other eleven take
#:   `(store, embedder, reranker)`. Two different collaborator shapes, so a
#:   single `__init__` cannot cover all thirteen as-is.
#: - `infoscience` and `ethz_research_collection` do **not** gate on their env
#:   var inside `build_default_provider`; `dependencies.py` gates them instead.
#:   The other eleven gate themselves.
#:
#: fields: (module, class, scope param, env var gated *in the provider*, collaborators)
PROVIDERS: tuple[tuple[str, str, str | None, str | None, str], ...] = (
    ("epfl_graph_rag", "EpflGraphRagProvider", None, "V2_EPFL_GRAPH_RAG_ENABLED", "embedder"),
    ("github_rag", "GitHubRagProvider", None, "V2_GITHUB_RAG_ENABLED", "embedder"),
    ("swissubase_rag", "SwissubaseRagProvider", None, "V2_SWISSUBASE_RAG_ENABLED", "embedder"),
    ("zenodo_rag", "ZenodoRagProvider", None, "V2_ZENODO_RAG_ENABLED", "embedder"),
    ("ror_rag", "RorRagProvider", "scope_mode", "V2_ROR_RAG_ENABLED", "rcp"),
    ("snsf_rag", "SnsfRagProvider", "scope_mode", "V2_SNSF_RAG_ENABLED", "rcp"),
    ("openalex_rag", "OpenAlexRagProvider", "collection", "V2_OPENALEX_RAG_ENABLED", "embedder"),
    ("huggingface_rag", "HuggingFaceRagProvider", "collection", "V2_HUGGINGFACE_RAG_ENABLED", "embedder"),
    ("orcid_rag", "OrcidRagProvider", "entity_type", "V2_ORCID_RAG_ENABLED", "embedder"),
    ("oamonitor_rag", "OamonitorRagProvider", "entity_type", "V2_OAMONITOR_RAG_ENABLED", "embedder"),
    ("renkulab_rag", "RenkulabRagProvider", None, "V2_RENKULAB_RAG_ENABLED", "embedder"),
    ("infoscience_rag", "InfoscienceRagProvider", None, None, "embedder"),
    (
        "ethz_research_collection_rag",
        "EthzResearchCollectionRagProvider",
        None,
        None,
        "embedder",
    ),
)

IDS = [name for name, *_ in PROVIDERS]


def _module(suffix: str):
    return importlib.import_module(f"git_metadata_extractor.providers.{suffix}")


@pytest.mark.parametrize(
    ("suffix", "cls_name", "scope", "env", "collaborators"),
    PROVIDERS,
    ids=IDS,
)
def test_search_signature_is_stable(
    suffix: str,
    cls_name: str,
    scope: str | None,
    env: str | None,
    collaborators: str,
) -> None:
    """`query` first, then the index's own scope parameter under its own name."""
    provider = getattr(_module(suffix), cls_name)
    params = list(inspect.signature(provider.search).parameters)

    assert params[0] == "self"
    assert params[1] == "query"
    for expected in ("top_k", "filters", "rerank"):
        assert expected in params, f"{cls_name}.search lost `{expected}`"

    if scope is not None:
        assert scope in params, (
            f"{cls_name}.search lost its scope parameter `{scope}` — agent tools "
            f"pass it by keyword, so unifying scope names across indexes breaks "
            f"callers at runtime while still type-checking"
        )


@pytest.mark.parametrize(
    ("suffix", "cls_name", "scope", "env", "collaborators"),
    PROVIDERS,
    ids=IDS,
)
def test_builder_degrades_instead_of_raising(
    suffix: str,
    cls_name: str,
    scope: str | None,
    env: str | None,
    collaborators: str,
) -> None:
    """`build_default_provider` must return `None`, never raise.

    This is the guarantee that a missing Qdrant, an absent yaml or an unset
    `RCP_TOKEN` degrades extraction to no-RAG rather than 500-ing the API.

    Deliberately *not* asserting `is None` here. In a dev shell without
    `RCP_TOKEN` most builders return `None` for that reason alone, so an
    `is None` assertion would pass whatever the env gate did — green, and
    proof of nothing. The gate itself is asserted structurally below.
    """
    result = _module(suffix).build_default_provider()

    assert result is None or type(result).__name__ == cls_name


@pytest.mark.parametrize(
    ("suffix", "cls_name", "scope", "env", "collaborators"),
    PROVIDERS,
    ids=IDS,
)
def test_env_gating_lives_where_we_think_it_does(
    suffix: str,
    cls_name: str,
    scope: str | None,
    env: str | None,
    collaborators: str,
) -> None:
    """Eleven providers gate themselves; two are gated by `dependencies.py`.

    Structural rather than behavioural on purpose — see the docstring above for
    why calling the builder cannot distinguish "gated off" from "no credentials
    in this shell".

    `env` is `None` in the table for exactly two cases: `infoscience` and
    `ethz_research_collection` have a flag that their module ignores.
    """
    module = _module(suffix)
    provider = getattr(module, cls_name)

    # Two implementations of the same fact: a converted provider declares its
    # var on `SPEC` and the shared `build_provider` reads it; an unconverted
    # one calls `env_enabled` inline. Assert the *effective* var either way, so
    # the test survives the conversion it exists to protect.
    spec = getattr(provider, "SPEC", None)
    if spec is not None:
        assert spec.env_var == env, (
            f"{suffix} spec declares env_var={spec.env_var!r}, table says {env!r}"
        )
        return

    source = module.__file__
    assert source is not None
    text = Path(source).read_text(encoding="utf-8")

    if env is None:
        assert "env_enabled(" not in text, (
            f"{suffix} now gates itself; move it into the gated group and "
            f"drop its entry from dependencies.py"
        )
        return

    assert f'env_enabled("{env}")' in text, (
        f"{suffix} no longer gates on {env} inside build_default_provider"
    )


@pytest.mark.parametrize(
    ("suffix", "cls_name", "scope", "env", "collaborators"),
    PROVIDERS,
    ids=IDS,
)
def test_provider_constructs_from_store_embedder_reranker(
    suffix: str,
    cls_name: str,
    scope: str | None,
    env: str | None,
    collaborators: str,
) -> None:
    """There are **two** collaborator shapes, not one.

    Eleven providers take `(store, embedder, reranker)`; `ror` and `snsf` take
    `(store, rcp)`. That is the single biggest obstacle to one shared
    `__init__`, and it is invisible from the 78-96% textual similarity of the
    `search` bodies.
    """
    provider = getattr(_module(suffix), cls_name)
    params = inspect.signature(provider.__init__).parameters

    assert "store" in params
    assert collaborators in params, (
        f"{cls_name}.__init__ expected collaborator `{collaborators}`; "
        f"got {sorted(params)}"
    )
    assert hasattr(provider, "from_config")
