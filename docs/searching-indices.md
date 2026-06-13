<!--
Source-grounded against:
- src/v2/api.py            GET /v2/manifest (L2571), POST /v2/indices/<p>/search (L3346-4006), GET /v2/health (L4340)
- src/v2/api_models/contracts.py  IndexSearchRequest (L454), IndexSearchHit (L487), IndexSearchResponse (L499), IndexName Literal (L512), V2HealthResponse (L133)
- src/v2/indices/_search_common.py  hit_from_raw() — hit fields id/vector_score/rerank_score/payload/entity (L10)
- src/v2/indices/openalex.py  run_openalex_search() — target default & candidate_k fallback (L138)
- src/v2/indices/gitlab.py  GITLAB_INDEX_NAMES (L33)
- src/v2/auth.py          verify_token() — Bearer API_TOKEN, 503 if unset / 401 if bad
- src/index/openalex/api.py  standalone sidecar app: /search /query /predefined (separate service)
- src/index/_federated/manifest.py  build_manifest() entry shape (name/duckdb/entity_types/backend/surface_as_source/id_shape/structured_query)
- docs/rag-indices.md, docs/federated-search.md  (CLI federation context)
NOTE: there is NO /query or federated-search route on the /v2 HTTP API. /query + /predefined exist only on the per-index sidecar apps; federation is CLI-only (gme search).
-->

# Searching the RAG indices

Query the Git Metadata Extractor (GME) RAG indices over HTTP: discover what's available, then run semantic search against any single index.

The deployed API exposes **per-index semantic search** under `/v2/indices/<provider>/search`, plus a **manifest** endpoint that tells you which indices exist and what each contains. All examples use `http://localhost:8000` — swap in your real deployment host.

!!! note "What this page covers (and what it doesn't)"
    The `/v2` HTTP API gives you **one endpoint shape for all indices**: `POST /v2/indices/<provider>/search`. There is **no** `/query` (SQL) route and **no** federated cross-index route on the `/v2` API. SQL queries (`/query`), predefined query lists (`/predefined`), and federated cross-index search live elsewhere — see [Other surfaces](#other-surfaces-query-predefined-federated) below.

## Authentication

Every `/v2/indices/.../search` endpoint and `GET /v2/manifest` require a bearer token. `GET /v2/health` is the only open endpoint.

```bash
export GME_TOKEN="your-api-token"      # matches the server's API_TOKEN env var
```

Send it as `Authorization: Bearer <token>` on every request.

| Situation | Status |
|---|---|
| Valid token | `200 OK` |
| Missing / non-Bearer header | `401 Unauthorized` (`WWW-Authenticate: Bearer`) |
| Wrong token | `401 Unauthorized` |
| Server has no `API_TOKEN` configured | `503 Service Unavailable` (fails closed) |

!!! warning "Fail-closed auth"
    If the deployment hasn't set `API_TOKEN`, **all** authenticated endpoints return `503` — the service never silently runs open. A `503` with `detail: "Auth not configured: API_TOKEN is unset"` means the *server* is misconfigured, not your request.

## Discover available indices — `GET /v2/manifest`

Before searching, list the registered index stores. Each entry is the contract you build against.

```bash
curl -s http://localhost:8000/v2/manifest \
  -H "Authorization: Bearer $GME_TOKEN"
```

Trimmed response (one object per store, sorted ascending by `name`):

```json
[
  {
    "name": "github_repos",
    "duckdb": "github_repos.duckdb",
    "entity_types": ["repos"],
    "backend": "vector",
    "surface_as_source": false,
    "id_shape": "url",
    "structured_query": false
  },
  {
    "name": "openalex",
    "duckdb": "openalex.duckdb",
    "entity_types": ["works", "authors", "institutions", "sources", "topics", "concepts"],
    "backend": "vector",
    "surface_as_source": false,
    "id_shape": "url",
    "structured_query": false
  },
  {
    "name": "zenodo_communities",
    "duckdb": "zenodo_communities.duckdb",
    "entity_types": ["community"],
    "backend": "duckdb",
    "surface_as_source": true,
    "id_shape": "url",
    "structured_query": false
  }
]
```

Field meanings:

| Field | Meaning |
|---|---|
| `name` | The `<provider>` you put in the search path. |
| `duckdb` | On-disk store filename (`<name>.duckdb`). |
| `entity_types` | The entity types this store serves — these are the values you pass as `target`. |
| `backend` | `vector` (has a Qdrant collection → semantic search) or `duckdb` (SQL-only / lexical search). |
| `surface_as_source` | Whether the Hub shows it as a "Sources" tile. Today this is `true` only for the nine `gitlab_*` stores and `zenodo_communities`. |
| `id_shape` | Shape of the canonical id (`url`). |
| `structured_query` | Whether the store also offers a structured/faceted SQL surface. Today this is `true` only for `snsf` (see its `/grants` endpoints below). |

Filter to only the stores meant to surface as Hub "Sources" tiles (vector-backed plus allowlisted DuckDB-only):

```bash
curl -s "http://localhost:8000/v2/manifest?sources=true" \
  -H "Authorization: Bearer $GME_TOKEN"
```

## Run a semantic search — `POST /v2/indices/<provider>/search`

One uniform request body works for every index. Replace `<provider>` with an index name from the manifest.

### Request body (`IndexSearchRequest`)

| Field | Type | Default | Notes |
|---|---|---|---|
| `query` | string | — | **Required.** Free-text query. Must be non-empty (`min_length=1`). |
| `top_k` | int | `10` | Max results to return. Range `1`–`200`. |
| `candidate_k` | int / null | `null` | Vector-search candidate pool size *before* reranking. Indices that don't rerank ignore it. When omitted, rerank-capable indices fall back to `max(top_k * 5, 50)`. Range `1`–`1000`. |
| `filter_payload` | object / null | `null` | Optional metadata filter. **Shape is index-specific** — a Qdrant filter for most indices; a ChromaDB-style `where` clause for `ethz_research_collection`. |
| `target` | string / null | `null` | Entity type / collection to search within, for multi-entity indices. Single-entity indices ignore it. Valid values per index = that index's `entity_types` in the manifest. |

!!! warning "Unknown fields are rejected"
    The body is strict (`extra="forbid"`). Sending a field that isn't one of the five above returns `422 Unprocessable Entity`. There are no other filter parameters — `filter_payload` is the only filtering hook, and its contents are passed straight through to the underlying store.

### Example: search GitHub repositories

```bash
curl -s -X POST http://localhost:8000/v2/indices/github_repos/search \
  -H "Authorization: Bearer $GME_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "scientific Python data pipeline",
    "top_k": 5
  }'
```

### Response (`IndexSearchResponse`)

The envelope wraps a list of hits:

```json
{
  "index_name": "github_repos",
  "target": null,
  "query": "scientific Python data pipeline",
  "hits": [
    {
      "id": "https://github.com/sdsc-ordes/gimie",
      "vector_score": 0.71,
      "rerank_score": 0.93,
      "payload": {
        "full_name": "sdsc-ordes/gimie",
        "description": "Extract structured metadata from git repositories",
        "language": "Python"
      },
      "entity": {
        "url": "https://github.com/sdsc-ordes/gimie",
        "name": "gimie"
      }
    }
  ]
}
```

Envelope fields:

| Field | Meaning |
|---|---|
| `index_name` | Echoes the provider you queried. |
| `target` | The resolved entity type/collection (echoes your `target`, or the index default). |
| `query` | Echoes your query. |
| `hits` | List of result rows (below). |
| `extra` | Index-specific extras (e.g. HuggingFace facets, ETHZ Research Collection related persons/orgs). Omitted when empty. |

Each hit (`IndexSearchHit`):

| Field | Type | Meaning |
|---|---|---|
| `id` | string | Canonical id of the matched entity (usually a URL). |
| `vector_score` | float / null | Raw cosine similarity from Qdrant. |
| `rerank_score` | float / null | Cross-encoder rerank score, when the index reranks. **When present, this is the field you sort by** — it reflects the final ordering. |
| `payload` | object | The stored Qdrant payload for the matched point. |
| `entity` | object / null | The fuller canonical record (when the index resolves one). |

!!! note "Scores are per-index only"
    `vector_score` / `rerank_score` rank results *within a single index*. They are not comparable across different indices — there is no cross-index normalisation on the `/v2` API.

### Example: pick an entity type with `target`

Multi-entity indices (e.g. `openalex`, the HuggingFace families exposed as separate providers, `infoscience`, `ethz_research_collection`) use `target` to choose the collection. For OpenAlex, valid targets are `works` (default), `authors`, `institutions`, `sources`, `topics`, `concepts`:

```bash
curl -s -X POST http://localhost:8000/v2/indices/openalex/search \
  -H "Authorization: Bearer $GME_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "machine learning fairness",
    "target": "authors",
    "top_k": 3
  }'
```

```json
{
  "index_name": "openalex",
  "target": "authors",
  "query": "machine learning fairness",
  "hits": [
    {
      "id": "https://openalex.org/A5023888391",
      "vector_score": 0.62,
      "rerank_score": 0.88,
      "payload": {"display_name": "Jane Researcher", "works_count": 47},
      "entity": {"openalex_id": "A5023888391", "orcid": "0000-0001-9534-3870"}
    }
  ]
}
```

### Example: a bigger candidate pool + a metadata filter

`candidate_k` widens the vector recall before reranking; `filter_payload` narrows by metadata. The filter shape depends on the target store — verify against the index's own data before relying on a key.

```bash
curl -s -X POST http://localhost:8000/v2/indices/zenodo_records/search \
  -H "Authorization: Bearer $GME_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "query": "agricultural remote sensing dataset",
    "top_k": 10,
    "candidate_k": 100,
    "filter_payload": {"resource_type": "dataset"}
  }'
```

!!! note "`ethz_research_collection` filters are different"
    For `ethz_research_collection`, `filter_payload` is forwarded as a **ChromaDB-style `where` clause**, and the search mode is fixed to `hybrid`. Targets are `chunks` (default), `articles`, `persons`, `organizations`. For the other modes, use the standalone sidecar app directly (see below).

### Errors

| Status | When |
|---|---|
| `401` | Missing or invalid bearer token. |
| `422` | Body validation failed (empty `query`, out-of-range `top_k`, unknown field). |
| `503` | The index module isn't available on this deployment (`detail: "<index> index module unavailable on this deployment"`). |

## Which indices exist

The `<provider>` path segment is one of the names below (also returned by `GET /v2/manifest`). What each indexes is summarised here; see [`rag-indices.md`](rag-indices.md) for the full inventory.

**Native indices (own `/search` route):**

| Provider | Contains |
|---|---|
| `github_repos` | GitHub repositories + READMEs (EPFL/Swiss/gimie seed). |
| `github_users` | GitHub user cards — useful for disambiguating researchers by topic. |
| `github_organizations` | GitHub organisation cards. |
| `huggingface_models` | HuggingFace models. |
| `huggingface_datasets` | HuggingFace datasets. |
| `huggingface_spaces` | HuggingFace Spaces. |
| `huggingface_users` | HuggingFace user accounts. |
| `huggingface_organizations` | HuggingFace orgs. |
| `huggingface_papers` | HF-curated arXiv paper cards (daily Papers feed). |
| `openalex` | Scholarly works + `authors`, `institutions`, `sources`, `topics`, `concepts`. |
| `orcid` | ORCID persons (`persons | employments | educations`). |
| `zenodo_records` | Zenodo records (datasets, software, presentations, posters). |
| `renkulab` | RenkuLab `projects | groups | users | data_connectors`. |
| `swissubase` | swissUbase `studies | datasets | persons | institutions`. |
| `ethz_research_collection` | ETH Zürich DSpace publications (`chunks | articles | persons | organizations`). |
| `oamonitor` | OAM-CH open-access monitor (`journals | publications | publishers | organisations`). |
| `dockerhub` | Docker Hub images. |

**CLI-managed catalogs (populated by cron; `/search` only — no `/v2` ingest route):**

| Provider | Contains |
|---|---|
| `ror` | Research Organization Registry orgs (125k+). |
| `infoscience` | EPFL DSpace publications (`chunks | articles | persons | organizations`). |
| `snsf` | Swiss National Science Foundation grants. Also has faceted SQL at `GET /v2/indices/snsf/grants` and `.../grants/facets` (this is the one store with `structured_query: true` in the manifest). |
| `epfl_graph` | EPFL Graph academic-discipline ontology. |
| `zenodo_communities` | Institutional Zenodo communities registry. **Lexical (ILIKE) search**, not semantic — it's a small DuckDB-only registry. |

**GitLab family (full ingest + search):**

`gitlab_epfl_projects`, `gitlab_epfl_groups`, `gitlab_epfl_users`, `gitlab_ethz_projects`, `gitlab_ethz_groups`, `gitlab_ethz_users`, `gitlab_datascience_projects`, `gitlab_datascience_groups`, `gitlab_datascience_users` — projects/groups/users per GitLab instance (EPFL, ETHZ, Datascience).

```bash
# Example: GitLab EPFL projects
curl -s -X POST http://localhost:8000/v2/indices/gitlab_epfl_projects/search \
  -H "Authorization: Bearer $GME_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"query": "image segmentation", "top_k": 5}'
```

## Other surfaces: `/query`, `/predefined`, federated {#other-surfaces-query-predefined-federated}

These are **not** part of the `/v2` HTTP API. Knowing where they live saves you from calling routes that don't exist.

### `/search` vs `/query` — they're on different services

There is **no** `/query` route on the `/v2` API. The `/v2` API only does semantic `/search`.

Exact-match **SQL** querying (`/query`) and the list of canned queries (`/predefined`) exist only on the **standalone per-index sidecar apps** — separate FastAPI services (`src/index/<name>/api.py`) deployed alongside the main API, e.g. for OpenAlex, ORCID, Zenodo, GitHub repos, RenkuLab, swissUbase. On those apps:

| Route | Method | Purpose |
|---|---|---|
| `/search` | POST | Semantic search (same idea as `/v2`, but raw — returns a bare list of hit dicts, not the wrapped envelope). |
| `/query` | POST | Run SQL: `{"sql": "..."}` or `{"predefined": "<name>", "params": {...}}` (one or the other, not both → `400`). |
| `/predefined` | GET | List available predefined query names: `{"predefined": [...]}`. |
| `/healthz` | GET | Probe the index's DuckDB + Qdrant. |

Example against a sidecar app (host/port is deployment-specific):

```bash
# SQL via predefined query on the OpenAlex sidecar.
# `top_works_by_year` binds both $year and $limit, so pass both params.
curl -s -X POST http://openalex-sidecar:8000/query \
  -H "Content-Type: application/json" \
  -d '{"predefined": "top_works_by_year", "params": {"year": 2024, "limit": 20}}'

# Ad-hoc SQL (the works table's year column is `publication_year`)
curl -s -X POST http://openalex-sidecar:8000/query \
  -H "Content-Type: application/json" \
  -d '{"sql": "SELECT title, publication_year FROM works WHERE publication_year = 2024 LIMIT 20"}'
```

!!! note "Sidecars are internal"
    The sidecar apps are deployed as internal services. Whether they're reachable, and on what host/port, depends on the deployment. For most consumers the `/v2` API is the public surface; reach for a sidecar only when you need SQL or a predefined query.

### Federated cross-index search

Querying all indices at once (merge-by-score across HuggingFace, OpenAlex, ORCID, ROR, Zenodo, GitHub, …) is **CLI-only** today — there is no federated HTTP endpoint on the `/v2` API. It runs as `gme search` / `gme entity` and is also wired into the v2 LLM agents (`search_federated_rag`, `lookup_entity_federated`). See [`federated-search.md`](federated-search.md) for the federated layer, its `{"hits":[...], "by_index":{...}, "errors":{...}}` shape, and cross-index entity lookup.

If you need cross-index results over HTTP, the supported path is to call several `/v2/indices/<provider>/search` endpoints yourself and merge client-side — but remember scores aren't comparable across indices.

## Health check (no auth)

```bash
curl -s http://localhost:8000/v2/health
```

```json
{
  "status": "healthy",
  "components": {"python": "healthy", "config": "healthy", "github_token": "degraded"},
  "version": "x.y.z"
}
```

`status` is the worst of the component statuses (`healthy` / `degraded` / `unhealthy`).

## Related pages

- [`rag-indices.md`](rag-indices.md) — full index inventory and what each contains.
- [`federated-search.md`](federated-search.md) — cross-index `gme search` / `gme entity` (CLI).
