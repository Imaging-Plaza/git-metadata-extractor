# Zenodo Index

Standalone RAG index over Zenodo records (datasets, software releases,
presentations, posters, papers) cited by EPFL / Swiss research outputs.
Lives at `src/index/zenodo/`.

> **Two complementary views.** [`docs/v2-rag-tools.md`](v2-rag-tools.md)
> documents the **agent-facing** tools (`search_zenodo_rag`,
> `fetch_zenodo_records`) used by the v2 LLM pipeline. *This* page
> documents the **direct-access** CLI, ingest pipeline, federated
> adapter, and the citation-driven discovery path.

## Architecture

DuckDB at `data/index/zenodo/duckdb/zenodo.duckdb` holds the canonical
records (one row per Zenodo deposit) plus normalised creator,
community, and file tables. Qdrant collection `zenodo_records` holds
the embedding vectors — one point per chunk, identified by
`uuid5(NAMESPACE_URL, "records|<zenodo_id>|<chunk_index>")`. Embeddings
come from `Qwen/Qwen3-Embedding-8B` on EPFL RCP; reranking from
`Qwen/Qwen3-Reranker-8B`.

```
┌─ Discover (citation-driven) ──────────────────────────────────────────┐
│  data/index/infoscience/text/*.txt  →  scan for `zenodo.org/{id}` /   │
│  `10.5281/zenodo.{id}`  →  diff against records.zenodo_id and         │
│  records.concept_recid                                                │
└──────┬────────────────────────────────────────────────────────────────┘
       ▼
┌─ Ingest ──────────────────────────────────────────────────────────────┐
│  Zenodo REST  /api/records/{id}  (or  /api/records?communities=…)     │
│       │                                                               │
│       ▼                                                               │
│  DuckDB: records / creators / communities / record_creators /         │
│          record_communities / files                                   │
└──────┬────────────────────────────────────────────────────────────────┘
       ▼
┌─ Embed ───────────────────────────────────────────────────────────────┐
│  DuckDB rows  →  chunker (256 tok, 64 overlap) →  RCP /v1/embeddings  │
│       │                                            →  Qdrant          │
│       ▼                                                               │
│  bookkeeping: chunks(chunk_id, entity_type, entity_id, chunk_index, …)│
└──────┬────────────────────────────────────────────────────────────────┘
       ▼
┌─ Query ───────────────────────────────────────────────────────────────┐
│  semantic: query → embed → Qdrant → optional rerank → thin hits       │
│  hydrate: ids (zenodo_id OR concept_recid) → DuckDB → full record     │
│  federated: same query fans out across all gme indices in parallel    │
└───────────────────────────────────────────────────────────────────────┘
```

## Two ingest modes

### A. Community-driven (the default)

Pulls every record published under a Zenodo community. The community
slugs are **not** hardcoded here — `ingest --scope <name>` resolves them
from the [communities index](communities-index.md) by `parent_org`:

| Scope | Communities resolved |
|---|---|
| `epfl` | every community with `parent_org = epfl` |
| `ethz` | `parent_org = ethz` |
| `cern` | `parent_org = cern` |
| `cern_openlab` | `parent_org = cern_openlab` |
| `switzerland` | `epfl` + `ethz` combined |
| `all` | every community in the index |

```bash
python -m src.index.communities.cli build   # populate the communities index first
python -m src.index.zenodo ingest --scope epfl
python -m src.index.zenodo ingest --scope cern
```

Each persisted record is stamped with the community it was crawled
under (`primary_community_id`) and the full set it belongs to
(`community_ids`). Resumable via
`data/index/zenodo/state/ingest_<scope>.json` — completed community
slugs are skipped on re-run unless `--refresh` is set.

### B. Citation-driven (the broader signal)

Most Zenodo deposits cited in EPFL publications are **not** under the
`epfl` community — they're external software (LMFIT, Yade, py-sphviewer)
or per-lab data deposits (`petersen-lab-data`, `holtmaat-lab-data`,
`mobilise-d`, `impresso`, `spi-ace`, …) that the community filter
misses entirely. The `discover` subcommand mines the local Infoscience
full-text dump for `zenodo.org/{id}` / `10.5281/zenodo.{id}` references
and emits the IDs we don't already have:

```bash
# Discover only (prints summary):
python -m src.index.zenodo discover --source infoscience

# Discover + dump full payload (ID list + community evidence):
python -m src.index.zenodo discover --source infoscience \
    --out data/index/zenodo/state/discovered_from_infoscience.json

# Discover + ingest in one go:
python -m src.index.zenodo discover --source infoscience --ingest
```

The diff against existing records is performed against **both**
`records.zenodo_id` (canonical version-record) and
`records.concept_recid` (Zenodo's "all versions" parent id) — this
prevents re-fetching the same deposit just because a citation referenced
the concept rather than a specific version (Zenodo's `/api/records/{id}`
follows redirects from concept → latest version, and we persist under
the canonical version's id).

Direct ingest by an arbitrary list of IDs / DOIs / URLs:

```bash
python -m src.index.zenodo ingest --ids my_ids.txt
```

The token normaliser accepts:

| Input | Resolved id |
|---|---|
| `3909400` | `3909400` |
| `10.5281/zenodo.3909400` | `3909400` |
| `https://zenodo.org/records/3909400` | `3909400` |
| `https://doi.org/10.5281/zenodo.3909400` | `3909400` |

## Schema

```sql
records (
    zenodo_id          TEXT PRIMARY KEY,   -- canonical version-record id (post-redirect)
    concept_recid      TEXT,                -- Zenodo concept-record (groups all versions)
    doi                TEXT,
    title              TEXT,
    description        TEXT,                -- HTML-stripped
    publication_date   DATE,
    resource_type      TEXT,                -- e.g. publication-article, dataset, software
    access_right       TEXT,                -- open | embargoed | restricted | closed
    license_id         TEXT,
    keywords_json      JSON,
    community_ids        JSON,              -- every community slug this record belongs to
    primary_community_id TEXT,              -- the community it was crawled under (see communities index)
    raw                JSON,                -- full Zenodo API payload
    ingested_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
)

creators (creator_key TEXT PRIMARY KEY, display_name, orcid, affiliation, raw, ingested_at)
record_creators (record_id, creator_key, position)
communities (community_id TEXT PRIMARY KEY, title, raw, ingested_at)
record_communities (record_id, community_id)
files (record_id, file_key, file_id, size_bytes, checksum, download_url)
chunks (chunk_id, entity_type, entity_id, chunk_index, text, token_count, vector_id, embedded_at)
```

`ZenodoStore.bootstrap()` is idempotent and self-migrates older DBs:
on open, it adds `concept_recid` if missing, then back-fills it from
`raw.conceptrecid` for every existing row.

## CLI subcommands

| Subcommand | Purpose |
|---|---|
| `discover` | Find candidate Zenodo IDs from external sources (currently `--source infoscience`); optional `--ingest` chains directly into the per-id ingest |
| `ingest` | Pull records into DuckDB. `--scope <name>` walks Zenodo communities; `--ids <file>` bulk-fetches a list of IDs / DOIs / URLs |
| `embed` | Chunk + embed records with no chunks yet, push vectors to Qdrant |
| `search` | Semantic retrieval (vector + RCP rerank). Mirrored by `just zenodo-search` |
| `query` | Read-only DuckDB SQL — predefined queries (`records_by_author`, …) or guarded ad-hoc |
| `status` | Row counts + Qdrant point count + paths |
| `serve` | Run the FastAPI app (port 8003) |

## Federated layer

Zenodo registers a `ZenodoAdapter` at
`src/index/_federated/adapters/zenodo.py`, exposing two operations to
the federated layer:

- **`search`** — wraps `semantic_search()` from
  `src/index/zenodo/retrieval/semantic.py`. Returns `Hit` objects with
  `index="zenodo"`, `entity_type="zenodo_record"`, score, summary, URL.
- **`lookup`** — recognises identifier strings (numeric Zenodo id,
  `10.5281/zenodo.<id>` DOI, `https://zenodo.org/records/<id>` URL).
  Tries `fetch_record(id)` first, then falls back to
  `fetch_record_by_concept(id)` so a cited concept-recid still resolves
  to the canonical version-record we have on disk.

```bash
gme search "Yade granular dynamics" --indices zenodo --top-k 5
gme entity 10.5281/zenodo.3909400 --indices zenodo
```

## Agent tools (v2 LLM pipeline)

Wired into the **article agent**:

| Tool | Purpose |
|---|---|
| `search_zenodo_rag(query, top_k, filters?, rerank?)` | Vector search the `zenodo_records` Qdrant collection. Returns thin hits (`zenodo_id`, `title`, `doi`, `year`, `resource_type`, `access_right`). |
| `fetch_zenodo_records(ids)` | Hydrate full records from DuckDB by `zenodo_id` **or** `concept_recid`. Returns full body (title, doi, description capped at 2000 chars, license, access_right, dates). |

Tools are gated by `V2_ZENODO_RAG_ENABLED` (default `true`) and
construct lazily — if Qdrant or RCP is unreachable, the provider builder
returns `None` and the tools simply aren't attached.

## Configuration

The Zenodo index reads `config/index/zenodo.yaml`. Notable env vars
(merged at `load_config()`):

| Var | Default | Purpose |
|---|---|---|
| `ZENODO_TOKEN` | unset | optional bearer token. Anonymous is rate-limited to 25/min and `page_size ≤ 25`. |
| `RCP_TOKEN` | — | required for embed / search / rerank |
| `INDEX_QDRANT_URL` | `http://gme-qdrant:6333` (yaml default) | Qdrant endpoint |
| `INDEX_QDRANT_API_KEY` | unset | passed through if your Qdrant requires it |
| `INDEX_DATA_DIR` | `data/` | overrides where DuckDB / state files live |
| `V2_ZENODO_RAG_ENABLED` | `true` | flip to `false` to detach the agent tools |

## Persisted state files

```
data/index/zenodo/
  duckdb/zenodo.duckdb                              # canonical store
  state/
    ingest_epfl.json                                # community-completion cursor
    discovered_from_infoscience.json                # last `discover --out` payload
    ids_ingest_summary.json                         # last `ingest --ids` summary (audit)
```

## Status

```bash
python -m src.index.zenodo status
```

Prints DuckDB row counts (`records`, `creators`, `record_creators`,
`communities`, `record_communities`, `files`, `chunks`), the Qdrant
collection name + point count, and the configured EPFL community list.
