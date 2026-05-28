# SWISSUbase Index

Standalone RAG index over the Swiss social-science research-data
platform [`swissubase.ch`](https://www.swissubase.ch) — run by FORS,
UZH, U. Neuchâtel, and DASCH. Indexes the platform's four entity types:
**studies** (UI label: "Project"), **datasets**, **persons**, and
**institutions**. Lives at `src/index/swissubase/`.

> **Two complementary views.** [`docs/v2-rag-tools.md`](v2-rag-tools.md)
> documents the **agent-facing** tool (`search_swissubase_rag`) used by
> the v2 LLM pipeline. *This* page documents the **direct-access** CLI,
> SQL, and HTTP surfaces — for analysts and scripts that don't want to
> go through an agent.

## Why the ingest is Selenium-driven

swissUbase has **no documented public REST API**. Every catalogue
endpoint is path-prefixed `/api/public/...` but enforces two things:

1. A session cookie set by the SPA via `/api/v2/actions/base` on first
   page load (anonymous `curl` is rejected with `403 Not authorized`).
2. A quirky `Accept: q=0.8;application/json;q=0.9` header (anything
   else 4xx's, even from inside the browser session).

We therefore open a long-lived `webdriver.Remote` session against the
shared Selenium Grid (`SELENIUM_REMOTE_URL`), navigate once to the
catalogue search page so the browser obtains the cookies, then invoke
the JSON API endpoints from inside the browser via
`driver.execute_async_script(fetch(...))`. Same browser session, full
session-cookie attachment, but JSON in / JSON out — much faster than
re-rendering the Material table for every study.

## Why the ingest enumerates `studyVersionId` (not the search endpoint)

The catalogue's `search-studies` endpoint **silently caps deep
pagination**:

| pagesize | items reachable | last working `start` |
|---:|---:|---:|
| 50 | ~250 | 201 |
| 20 | ~420 | 401 |
| 10 | ~1010 | 1001 |
|  5 | ~1005 | 1001 |

The cap survives both filtering (single + multi-facet) and session
rotation; even `start=251 pagesize=10` under `language=en` (total=1909)
returns `{items: [], total: 0}`. So we cannot enumerate the full ~12 k
catalogue via search alone.

Per-study endpoints (`/api/public/catalogue/studies/v1/{id}/...`) have
no such cap. They accept any `studyVersionId`:

* **200** → return full overview / main / dynamic-blocks JSON.
* **404** → study doesn't exist for that ID.
* **403** → ID exists but isn't public; skip silently.

So the ingest just iterates `studyVersionId ∈ [1..25000]` (configurable
range; observed live range is 1..21,500 with ~58 % density). Every
existing study returns 200 in one shot — no search needed.

## Architecture (1-paragraph version)

DuckDB at `data/index/swissubase/duckdb/swissubase.duckdb` holds the
canonical records — `studies`, `datasets`, `persons`, `institutions`,
plus M:N bridge tables `study_persons` (with role) and
`study_institutions`, plus a unified `chunks` ledger. Qdrant (compose
service `gme-qdrant:6333`) holds embedding vectors in a single
collection `swissubase_entities`; the `entity_type` payload field
disambiguates studies vs datasets vs persons vs institutions, so one
search-tool call can return mixed-type hits. Embeddings come from
`Qwen/Qwen3-Embedding-8B` on EPFL RCP; reranking from
`Qwen/Qwen3-Reranker-8B`. Every entity row stores `source_url`
(`https://www.swissubase.ch/{lang}/catalogue/studies/{id}`) — `NOT NULL`
for studies and datasets, populated when known for persons and
institutions.

```
┌─ Ingest ──────────────────────────────────────────────────────────────┐
│  Selenium session ─→ for sid in [id_start..id_end]:                   │
│                       overview-block + main + dynamic-blocks          │
│                       └─→ project → studies / persons / institutions  │
│                       (404/403 skip; transient errors retry-skip)     │
│                                                                       │
│  scope filter: affiliation_match=TRUE iff institution string matches  │
│  EPFL / ETH Zurich / ETHZ / SDSC                                      │
└──────┬────────────────────────────────────────────────────────────────┘
       ▼
┌─ Embed ───────────────────────────────────────────────────────────────┐
│  rows where affiliation_match  →  chunker  →  RCP /v1/embeddings      │
│        →  Qdrant collection swissubase_entities                       │
│  bookkeeping: chunks(chunk_id, entity_type, entity_id, chunk_index, …)│
└──────┬────────────────────────────────────────────────────────────────┘
       ▼
┌─ Query ───────────────────────────────────────────────────────────────┐
│  semantic: query → embed → Qdrant → rerank → entity-dedup → hydrate   │
│  sql:     guarded SELECT/WITH over DuckDB                             │
└───────────────────────────────────────────────────────────────────────┘
```

## Entity model

| Table | Primary key | Notes |
|---|---|---|
| `studies` | `study_id` (= `studyVersionId`) | One row per swissUbase Project. Carries `ref` (referenceNumber the UI shows), `title`, `description`, `description_language`, `start_date`, `end_date`, `progress`, `main_discipline` / `sub_discipline`, `version`, `data_availability`, `dataset_count`, `affiliation_match`, `source_url` (NOT NULL). Full overview + dynamic-blocks JSON kept in `raw_overview` / `raw_dynamic_blocks`. |
| `datasets` | `dataset_id` | Currently stubbed (we don't yet harvest the per-study `Datasets` dynamic block). Schema is in place; the embed pipeline already streams from this table. |
| `persons` | `person_key` (`swissubase:person:{personId}` or `name:{slug}`) | Authors, principal investigators, former collaborators. `display_name`, `orcid` (currently always null — swissUbase doesn't expose ORCIDs at this layer), `affiliation`, `source_url` (often null). |
| `institutions` | `institution_key` (`name:{slug}` until a ROR match is computed) | `name`, `address`, `ror_id` (currently null), `source_url`. |
| `study_persons` | `(study_id, person_key, role)` | Composite — same person can have multiple roles per study. `role ∈ {Principal investigator, Author, Former collaborator}`. `position` from the swissUbase `sort` field. |
| `study_institutions` | `(study_id, institution_key)` | M:N. |
| `chunks` | `chunk_id` (`uuid5(NAMESPACE_URL, "{entity_type}|{entity_id}|{index}")`) | Bookkeeping — one row per Qdrant point, `chunk_id == vector_id`. |

## Scope filter

The catalogue has no institution facet, so the scope is applied as a
**post-filter on the rendered institution string**. Each study is
matched (case-insensitively) against the patterns in
`scope.epfl_sdsc_ethz_patterns`:

```
EPFL · École polytechnique fédérale de Lausanne ·
Ecole polytechnique fédérale de Lausanne ·
ETH Zurich · ETHZ · Eidgenössische Technische Hochschule ·
SDSC · Swiss Data Science Center
```

Studies that match get `affiliation_match=TRUE` and are the **only**
ones embedded by default. Override at runtime with
`INDEX_SWISSUBASE_SCOPE=switzerland` to flag every study and embed the
full ~13 k Swiss social-science catalogue.

Storage is unconditional — even non-matching studies land in DuckDB —
so flipping scope later doesn't require re-scraping. Only a re-embed.

## CLI

```bash
just swissubase-status                       # row counts + Qdrant collection size + paths
just swissubase-ingest                       # full id-range scan, resumable via state checkpoint
just swissubase-ingest --refresh             # restart from id=id_start, ignoring checkpoint
just swissubase-ingest --limit 50            # smoke test
just swissubase-ingest --scope switzerland   # mark every ingested study in-scope

just swissubase-embed                                        # all four entity types
just swissubase-embed --entity studies --entity persons      # subset
just swissubase-embed --limit 100

just swissubase-search "language teacher education" --top-k 5
just swissubase-search "..." --filter '{"entity_type":"persons"}'
just swissubase-search "..." --filter '{"main_discipline":"Humanities and Social Sciences"}'

just swissubase-query --predefined count_by_entity
just swissubase-query --predefined in_scope_studies --param limit=20
just swissubase-query --predefined studies_by_institution --param institution_key=name:epfl --param limit=10
just swissubase-query --predefined top_institutions_in_scope --param limit=15
just swissubase-query --predefined studies_by_discipline
just swissubase-query --sql "SELECT main_discipline, COUNT(*) AS n FROM studies WHERE affiliation_match GROUP BY main_discipline"

just swissubase-serve --port 8004            # FastAPI on :8004
```

## HTTP API

`just swissubase-serve` boots `src/index/swissubase/api.py` (FastAPI).

| Method | Path | Purpose |
|---|---|---|
| GET | `/healthz` | DuckDB + Qdrant + RCP + Selenium status |
| POST | `/search` | `{query, top_k, candidate_k, filter_payload}` → semantic search |
| POST | `/query` | `{predefined?, sql?, params?}` — predefined or guarded SELECT/WITH |
| GET | `/predefined` | List predefined query names |
| GET | `/study/{study_id}` | Full study row (joins not hydrated) |
| GET | `/dataset/{dataset_id}` | Full dataset row |

## Predefined queries

| Name | Returns |
|---|---|
| `count_by_entity` | One row per entity bucket (studies, in-scope studies, persons, …) |
| `in_scope_studies` | EPFL / ETHZ / SDSC studies, latest-first |
| `studies_by_institution` | Studies linked to a given `institution_key` |
| `studies_by_person` | Studies a given `person_key` was on, with role |
| `top_institutions_in_scope` | Institution leaderboard (in-scope only) |
| `top_persons_in_scope` | Person leaderboard (in-scope only) |
| `studies_by_discipline` | Discipline distribution (in-scope only) |

## Configuration

Static settings live in [`config/index/swissubase.yaml`](https://github.com/Imaging-Plaza/git-metadata-extractor/blob/main/config/index/swissubase.yaml). Runtime overrides:

| Env | Default | Purpose |
|---|---|---|
| `SELENIUM_REMOTE_URL` | — | **Required.** Selenium Grid (Firefox). |
| `RCP_TOKEN` | — | Required for `embed` + `search`. |
| `INDEX_QDRANT_URL` | yaml `gme-qdrant:6333` | Qdrant endpoint (use `localhost:6333` from a host shell). |
| `INDEX_QDRANT_API_KEY` | unset | Qdrant API key (when applicable). |
| `INDEX_SWISSUBASE_SCOPE` | `epfl_sdsc_ethz` | `epfl_sdsc_ethz` or `switzerland`. |
| `INDEX_SWISSUBASE_ID_START` | yaml `1` | Lower bound of the studyVersionId scan. |
| `INDEX_SWISSUBASE_ID_END` | yaml `25000` | Upper bound. Bump if swissUbase grows past id 21,500. |
| `INDEX_DATA_DIR` | `data/index` | Filesystem root. |
| `V2_SWISSUBASE_RAG_ENABLED` | `true` | Disable to drop the v2 agent tool without removing the index. |

## Federated layer

`src/index/_federated/adapters/swissubase.py` exposes the index to
`gme-search` / `gme-entity`:

```bash
just gme-search "language teacher education" --indices swissubase --top-k 5
just gme-entity https://www.swissubase.ch/en/catalogue/studies/21160
just gme-entity 21160                                 # bare id → studies
just gme-entity swissubase:person:11073               # composite key
```

The adapter:

* `search()` — pushes the optional `entity_type` argument into the
  Qdrant filter as `entity_type=...`, scopes to a single bucket. With
  `entity_type=None` (the default), all four buckets are searched
  together.
* `lookup()` — recognises swissUbase study URLs, bare numeric
  `studyVersionId`s (tries studies first, then `swissubase:person:N`),
  composite person/institution keys.

## V2 LLM tool

`search_swissubase_rag` (factory: `make_swissubase_rag_search_tool`,
provider: `SwissubaseRagProvider`).

| Argument | Notes |
|---|---|
| `query` | Free-text. |
| `top_k` | 1..100, default 10. |
| `filters` | Allowlisted: `entity_type, study_id, dataset_id, person_key, institution_key, ref, main_discipline, sub_discipline, progress, year_start, year_end, access_right`. |
| `rerank` | `false` (default) — vector only. `true` — vector + RCP cross-encoder against title+discipline. |

Wired into the **article agent** today (matches zenodo's placement).
Mirror the same two-line edit (import + `if providers.swissubase_rag is not None: tools.append(...)`) to extend to the person / organization / repository agents.

Returns thin hits with a top-level `source_url` so downstream consumers
have the canonical link without a second fetch.

## Limitations & follow-ups

- **Datasets ingest is stubbed.** swissUbase loads a study's datasets
  as a separate "dynamic block" served by per-block endpoints we
  haven't reverse-engineered. The schema, embed path, federated
  adapter, and predefined queries are all ready — just needs the
  per-block fetch + projection.
- **No ORCID / ROR enrichment.** Persons carry `personId` and
  `refNumber` but no ORCID; institutions carry no ROR ID. A follow-up
  pass could fuzzy-match against the ORCID and ROR indices already in
  this repo.
- **Polite single-thread ingest.** ~6 hours for the full 25,000-ID
  range. Multi-session parallelism (one Selenium driver per worker
  thread) is feasible but not implemented.
