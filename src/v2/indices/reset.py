"""Reset / cold-start a per-provider index.

Three layers can be wiped:
  1. **DuckDB file** — deletes ``<INDEX_DATA_DIR>/<provider>/duckdb/<provider>.duckdb``
     (and the cached in-process handle so the next ingest re-bootstraps
     the schema from scratch).
  2. **Qdrant collection(s)** — drops every Qdrant collection the
     provider owns. After this the next embed run rebuilds the
     collection from the DuckDB rows (when present) or from a fresh
     wire pull (when DuckDB was also wiped).
  3. **ProviderCache** (opt-in, ``wipe_cache=True``) — clears the
     per-provider HTTP-response cache used by the wire clients. Default
     is False so re-ingest reuses cached upstream responses for speed;
     set True when you suspect the upstream data itself has shifted.

The reset is **idempotent**: calling it twice in a row is fine — the
second call sees a missing DB / missing collection and treats those
as "already done".

Wired to:
  - ``DELETE /v2/indices/<provider>/reset`` (per-provider)
  - ``DELETE /v2/indices/reset-all`` (every known provider)
  - ``python -m src.v2.indices.reset <provider>`` (CLI)
  - ``python -m src.v2.indices.reset --all`` (CLI bulk)
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.v2.indices.compact import close_cached_resources_for

if TYPE_CHECKING:
    from collections.abc import Iterable

LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Per-provider spec
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class _ResetSpec:
    """How to locate the on-disk + Qdrant assets for one provider."""

    # `duckdb_path_getter` is a zero-arg callable returning the DuckDB
    # path. Bridges the two paths-module conventions in the codebase:
    # class-based (returns an object with `.duckdb_path` attr) and
    # function-based (exposes `duckdb_path()` directly). Set to None
    # for providers without a DuckDB.
    duckdb_path_getter: Any | None
    # Qdrant collection names this provider owns. Empty for DuckDB-only
    # providers (zenodo_communities — no Qdrant).
    qdrant_collections: tuple[str, ...]
    # Importable module + function for `load_config()` returning a
    # config object with `.qdrant.{url, api_key, prefer_grpc}`. Used to
    # build the Qdrant client + locate the ProviderCache path. None
    # when the provider has no Qdrant and no ProviderCache.
    config_loader_dotted: str | None


def _hf_models_spec() -> _ResetSpec:
    from src.index.huggingface_models.paths import get_huggingface_models_paths
    return _ResetSpec(
        duckdb_path_getter=lambda g=get_huggingface_models_paths: g().duckdb_path,
        qdrant_collections=("huggingface_models",),
        config_loader_dotted="src.index.huggingface_models.config:load_config",
    )


def _hf_datasets_spec() -> _ResetSpec:
    from src.index.huggingface_datasets.paths import get_huggingface_datasets_paths
    return _ResetSpec(
        duckdb_path_getter=lambda g=get_huggingface_datasets_paths: g().duckdb_path,
        qdrant_collections=("huggingface_datasets",),
        config_loader_dotted="src.index.huggingface_datasets.config:load_config",
    )


def _hf_spaces_spec() -> _ResetSpec:
    from src.index.huggingface_spaces.paths import get_huggingface_spaces_paths
    return _ResetSpec(
        duckdb_path_getter=lambda g=get_huggingface_spaces_paths: g().duckdb_path,
        qdrant_collections=("huggingface_spaces",),
        config_loader_dotted="src.index.huggingface_spaces.config:load_config",
    )


def _hf_users_spec() -> _ResetSpec:
    from src.index.huggingface_users.paths import get_huggingface_users_paths
    return _ResetSpec(
        duckdb_path_getter=lambda g=get_huggingface_users_paths: g().duckdb_path,
        qdrant_collections=("huggingface_users",),
        config_loader_dotted="src.index.huggingface_users.config:load_config",
    )


def _hf_organizations_spec() -> _ResetSpec:
    from src.index.huggingface_organizations.paths import (
        get_huggingface_organizations_paths,
    )
    return _ResetSpec(
        duckdb_path_getter=lambda g=get_huggingface_organizations_paths: g().duckdb_path,
        qdrant_collections=("huggingface_organizations",),
        config_loader_dotted="src.index.huggingface_organizations.config:load_config",
    )


def _hf_papers_spec() -> _ResetSpec:
    from src.index.huggingface_papers.paths import get_huggingface_papers_paths
    return _ResetSpec(
        duckdb_path_getter=lambda g=get_huggingface_papers_paths: g().duckdb_path,
        qdrant_collections=("huggingface_papers",),
        config_loader_dotted="src.index.huggingface_papers.config:load_config",
    )


def _github_repos_spec() -> _ResetSpec:
    # G3 renamed the module to github_repos but kept the function name
    # `get_github_paths` for backwards compatibility within the module.
    from src.index.github_repos.paths import get_github_paths
    return _ResetSpec(
        duckdb_path_getter=lambda g=get_github_paths: g().duckdb_path,
        qdrant_collections=("github_repos",),
        config_loader_dotted="src.index.github_repos.config:load_config",
    )


def _dockerhub_spec() -> _ResetSpec:
    from src.index.dockerhub.paths import get_dockerhub_paths
    return _ResetSpec(
        duckdb_path_getter=lambda g=get_dockerhub_paths: g().duckdb_path,
        qdrant_collections=("dockerhub",),
        config_loader_dotted="src.index.dockerhub.config:load_config",
    )


def _github_users_spec() -> _ResetSpec:
    from src.index.github_users.paths import get_github_users_paths
    return _ResetSpec(
        duckdb_path_getter=lambda g=get_github_users_paths: g().duckdb_path,
        qdrant_collections=("github_users",),
        config_loader_dotted="src.index.github_users.config:load_config",
    )


def _github_organizations_spec() -> _ResetSpec:
    from src.index.github_organizations.paths import (
        get_github_organizations_paths,
    )
    return _ResetSpec(
        duckdb_path_getter=lambda g=get_github_organizations_paths: g().duckdb_path,
        qdrant_collections=("github_organizations",),
        config_loader_dotted="src.index.github_organizations.config:load_config",
    )


def _zenodo_records_spec() -> _ResetSpec:
    from src.index.zenodo_records.paths import get_zenodo_paths
    return _ResetSpec(
        duckdb_path_getter=lambda g=get_zenodo_paths: g().duckdb_path,
        qdrant_collections=("zenodo_records",),
        config_loader_dotted="src.index.zenodo_records.config:load_config",
    )


def _zenodo_communities_spec() -> _ResetSpec:
    # DuckDB-only. No Qdrant.
    from src.index.zenodo_communities import paths as zc_paths

    return _ResetSpec(
        duckdb_path_getter=zc_paths.duckdb_path,
        qdrant_collections=(),
        config_loader_dotted=None,
    )


# Catalog-only providers (DuckDB-only or non-trivial Qdrant layouts):
# we list their DuckDB path lookup and either an empty `qdrant_collections`
# tuple (no Qdrant) or the known collection names. For the multi-collection
# catalogs (openalex, infoscience, ethz_research_collection, orcid, snsf,
# oamonitor) we hard-code the collection list based on their `*.py` constants
# at the time of writing.


def _openalex_spec() -> _ResetSpec:
    from src.index.openalex.paths import get_openalex_paths
    return _ResetSpec(
        duckdb_path_getter=lambda g=get_openalex_paths: g().duckdb_path,
        # openalex builds one collection per entity type at embed time.
        # See src/index/openalex/embed/pipeline.py — collection name
        # equals the entity_type string. These are the six the catalog
        # currently supports.
        qdrant_collections=(
            "works", "authors", "institutions", "sources", "topics", "concepts",
        ),
        config_loader_dotted="src.index.openalex.config:load_config",
    )


def _orcid_spec() -> _ResetSpec:
    from src.index.orcid.paths import get_orcid_paths
    return _ResetSpec(
        duckdb_path_getter=lambda g=get_orcid_paths: g().duckdb_path,
        # Single-entity orcid index — see embed/pipeline.py:115.
        qdrant_collections=("persons",),
        config_loader_dotted="src.index.orcid.config:load_config",
    )


def _ror_spec() -> _ResetSpec:
    # ROR uses module-function paths (no class-based loader).
    from src.index.ror import paths as ror_paths
    return _ResetSpec(
        # ROR keys files by `scope_mode`, not a single duckdb file.
        # `faiss_path("epfl")` is the closest analogue — see paths.py.
        # The reset module focuses on DuckDB providers; ROR's index
        # files are Faiss + JSON, handled by the scope-specific
        # `delete_dump` CLI. For now, expose a no-op duckdb_path so
        # the rest of the reset (Qdrant) still works.
        duckdb_path_getter=None,
        qdrant_collections=("ror",),
        config_loader_dotted="src.index.ror.config:load_config",
    )


def _infoscience_spec() -> _ResetSpec:
    # Infoscience uses module-function paths.
    from src.index.infoscience.paths import duckdb_path as infoscience_duckdb_path
    return _ResetSpec(
        duckdb_path_getter=infoscience_duckdb_path,
        # See src/index/infoscience/store.py: 4 entity collections.
        qdrant_collections=(
            "infoscience_chunks",
            "infoscience_articles",
            "infoscience_persons",
            "infoscience_organizations",
        ),
        config_loader_dotted="src.index.infoscience.config:load_config",
    )


def _ethz_research_collection_spec() -> _ResetSpec:
    # ETHZ uses module-function paths.
    from src.index.ethz_research_collection.paths import (
        duckdb_path as ethz_duckdb_path,
    )
    return _ResetSpec(
        duckdb_path_getter=ethz_duckdb_path,
        qdrant_collections=(
            "ethz_research_collection_chunks",
            "ethz_research_collection_articles",
            "ethz_research_collection_persons",
            "ethz_research_collection_organizations",
        ),
        config_loader_dotted="src.index.ethz_research_collection.config:load_config",
    )


def _snsf_spec() -> _ResetSpec:
    # SNSF uses module-function paths.
    from src.index.snsf.paths import duckdb_path as snsf_duckdb_path
    return _ResetSpec(
        duckdb_path_getter=snsf_duckdb_path,
        # SNSF keys Qdrant collections by scope_mode. The current
        # two scopes are 'epfl' and 'switzerland'; if more are added,
        # extend this list.
        qdrant_collections=("epfl", "switzerland"),
        config_loader_dotted="src.index.snsf.config:load_config",
    )


def _renkulab_spec() -> _ResetSpec:
    from src.index.renkulab.paths import get_renkulab_paths
    return _ResetSpec(
        duckdb_path_getter=lambda g=get_renkulab_paths: g().duckdb_path,
        qdrant_collections=("renkulab",),
        config_loader_dotted="src.index.renkulab.config:load_config",
    )


def _swissubase_spec() -> _ResetSpec:
    from src.index.swissubase.paths import get_swissubase_paths
    return _ResetSpec(
        duckdb_path_getter=lambda g=get_swissubase_paths: g().duckdb_path,
        qdrant_collections=("swissubase_entities",),
        config_loader_dotted="src.index.swissubase.config:load_config",
    )


def _oamonitor_spec() -> _ResetSpec:
    from src.index.oamonitor.paths import get_oamonitor_paths
    return _ResetSpec(
        duckdb_path_getter=lambda g=get_oamonitor_paths: g().duckdb_path,
        # 4 collections; see src/index/oamonitor/ingest/*.py.
        qdrant_collections=("Journals", "Organisations", "Publishers", "Publications"),
        config_loader_dotted="src.index.oamonitor.config:load_config",
    )


def _epfl_graph_spec() -> _ResetSpec:
    from src.index.epfl_graph.paths import get_epfl_graph_paths
    return _ResetSpec(
        duckdb_path_getter=lambda g=get_epfl_graph_paths: g().duckdb_path,
        qdrant_collections=("epfl_graph_disciplines",),
        config_loader_dotted="src.index.epfl_graph.config:load_config",
    )


# Provider → lazy spec loader. Lazy so importing this module doesn't
# pull in every index module's config (each carries a pydantic model
# graph + yaml read at import time).
_SPEC_LOADERS: dict[str, Any] = {
    "huggingface_models": _hf_models_spec,
    "huggingface_datasets": _hf_datasets_spec,
    "huggingface_spaces": _hf_spaces_spec,
    "huggingface_users": _hf_users_spec,
    "huggingface_organizations": _hf_organizations_spec,
    "huggingface_papers": _hf_papers_spec,
    "github_repos": _github_repos_spec,
    "dockerhub": _dockerhub_spec,
    "github_users": _github_users_spec,
    "github_organizations": _github_organizations_spec,
    "zenodo_records": _zenodo_records_spec,
    "zenodo_communities": _zenodo_communities_spec,
    "openalex": _openalex_spec,
    "orcid": _orcid_spec,
    "ror": _ror_spec,
    "infoscience": _infoscience_spec,
    "ethz_research_collection": _ethz_research_collection_spec,
    "snsf": _snsf_spec,
    "renkulab": _renkulab_spec,
    "swissubase": _swissubase_spec,
    "oamonitor": _oamonitor_spec,
    "epfl_graph": _epfl_graph_spec,
}


def known_providers() -> tuple[str, ...]:
    """Names of every provider the reset module knows about."""
    return tuple(_SPEC_LOADERS.keys())


# ---------------------------------------------------------------------------
# Reset result + main function
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ResetResult:
    """What a reset call actually did."""

    provider: str
    duckdb_path: str | None
    duckdb_bytes_reclaimed: int
    duckdb_deleted: bool
    qdrant_collections_attempted: tuple[str, ...]
    qdrant_collections_dropped: tuple[str, ...]
    qdrant_skipped: bool
    cache_cleared: bool
    elapsed_seconds: float


class UnknownProviderError(ValueError):
    """Raised when reset_index is called with a provider name not in
    _SPEC_LOADERS. The error message lists the supported providers
    so the operator can fix the typo."""


def _load_spec(provider: str) -> _ResetSpec:
    loader = _SPEC_LOADERS.get(provider)
    if loader is None:
        message = (
            f"unknown provider for reset: {provider!r}; "
            f"supported: {', '.join(sorted(_SPEC_LOADERS))}"
        )
        raise UnknownProviderError(message)
    return loader()


def _import_config_loader(dotted: str) -> Any:
    """Resolve a "module.path:func" string to the callable."""
    module_path, _, attr = dotted.partition(":")
    from importlib import import_module
    module = import_module(module_path)
    return getattr(module, attr)


def _delete_duckdb(db_path: Path) -> tuple[bool, int]:
    """Best-effort DuckDB file delete. Returns (deleted, bytes_freed).

    Idempotent: a missing file returns (False, 0) without raising.
    """
    if not db_path.exists():
        return False, 0
    bytes_freed = db_path.stat().st_size
    db_path.unlink()
    # DuckDB writes a `<file>.wal` alongside when there are uncommitted
    # transactions; clean that up too.
    wal_path = db_path.with_name(db_path.name + ".wal")
    if wal_path.exists():
        try:
            wal_path.unlink()
        except OSError:  # noqa: BLE001
            pass
    return True, bytes_freed


def _drop_qdrant_collections(
    config: Any,
    collections: Iterable[str],
) -> list[str]:
    """Drop each collection that exists. Returns the list of names
    actually dropped. Missing collections are treated as a no-op so
    reset stays idempotent.
    """
    from qdrant_client import QdrantClient

    qdrant_cfg = getattr(config, "qdrant", None)
    if qdrant_cfg is None:
        LOGGER.warning("reset: config has no `qdrant` block — skipping Qdrant drop")
        return []
    client = QdrantClient(
        url=qdrant_cfg.url,
        prefer_grpc=getattr(qdrant_cfg, "prefer_grpc", False),
        api_key=getattr(qdrant_cfg, "api_key", None),
    )
    dropped: list[str] = []
    try:
        existing = {c.name for c in client.get_collections().collections}
    except Exception as exc:  # noqa: BLE001
        LOGGER.warning("reset: failed to list Qdrant collections — %s", exc)
        return []
    for name in collections:
        if name not in existing:
            LOGGER.info("reset: Qdrant collection %r absent — skipping", name)
            continue
        try:
            client.delete_collection(collection_name=name)
            dropped.append(name)
            LOGGER.info("reset: dropped Qdrant collection %r", name)
        except Exception as exc:  # noqa: BLE001 — best-effort
            LOGGER.warning(
                "reset: failed to drop Qdrant collection %r — %s", name, exc,
            )
    return dropped


def _clear_provider_cache(config: Any) -> bool:
    """Wipe the ProviderCache file for this provider. Returns True if a
    file was deleted, False if none existed.
    """
    paths = getattr(config, "paths", None)
    if paths is None:
        return False
    cache_path = getattr(paths, "cache_db_path", None)
    if cache_path is None:
        return False
    cache_path = Path(cache_path)
    if not cache_path.exists():
        return False
    try:
        cache_path.unlink()
        LOGGER.info("reset: cleared ProviderCache at %s", cache_path)
        return True
    except OSError as exc:
        LOGGER.warning("reset: failed to clear ProviderCache at %s — %s", cache_path, exc)
        return False


def reset_index(
    provider: str,
    *,
    app_state: Any | None = None,
    wipe_qdrant: bool = True,
    wipe_cache: bool = False,
) -> ResetResult:
    """Cold-start a single provider's index.

    Order of operations:
      1. Close cached in-process resources (DuckDB write handles +
         Qdrant clients) so the deletes don't fight the live process.
      2. Delete the DuckDB file (always).
      3. Drop the Qdrant collection(s) if ``wipe_qdrant``.
      4. Clear the ProviderCache file if ``wipe_cache``.

    Idempotent. Safe to call when the DB or collection is already gone.
    """
    start = time.monotonic()
    spec = _load_spec(provider)

    # 1. Close any cached in-process handle. No-op if `app_state` is
    # None (CLI path) — nothing to close because nothing was opened.
    if app_state is not None:
        close_cached_resources_for(provider, app_state)

    # 2. DuckDB.
    duckdb_path: Path | None = None
    duckdb_deleted = False
    bytes_freed = 0
    if spec.duckdb_path_getter is not None:
        duckdb_path = Path(spec.duckdb_path_getter())
        duckdb_deleted, bytes_freed = _delete_duckdb(duckdb_path)

    # 3. Qdrant collection(s).
    collections_dropped: list[str] = []
    qdrant_skipped = False
    if not wipe_qdrant or not spec.qdrant_collections:
        qdrant_skipped = True
    else:
        if spec.config_loader_dotted is None:
            qdrant_skipped = True
            LOGGER.warning(
                "reset: provider %s has Qdrant collections but no config loader; skipping",
                provider,
            )
        else:
            try:
                load_config = _import_config_loader(spec.config_loader_dotted)
                config = load_config()
                collections_dropped = _drop_qdrant_collections(
                    config, spec.qdrant_collections,
                )
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning(
                    "reset: Qdrant drop for %s failed — %s; continuing", provider, exc,
                )

    # 4. ProviderCache (opt-in).
    cache_cleared = False
    if wipe_cache and spec.config_loader_dotted is not None:
        try:
            load_config = _import_config_loader(spec.config_loader_dotted)
            config = load_config()
            cache_cleared = _clear_provider_cache(config)
        except Exception as exc:  # noqa: BLE001
            LOGGER.warning(
                "reset: ProviderCache clear for %s failed — %s", provider, exc,
            )

    result = ResetResult(
        provider=provider,
        duckdb_path=str(duckdb_path) if duckdb_path is not None else None,
        duckdb_bytes_reclaimed=bytes_freed,
        duckdb_deleted=duckdb_deleted,
        qdrant_collections_attempted=spec.qdrant_collections,
        qdrant_collections_dropped=tuple(collections_dropped),
        qdrant_skipped=qdrant_skipped,
        cache_cleared=cache_cleared,
        elapsed_seconds=time.monotonic() - start,
    )
    LOGGER.info(
        "reset %s: duckdb=%s (%d bytes), qdrant=%d/%d dropped, cache=%s, %.2fs",
        provider, duckdb_deleted, bytes_freed,
        len(collections_dropped), len(spec.qdrant_collections),
        cache_cleared, result.elapsed_seconds,
    )
    return result


def reset_all(
    *,
    app_state: Any | None = None,
    wipe_qdrant: bool = True,
    wipe_cache: bool = False,
) -> list[ResetResult]:
    """Reset every known provider. Failures on one provider do not
    stop the rest — each provider's result is captured independently."""
    results: list[ResetResult] = []
    for provider in known_providers():
        try:
            results.append(
                reset_index(
                    provider,
                    app_state=app_state,
                    wipe_qdrant=wipe_qdrant,
                    wipe_cache=wipe_cache,
                ),
            )
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("reset_all: %s failed — %s", provider, exc)
    return results


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _cli_main() -> int:  # pragma: no cover — CLI entry
    """``python -m src.v2.indices.reset <provider> | --all``"""
    import argparse
    import json

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    parser = argparse.ArgumentParser(prog="reset-index")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument(
        "provider",
        nargs="?",
        help="Provider name (one of: " + ", ".join(sorted(_SPEC_LOADERS)) + ")",
    )
    group.add_argument(
        "--all",
        action="store_true",
        dest="reset_everything",
        help="Reset every known provider.",
    )
    parser.add_argument(
        "--no-qdrant",
        action="store_true",
        help="Skip dropping Qdrant collections (DuckDB-only reset).",
    )
    parser.add_argument(
        "--wipe-cache",
        action="store_true",
        help="Also clear the per-provider ProviderCache (hard reset).",
    )
    args = parser.parse_args()

    if args.reset_everything:
        results = reset_all(
            wipe_qdrant=not args.no_qdrant,
            wipe_cache=args.wipe_cache,
        )
        print(json.dumps(
            {"results": [r.__dict__ for r in results]},
            indent=2,
            default=str,
        ))
        return 0
    try:
        result = reset_index(
            args.provider,
            wipe_qdrant=not args.no_qdrant,
            wipe_cache=args.wipe_cache,
        )
    except UnknownProviderError as exc:
        parser.error(str(exc))
        return 2  # unreachable; parser.error sys.exits
    print(json.dumps(result.__dict__, indent=2, default=str))
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_cli_main())
