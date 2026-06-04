# Index store naming & the federated manifest

This is the contract consumers (the Hub's "Sources" tiles, federated search,
any external reader) should build against. **Do not infer a store's shape from
its name or filename** — read the manifest.

## The one invariant

For every store:

```
store name  ==  <store name>.duckdb  ==  the key in src/index/_federated/registry.py
```

The DuckDB file is always `<store-name>.duckdb` (e.g. `zenodo_communities.duckdb`,
`openalex.duckdb`). The registry (`load_adapters()`) is the source of truth for
which stores exist.

## Two naming patterns (both valid — don't "fix" them)

A store name is **opaque**. Two patterns coexist by design:

| Pattern | Shape | When | Examples |
|---|---|---|---|
| **A — split** | `<source>_<entity>` | a source is split into several independently-useful entity stores | `github_repos`, `github_users`, `huggingface_models`, `zenodo_records`, `zenodo_communities` |
| **B — unified** | `<source>` | a source is one store holding many tables in one DuckDB | `openalex` (10 tables), `snsf` (17), `infoscience` (6), `orcid`, `ror`, `dockerhub` |

You **cannot** put "the table" in the name of a Pattern-B store — `openalex.duckdb`
has works, authors, institutions, … . So `<source>_<table>` is *not* a universal
rule, and trying to enforce it everywhere would misrepresent multi-table stores.

A single DuckDB legitimately holds **many tables** (entity + junction/satellite +
chunk bookkeeping). The file boundary is the *store*, not the table.

## The manifest

`python -m src.index._federated.manifest` emits the structured truth so consumers
never parse names:

```json
{
  "name": "zenodo_communities",
  "duckdb": "zenodo_communities.duckdb",
  "entity_types": ["community"],
  "backend": "duckdb",          // "vector" = own Qdrant collection; "duckdb" = SQL only
  "surface_as_source": true,    // show as a Hub "Sources" tile
  "id_shape": "url"             // v3.0.0: every id is a canonical URL
}
```

`--sources` filters to the stores the Hub should tile: **vector-backed stores
plus DuckDB-only stores explicitly allowlisted** via `surface_as_source`. This is
the curated allowlist — DuckDB-only stores stay off the grid unless they opt in
(so dead/legacy stores don't resurrect), and it's declared in the index, not
hardcoded in the Hub.

### Declaring the hints

The three manifest fields are optional class attributes on the adapter (read via
`getattr`, so they are **not** part of the `IndexAdapter` `isinstance` contract —
existing adapters need no change):

```python
class ZenodoCommunitiesAdapter:
    name = "zenodo_communities"
    entity_types = ["community"]
    backend = "duckdb"
    surface_as_source = True
    id_shape = "url"
```

Defaults when unset: `backend="vector"`, `surface_as_source=False`, `id_shape="url"`.

## Consumer guidance (Hub)

1. Iterate `python -m src.index._federated.manifest --sources` (or the registry).
2. Treat each store as opaque: name = filename, possibly multi-table.
3. Render one tile per entry; use the entity's canonical URL id (`community_id`,
   etc.) as the tile id and click-through target.
4. New stores (e.g. a future `github_communities`) appear automatically once
   registered + allowlisted — no Hub change.
