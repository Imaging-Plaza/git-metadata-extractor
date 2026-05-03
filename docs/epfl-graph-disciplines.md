# EPFL Graph disciplines RAG index

Mirrors the curated EPFL Graph academic-discipline ontology
(`graphai.epfl.ch/ontology/*`) into a single Qdrant collection so callers
can ask **"what discipline is this README closest to?"** without burning
per-concept HTTP calls. The ontology is a 6-level tree of ~2226
categories where each leaf is anchored by 50–110 Wikipedia articles that
together define the category.

## What it indexes

| Item | Source | Detail |
|---|---|---|
| Categories | `GET /ontology/tree` + `GET /ontology/tree/category/{id}` | name, depth, parent, child categories, anchor concepts, canonical Wikipedia page id + title |
| Anchor concepts | from each `category_info.concepts[*]` | top-N Wikipedia article names per category, used in the embedding text |
| Wikipedia extracts | MediaWiki TextExtracts API (`prop=extracts&exintro=1`) | canonical lead-section plain text per category, mapped via the category's `name` (= article title) with `redirects=1` |

The full payload returned by graphai for each category is also stored in
the `categories.raw` JSON column so downstream consumers can reach into
it without a re-fetch.

## Storage layout

```
data/index/epfl_graph/
└── duckdb/
    └── epfl_graph.duckdb            # ~8.6 MB after a full ingest
```

Two tables:

- **`categories`** — one row per ontology node. Columns: `category_id`,
  `name`, `depth`, `parent_id`, `wikipedia_page_id`, `wikipedia_url`,
  `wikipedia_extract`, `graphsearch_url`, `n_concepts`, `n_children`,
  `embedding_text`, `raw` (JSON), `fetched_at`.
- **`category_concepts`** — top-N anchor Wikipedia concepts per
  category. Columns: `category_id`, `concept_id`, `concept_name`, `rank`.

A single Qdrant collection — `epfl_graph_disciplines` — holds the
embeddings: one 4096-dim point per category at depth ≥ 3 (the
`filter.min_depth` knob skips the broad ancestor nodes
`academic-disciplines`/`applied-sciences`/etc. by default).

## Embedding text shape

```
{name}. {wikipedia_extract truncated to 1200 chars}. Anchor concepts: c1, c2, …
```

Built by `build_embedding_text()` in
`src/index/epfl_graph/ingest/wikipedia_extracts.py`. Categories without
a Wikipedia extract (~36% of the catalogue — mostly EPFL-Graph synthetic
`topics-in-X` / `entities-in-X` taxonomy nodes) fall back to
`name + anchor concepts`.

## CLI

```bash
just epfl-graph-status                                          # row counts + Qdrant collection name
just epfl-graph-ingest                                          # walk the 2226-node tree (~25 min, polite ~5 req/s)
just epfl-graph-enrich-wikipedia                                # fill Wikipedia extracts (~3 min, batched 50/req)
just epfl-graph-embed                                           # push vectors to Qdrant (~5 min, batches of 32)
just epfl-graph-search "extracting research metadata from GitHub" --top-k 10
just epfl-graph-search "..." --min-depth 4                      # leaf-only disciplines
just epfl-graph-search "..." --no-rerank                        # skip the cross-encoder, vector-only
```

Equivalent without `just`:

```bash
python -m src.index.epfl_graph status
python -m src.index.epfl_graph ingest [--limit N]
python -m src.index.epfl_graph enrich-wikipedia [--limit N]
python -m src.index.epfl_graph embed [--limit N]
python -m src.index.epfl_graph search "<query>" --top-k 10 --candidate-k 50 --min-depth 4
```

## Configuration

Static settings live in [`config/index/epfl_graph.yaml`](../config/index/epfl_graph.yaml).
The ones worth tuning per deployment:

| Path | Default | Purpose |
|---|---|---|
| `rcp.embedding_model` | `Qwen/Qwen3-Embedding-8B` | shared with the other indices |
| `rcp.reranker_model` | `Qwen/Qwen3-Reranker-8B` | shared with the other indices |
| `graphai.rate_per_second` | `5` | tree-walk pacing (graphai is gentle but not free) |
| `graphai.anchor_concepts_per_category` | `12` | how many top anchor concepts to keep per category for the embedding text |
| `qdrant.url` | `http://localhost:6333` (override with `INDEX_QDRANT_URL=http://gme-qdrant:6333` inside the devcontainer) | |
| `filter.min_depth` | `3` | shallowest category depth that gets embedded — depth 1–2 are too broad to be useful matches |

Env vars:

- `EPFL_GRAPH_USERNAME`, `EPFL_GRAPH_PASSWORD` — required by `ingest` (the auth flows via `src/module/epfl_graph/auth.py`).
- `RCP_TOKEN` — required by `embed` and by `search` (the reranker is opt-in but on by default).
- `INDEX_QDRANT_URL` / `INDEX_QDRANT_API_KEY` — Qdrant overrides.
- `V2_EPFL_GRAPH_RAG_ENABLED` — opt-out flag for the v2 LLM-agent tool. Default `true`.

## Wired into the v2 pipeline

When `V2_EPFL_GRAPH_RAG_ENABLED` is set or unset (default `true`),
`src/v2/dependencies.py:_resolve_epfl_graph_rag_provider()` constructs an
`EpflGraphRagProvider` and stamps it onto the `ProviderSet`.

LLM agents that pick up the tool when the provider is non-None:

| Agent | Tool registered as |
|---|---|
| Repository | `search_epfl_graph_disciplines` |
| Person | `search_epfl_graph_disciplines` |
| Organization | `search_epfl_graph_disciplines` |
| Article | `search_epfl_graph_disciplines` |

Contribution and membership agents are intentionally skipped — they are
pure linking agents (person↔repo, person↔org), no discipline benefit.

The federated layer registers `epfl_graph` as an adapter exposing the
`disciplines` entity type. `gme search` hits it alongside the other
indices when the query has no entity-type hint.

## Tool signature (LLM-facing)

```python
search_epfl_graph_disciplines(
    query: str,
    top_k: int = 10,
    filters: dict[str, Any] | None = None,   # category_id, depth, parent_id, entity_type
    rerank: bool = False,
) -> list[dict[str, Any]]
```

Each hit is a thin dict:

```python
{
    "id": "<uuid>",                 # qdrant point id (uuid5(name="epfl_graph|<slug>"))
    "score": 0.45,                  # vector cosine score
    "category_id": "topics-in-natural-language-processing",
    "name": "Natural language processing",
    "depth": 4,
    "parent_id": "natural-language-processing",
    "wikipedia_page_id": "21652",
    "wikipedia_url": "https://en.wikipedia.org/?curid=21652",
    "graphsearch_url": "https://graphsearch.epfl.ch/en/category/topics-in-natural-language-processing",
    "n_concepts": 110,
    "n_children": 0,
    "collection": "epfl_graph_disciplines",
}
```

For `min_depth=4` (leaf-only discipline tagging) pass it via the CLI
flag for direct calls, or via `filters={"depth": {"$gte": 4}}` from
inside an LLM tool call.

## How the tool actually behaves

```
LLM: search_epfl_graph_disciplines(
        query="GPU-accelerated finite element solver for elastodynamics in geomechanics",
        top_k=5,
        filters={"depth": 4},
        rerank=True,
     )
→ [
    {name="Computational mechanics",     score=0.94, …},
    {name="Continuum mechanics",         score=0.93, …},
    {name="Finite element method",       score=0.92, …},
    {name="Geophysics",                  score=0.90, …},
    {name="High-performance computing",  score=0.89, …},
  ]
```

## Refreshing

The ontology changes rarely (months between meaningful tree updates).
Recommended cadence:

- **Quarterly**: `just epfl-graph-ingest && just epfl-graph-enrich-wikipedia && just epfl-graph-embed`. Total ~35 min.
- **Ad-hoc** when a new EPFL Graph deployment ships ontology changes you care about.

The ingest is fully idempotent — re-running it upserts on `category_id`
without duplicating concepts.

## Shape of the underlying graphai endpoints

For reference (these are NOT exposed by upstream `graphai-client`; they
are wrapped in `src/module/epfl_graph/ontology.py`):

- `GET /ontology/tree` — returns `{child_to_parent: [{child_id, parent_id}, …]}`. ~2226 edges.
- `GET /ontology/tree/category/{id}` — returns `{info: {category_id, depth, id, name}, parent_category: <slug>, child_categories: [<slug>, …] | None, clusters: [<id>, …] | None, concepts: [{id, name}, …] | None}`. The `info.id` and `info.name` fields are the Wikipedia page id + canonical title.
- `POST /ontology/nearest_neighbor/concept/category` — concept-id → top-N nearest categories. Used by the legacy concept_tagging stage; superseded for README→category routing by this index's semantic search.
- `GET /ontology/openalex/category/{id}/nearest_topics` — bridges into OpenAlex topic IDs (the basis of the OpenAlex-side related-entity enrichment in `src/v2/pipeline/stages/concept_tagging.py`).
