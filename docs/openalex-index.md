# OpenAlex Index

Standalone RAG index over the OpenAlex graph (works, authors,
institutions, sources, topics, concepts) scoped to EPFL / Switzerland /
arbitrary ROR. Lives at `src/index/openalex/`.

> **Two complementary views.** [`docs/v2-rag-tools.md`](v2-rag-tools.md)
> documents the **agent-facing** tool (`search_openalex_rag`) used by
> the v2 LLM pipeline. *This* page documents the **direct-access** CLI,
> ingest pipeline, schema, federated adapter, and the citation-graph
> companion table.

## Architecture

DuckDB at `data/index/openalex/duckdb/openalex.duckdb` holds canonical
entity rows (one per `openalex_id`) plus a set of normalised linking
tables (`work_authors`, `work_institutions`, `work_github_urls`,
`work_references`). Qdrant collections — one per entity type
(`works`, `authors`, `institutions`, `sources`, `topics`, `concepts`)
— hold the embedding vectors. Embeddings come from
`Qwen/Qwen3-Embedding-8B` on EPFL RCP; reranking from
`Qwen/Qwen3-Reranker-8B`.

```
┌─ Ingest ──────────────────────────────────────────────────────────────┐
│  pyalex (filter chaining + select projection + retry)  →  OpenAlex    │
│  REST   /works  /authors  /institutions  /sources  /topics            │
│         /concepts                                                     │
│       │                                                               │
│       ▼                                                               │
│  DuckDB: works / authors / institutions / sources / topics /          │
│          concepts + work_authors / work_institutions /                │
│          work_github_urls / work_references                           │
└──────┬────────────────────────────────────────────────────────────────┘
       ▼
┌─ Discover (GitHub URLs) ─────────────────────────────────────────────┐
│  works.abstract / fulltext_origin  →  regex github.com/<owner>/<repo>│
│       │                                                               │
│       ▼                                                               │
│  DuckDB: work_github_urls (citing work → repo, with source provenance)│
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
│  hydrate: openalex_id → DuckDB → full record                          │
│  federated: same query fans out across all gme indices in parallel    │
└───────────────────────────────────────────────────────────────────────┘
```

## Scopes

The ingest CLI takes `--scope {epfl,switzerland}`. The two scopes apply
different OpenAlex filters:

| Scope | Works filter | Authors filter | Institutions filter |
|---|---|---|---|
| `epfl` | `authorships.institutions.ror = <ROR>` | `affiliations.institution.ror = <ROR>` | `ror = <ROR>` |
| `switzerland` | `authorships.institutions.country_code = <CC>` | `last_known_institutions.country_code = <CC>` | `country_code = <CC>` |

Defaults come from `config/index/openalex.yaml`:

```yaml
scope:
  ror: https://ror.org/02s376052      # EPFL ROR
  country: ch                          # Switzerland
```

### Reusing `--scope epfl` for any institution

Set `INDEX_OPENALEX_SCOPE_ROR` to override the ROR at runtime. Same DB,
same paths — just a different filter. Useful for adding a partner
institution's corpus on top of an existing scope:

```bash
# add SDSC works/authors to the existing DuckDB
INDEX_OPENALEX_SCOPE_ROR=https://ror.org/02hdt9m26 \
  python -m src.index.openalex.cli ingest --scope epfl --entities works,authors

# add ETH Zurich works
INDEX_OPENALEX_SCOPE_ROR=https://ror.org/05a28rw58 \
  python -m src.index.openalex.cli ingest --scope epfl --entities works,authors
```

All upserts are idempotent (`ON CONFLICT DO UPDATE/NOTHING`), so
overlapping works (e.g. EPFL × ETHZ co-authored papers) refresh
cleanly without duplication.

`INDEX_OPENALEX_SCOPE_COUNTRY` overrides the country filter the same
way.

## Schema

```sql
works (
    openalex_id        TEXT PRIMARY KEY,
    doi                TEXT,
    title              TEXT,
    abstract           TEXT,                -- reconstructed from inverted-index
    publication_year   INTEGER,
    primary_topic_id   TEXT,                -- FK → topics
    primary_source_id  TEXT,                -- FK → sources
    raw                JSON,                -- full OpenAlex payload (subject to WORKS_PROJECTION)
    ingested_at        TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
)

authors (
    openalex_id                 TEXT PRIMARY KEY,
    display_name                TEXT,
    orcid                       TEXT,
    last_known_institution_id   TEXT,        -- FK → institutions (current institution)
    raw                         JSON,
    ingested_at                 TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
)

institutions (openalex_id PK, ror, display_name, country_code, raw, ingested_at)
sources      (openalex_id PK, issn_l, display_name, type, raw, ingested_at)
topics       (openalex_id PK, display_name, domain_id, field_id, raw, ingested_at)
concepts     (openalex_id PK, display_name, level, raw, ingested_at)

-- Linking tables
work_authors      (work_id, author_id, position) PK (work_id, author_id)
work_institutions (work_id, institution_id) PK (work_id, institution_id)
work_github_urls  (work_id, url, normalized_url, owner, repo, source, found_at)
                  PK (work_id, normalized_url)
                  source ∈ {'abstract', 'fulltext'}

-- Backward citation graph (one row = "citing cites cited")
work_references (citing_work_id, cited_work_id, position)
                PK (citing_work_id, cited_work_id)

chunks (chunk_id, entity_type, entity_id, chunk_index, text, token_count, vector_id, embedded_at)
```

`DuckDBStore.bootstrap()` runs `schema.sql` on every `open()`; all
statements are `IF NOT EXISTS`, so re-runs are safe.

### `WORKS_PROJECTION` and `referenced_works`

The ingest path narrows the OpenAlex API response via a `select=`
projection (`src/index/openalex/models.py::WORKS_PROJECTION`). The
projection includes `referenced_works` so `works.raw.referenced_works`
is populated for newly-ingested works. **Older DuckDBs ingested before
this field was added** can be backfilled with a one-shot script that
batches missing IDs through `/works?filter=ids.openalex:W1|W2|…&select=id,referenced_works`
and populates `work_references` directly — no full re-ingest needed.

## CLI subcommands

| Subcommand | Purpose |
|---|---|
| `ingest` | Pull entities into DuckDB. `--scope {epfl,switzerland}` selects the filter. `--entities` defaults to all six (`works,authors,institutions,sources,topics,concepts`). |
| `find-github` | Discover GitHub URLs in `works.abstract` / `fulltext_origin`; persists matches to `work_github_urls` with provenance |
| `embed` | Chunk + embed entity rows with no chunks yet, push vectors to Qdrant. Idempotent — already-chunked rows are skipped via `stream_rows_for_embedding`. |
| `rebuild-qdrant` | Re-push existing `chunks` rows to Qdrant (re-embedding `chunks.text`). Use after a Qdrant wipe; does not touch DuckDB. |
| `search` | Semantic retrieval (vector + RCP rerank). |
| `query` | Read-only DuckDB SQL — predefined queries or guarded ad-hoc. |
| `serve` | Run the FastAPI app (port 8001). |

## Federated layer

OpenAlex registers an `OpenAlexAdapter` at
`src/index/_federated/adapters/openalex.py`, exposing two operations to
the federated layer:

- **`search`** — wraps `semantic_search()` from
  `src/index/openalex/retrieval/semantic.py`. Returns `Hit` objects
  with `index="openalex"`, `entity_type` ∈ {work, author, institution,
  source, topic, concept}, score, summary, URL.
- **`lookup`** — recognises identifier strings (full OpenAlex URL,
  short ID `W…`/`A…`/`I…`/`S…`/`T…`/`C…`, DOI, ROR, ORCID).

```bash
gme search "machine learning fairness" --indices openalex --top-k 5
gme entity W2741809807                            # OpenAlex Work
gme entity 10.1038/s41586-021-03819-2             # by DOI
```

## Agent tools (v2 LLM pipeline)

Wired into the **repository, person, organization, and article agents**:

| Tool | Purpose |
|---|---|
| `search_openalex_rag(query, collection, top_k, filters?, rerank?)` | Vector search any of the 6 Qdrant collections (`works`, `authors`, `institutions`, `sources`, `topics`, `concepts`). Returns thin hits hydrated from DuckDB. |

Tool is gated by `V2_OPENALEX_RAG_ENABLED` (default `true`) and
constructs lazily — if Qdrant or RCP is unreachable, the provider
builder returns `None` and the tool simply isn't attached.

## Configuration

Reads `config/index/openalex.yaml`. Notable env vars (merged at
`load_config()`):

| Var | Default | Purpose |
|---|---|---|
| `OPENALEX_MAILTO` | — | required for the polite-pool API rate. Pyalex sends it as `?mailto=`. |
| `RCP_TOKEN` | — | required for embed / search / rerank |
| `INDEX_OPENALEX_SCOPE_ROR` | unset | override `scope.ror` at runtime (per-institution ingests) |
| `INDEX_OPENALEX_SCOPE_COUNTRY` | unset | override `scope.country` at runtime |
| `INDEX_QDRANT_URL` | `http://gme-qdrant:6333` | Qdrant endpoint |
| `INDEX_QDRANT_API_KEY` | unset | passed through if your Qdrant requires it |
| `INDEX_DATA_DIR` | `data/` | overrides where DuckDB / state files live |
| `V2_OPENALEX_RAG_ENABLED` | `true` | flip to `false` to detach the agent tool |

## Discovery: GitHub URLs from abstracts

`find-github` is a separate ingest pass that scans `works.abstract` and
`fulltext_origin` for `github.com/<owner>/<repo>` URLs and persists each
match to `work_github_urls` with a `source` provenance flag (`abstract`
or `fulltext`). Run after a fresh works ingest:

```bash
python -m src.index.openalex.cli find-github --scope switzerland \
  --search both
```

This populates the link from "papers that cite open-source code" → the
referenced repo, which downstream pipelines join against the GitHub
RAG index.

## Citation graph (`work_references`)

`work_references` is the normalised version of
`works.raw.referenced_works`. Each row is one backward-citation edge
(`citing` cites `cited`). Populated either:

1. Automatically — `referenced_works` is part of `WORKS_PROJECTION`, so
   new ingests stamp it into `works.raw` and a one-shot extractor can
   read it out. The extractor walks every row in `works`, parses
   `raw.referenced_works`, and bulk-inserts into `work_references`.
2. As a backfill — for older DuckDBs ingested before
   `referenced_works` was in the projection, the federated discover/
   hydrate pipeline batches the missing `openalex_id`s through
   `/works?filter=ids.openalex:…&select=id,referenced_works` (100 IDs
   per request). Run via:

   ```bash
   gme discover --source from-references --indices openalex --out missing-refs.jsonl
   gme hydrate missing-refs.jsonl --indices openalex
   ```

   The OpenAlex hydrator detects `hint.refs_only=true` and runs the
   batched fast path (only populates `work_references`, no work
   upsert). Full guide at [`discover-hydrate.md`](discover-hydrate.md).

Useful queries:

```sql
-- Most-cited works in our corpus (internal forward citations)
SELECT w.title, w.publication_year, COUNT(*) AS internal_cites
FROM work_references r JOIN works w ON w.openalex_id = r.cited_work_id
GROUP BY w.openalex_id, w.title, w.publication_year
ORDER BY internal_cites DESC LIMIT 20;

-- Average references per work by year (signal of indexing depth)
SELECT publication_year, AVG(n) FROM (
  SELECT w.publication_year, COUNT(r.cited_work_id) AS n
  FROM works w LEFT JOIN work_references r ON r.citing_work_id = w.openalex_id
  GROUP BY w.openalex_id, w.publication_year
) GROUP BY 1 ORDER BY 1;
```

The cited side may reference works **outside** our DB (most do — only
~2-5% of cited IDs are also citing works in a single-institution
corpus); the `idx_work_refs_cited` index keeps both directions of the
join fast.

## Persisted state files

```
data/index/openalex/
  duckdb/openalex.duckdb                          # canonical store
  cache/                                          # optional pyalex cache (rare)
  logs/                                           # ingest / embed run logs
```

## Status

```bash
python -m src.index.openalex.cli query --predefined counts
```

…or the equivalent `just openalex-status` recipe. Reports row counts
per entity table, per linking table, and per Qdrant collection.
