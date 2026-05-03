# HuggingFace Index

Standalone RAG index over EPFL- and Switzerland-affiliated HuggingFace
namespaces (organisations + personal users) and the models / datasets /
spaces they publish. Lives at `src/index/huggingface/`.

> **Two complementary views.** [`docs/v2-rag-tools.md`](v2-rag-tools.md)
> documents the **agent-facing** tool (`search_huggingface_rag`) used by
> the v2 LLM pipeline. *This* page documents the **direct-access** CLI,
> SQL, and HTTP surfaces — for analysts and scripts that don't want to go
> through an agent.
>
> The design rationale and architectural decisions live in
> [`.internal/huggingface/PLAN.md`](https://github.com/Imaging-Plaza/git-metadata-extractor/blob/main/.internal/huggingface/PLAN.md).

## Architecture (1-paragraph version)

DuckDB at `data/index/huggingface/duckdb/huggingface.duckdb` holds the
canonical records (one row per repo / namespace) plus a `chunks`
bookkeeping ledger. Qdrant (compose service `gme-qdrant:6333`) holds the
embedding vectors in four collections — `hf_models`, `hf_datasets`,
`hf_spaces`, `hf_orgs` — each chunk indexed by a deterministic
`uuid5(NAMESPACE_URL, "<type>|<repo_id>|<chunk_index>")`. Embeddings come
from `Qwen/Qwen3-Embedding-8B` on EPFL RCP; reranking from
`Qwen/Qwen3-Reranker-8B`. README cards land on disk under
`data/index/huggingface/cards/{models,datasets,spaces}/<author>/<repo>/README.md`.

```
┌─ Ingest ──────────────────────────────────────────────────────────────┐
│  HF Hub  →  DuckDB orgs/models/datasets/spaces  +  cards/ on disk     │
└──────┬────────────────────────────────────────────────────────────────┘
       ▼
┌─ Embed ───────────────────────────────────────────────────────────────┐
│  DuckDB rows  →  chunker  →  RCP /v1/embeddings  →  Qdrant collections│
│                                                                       │
│  bookkeeping: chunks(chunk_id, entity_type, repo_id, chunk_index, …)  │
└──────┬────────────────────────────────────────────────────────────────┘
       ▼
┌─ Query ───────────────────────────────────────────────────────────────┐
│  semantic: query → embed → Qdrant → rerank → entity-dedup → hydrate   │
│  sql:     guarded SELECT/WITH over DuckDB                             │
└───────────────────────────────────────────────────────────────────────┘
```

## EPFL inventory (2026-05-01 snapshot)

**25 EPFL namespaces (24 orgs + 1 personal user) → 235 artefacts (177 models · 48 datasets · 10 spaces).**

| Namespace | Type | Models | Datasets | Spaces | HF followers |
|---|---|---:|---:|---:|---:|
| `Idiap` | org | 56 | 0 | 4 | 65 |
| `EPFL-VILAB` | org | 51 | 3 | 6 | 86 |
| `EPFL-ECEO` | org | 12 | 7 | 1 | 27 |
| `LIONS-EPFL` | org | 9 | 0 | 0 | 4 |
| `vanek-epfl` | user | 9 | 6 | 0 | 0 |
| `epfl-dlab` | org | 6 | 7 | 0 | 15 |
| `EPFL-IVRL` | org | 6 | 0 | 0 | 8 |
| `SAGESSE-EPFL` | org | 4 | 0 | 0 | 5 |
| `epfl-ml4ed` | org | 4 | 0 | 0 | 3 |
| `ADP-EPFL` | org | 3 | 0 | 0 | 3 |
| `epfl-ml-ytf` | org | 3 | 0 | 1 | 3 |
| `rapieniuta-epfl` | org | 3 | 0 | 0 | 1 |
| `epfl-dhlab` | org | 2 | 0 | 0 | 14 |
| `epfml` | org | 2 | 3 | 0 | 52 |
| `epfl-llm` | org | 2 | 1 | 1 | 274 |
| `structure-epflai` | org | 1 | 3 | 0 | 3 |
| `epfl-nlp` | org | 1 | 0 | 0 | 21 |
| `EPFL-DL-CER-project` | org | 1 | 9 | 0 | 3 |
| `EPFL-CVLAB-SPACECRAFT` | org | 1 | 3 | 0 | 14 |
| `epfl-ihl` | org | 1 | 0 | 0 | 3 |
| `EPFL-LNMC` | org | 0 | 2 | 0 | 2 |
| `EPFL-DrivingVQA` | org | 0 | 1 | 0 | 2 |
| `asl-epfl` | org | 0 | 1 | 0 | 2 |
| `epfl-radio-astro` | org | 0 | 1 | 0 | 2 |
| `EPFL-CVLab` | org | 0 | 1 | 0 | 3 |

The full Switzerland scope (58 namespaces) additionally covers ETH labs,
swiss-ai, ZurichNLP, SDSC, UniBe/UniGe/UniBas/HES-SO/HSLU/FHNW. Live
counts: `just hf-status`.

The canonical EPFL author list (use as `WHERE author IN (…)` or `--filter author=…`):

```
epfl-llm, epfl-nlp, EPFL-VILAB, epfl-ml4ed, EPFL-ECEO,
epfl-dlab, LIONS-EPFL, EPFL-IVRL, SAGESSE-EPFL, EPFL-CVLAB-SPACECRAFT,
ADP-EPFL, epfl-ml-ytf, epfl-dhlab, EPFL-LNMC, EPFL-CVLab,
EPFL-DrivingVQA, structure-epflai, asl-epfl, epfl-ihl, epfl-radio-astro,
vanek-epfl, rapieniuta-epfl, EPFL-DL-CER-project, Idiap, epfml
```

## Seven ways to access the data

### 1. CLI: predefined SQL queries

```bash
# All EPFL namespaces in seed
just hf-query --predefined orgs_by_scope --param scope=epfl

# Models by author, sorted by downloads
just hf-query --predefined models_by_author --param author=Idiap     --param limit=100
just hf-query --predefined models_by_author --param author=EPFL-VILAB --param limit=100

# Datasets by author
just hf-query --predefined datasets_by_author --param author=epfl-dlab --param limit=100

# Top models across the whole index
just hf-query --predefined top_models_by_downloads --param limit=20

# Models by HuggingFace pipeline tag
just hf-query --predefined models_by_pipeline_tag --param pipeline_tag=text-generation --param limit=20
```

Other predefined queries: `count_by_entity`, `count_models`, `count_datasets`, `count_spaces`, `top_datasets_by_downloads`. All defined in `src/index/huggingface/retrieval/sql.py`.

### 2. CLI: ad-hoc SQL across all 25 EPFL namespaces

```bash
just hf-query "SELECT repo_id, author, downloads, likes
               FROM models
               WHERE author IN ('epfl-llm','epfl-nlp','EPFL-VILAB','epfl-ml4ed','EPFL-ECEO',
                                'epfl-dlab','LIONS-EPFL','EPFL-IVRL','SAGESSE-EPFL',
                                'EPFL-CVLAB-SPACECRAFT','ADP-EPFL','epfl-ml-ytf','epfl-dhlab',
                                'EPFL-LNMC','EPFL-CVLab','EPFL-DrivingVQA','structure-epflai',
                                'asl-epfl','epfl-ihl','epfl-radio-astro','vanek-epfl',
                                'rapieniuta-epfl','EPFL-DL-CER-project','Idiap','epfml')
               ORDER BY downloads DESC NULLS LAST LIMIT 50"
```

Ad-hoc queries are guarded — only `SELECT`/`WITH` is allowed; `;` and `--` injection are blocked.

### 3. CLI: semantic search

```bash
# Find an EPFL lab that does X
just hf-search "Swiss German language model"        --type orgs --top-k 5
just hf-search "medical large language model"       --type orgs --top-k 5
just hf-search "remote sensing earth observation"   --type orgs --top-k 5

# Find a model by what it does
just hf-search "fine-tuned BERT for Swiss German"   --type models --top-k 10

# Filter to EPFL personal-user accounts only
just hf-search "EPFL researcher computer vision"    --type orgs   --filter namespace_kind=user --top-k 5

# Filter to one author's repos
just hf-search "diffusion model"                    --type models --filter author=EPFL-VILAB --top-k 5

# Combine filters (Apache-2.0 only)
just hf-search "instruction-tuned LLM"              --type models --filter license=apache-2.0 --top-k 5
```

`--filter key=value` is repeatable. Multiple values for the same key collapse to Qdrant `MatchAny`; numeric values are coerced to ints.

Verified working hits (rerank scores):

| Query | `--type` | Top hit (rerank score) |
|---|---|---|
| `swiss german large language model` | `models` | `ZurichNLP/swissbert` (0.99) |
| `EPFL machine learning and optimization laboratory` | `orgs` | `LIONS-EPFL` — *Laboratory for Information and Inference Systems* (0.98) |
| `Swiss AI Initiative large language models` | `orgs` | `swiss-ai` — *Swiss AI Initiative* (0.96) |
| `computer vision and remote sensing photogrammetry` | `orgs` | `prs-eth` — *Photogrammetry and Remote Sensing Lab of ETH Zurich* (0.96) |

### 4. Python: direct DuckDB read-only

```python
import duckdb
con = duckdb.connect('data/index/huggingface/duckdb/huggingface.duckdb', read_only=True)

EPFL = ['epfl-llm','epfl-nlp','EPFL-VILAB','epfl-ml4ed','EPFL-ECEO',
        'epfl-dlab','LIONS-EPFL','EPFL-IVRL','SAGESSE-EPFL','EPFL-CVLAB-SPACECRAFT',
        'ADP-EPFL','epfl-ml-ytf','epfl-dhlab','EPFL-LNMC','EPFL-CVLab',
        'EPFL-DrivingVQA','structure-epflai','asl-epfl','epfl-ihl','epfl-radio-astro',
        'vanek-epfl','rapieniuta-epfl','EPFL-DL-CER-project','Idiap','epfml']
ph = ','.join(['?'] * len(EPFL))

models   = con.execute(f"SELECT repo_id, pipeline_tag, library_name, license, "
                       f"       downloads, likes, last_modified "
                       f"FROM models   WHERE author IN ({ph}) "
                       f"ORDER BY downloads DESC NULLS LAST", EPFL).fetchall()

datasets = con.execute(f"SELECT repo_id, license, downloads, likes "
                       f"FROM datasets WHERE author IN ({ph})", EPFL).fetchall()

spaces   = con.execute(f"SELECT repo_id, sdk, license, likes "
                       f"FROM spaces   WHERE author IN ({ph})", EPFL).fetchall()
```

The `raw` JSON column on every table holds the full HF API payload if you
need fields beyond the structured columns.

### 5. Filesystem: every README is on disk

```
data/index/huggingface/cards/{models,datasets,spaces}/<author>/<repo_name>/README.md
```

```bash
# List every EPFL model card directory
find data/index/huggingface/cards/models -mindepth 2 -maxdepth 2 -type d \
  | grep -E "/(epfl|EPFL|Idiap|epfml|LIONS-EPFL|SAGESSE-EPFL|ADP-EPFL|structure-epflai|asl-epfl|vanek-epfl|rapieniuta-epfl)/"
```

### 6. Lineage: `hf-lineage <repo_id>`

Walks the `base_models` DAG up (ancestors — parent models that this one
was fine-tuned from) and down (descendants — other repos that list this
one as their base). Pure local DuckDB lookup — no RCP / Qdrant calls,
sub-second.

```bash
# What is meditron-7b fine-tuned from?
just hf-lineage epfl-llm/meditron-7b
# → ancestors: {level_1: [{repo_id: meta-llama/Llama-2-7b}]}, edges: [...]

# What's been fine-tuned from Llama-2-7b in our index?
just hf-lineage meta-llama/Llama-2-7b
# → descendants: {level_1: [{repo_id: epfl-llm/meditron-7b, downloads: 5251, likes: 321, ...}]}

# Walk further (default depth=3)
just hf-lineage epfl-llm/meditron-7b --depth 5
```

The same is exposed in the v2 LLM pipeline as the `lineage_huggingface`
tool (registered automatically when the HF RAG provider is enabled).

### 7. HTTP: FastAPI on `:8002`

```bash
just hf-serve &  # mounts src.index.huggingface.api:app on 0.0.0.0:8002

curl -s -X POST http://localhost:8002/search \
     -H 'content-type: application/json' \
     -d '{"query":"medical LLM","entity_type":"models","top_k":5,"filter":{"author":"epfl-llm"}}'
```

## Schema reference

| Table | Primary key | Key columns | JSON columns |
|---|---|---|---|
| `orgs` | `slug` | `namespace_kind` (org\|user), `scope`, `fullname`, `details`, `num_models`, `num_datasets`, `num_spaces`, `num_followers`, `avatar_url` | `raw` |
| `models` | `repo_id` | `author`, `pipeline_tag`, `library_name`, `license`, `downloads`, `downloads_all_time`, `likes`, `gated`, `private`, `created_at`, `last_modified` | `tags`, `card_data`, `base_models`, `raw` |
| `datasets` | `repo_id` | `author`, `license`, `downloads`, `downloads_all_time`, `likes`, `gated`, `private`, `created_at`, `last_modified` | `tags`, `card_data`, `dataset_info`, `raw` |
| `spaces` | `repo_id` | `author`, `sdk`, `runtime_stage`, `hardware`, `license`, `likes`, `created_at`, `last_modified` | `tags`, `card_data`, `raw` |
| `chunks` | `chunk_id` | `entity_type` (model\|dataset\|space\|org), `repo_id`, `chunk_index`, `text`, `token_count`, `vector_id`, `embedded_at` | — |

`vector_id == chunk_id` and both equal the Qdrant point id, so DuckDB ↔ Qdrant joins are trivial.

## Running counts

```bash
just hf-status
```

emits a JSON snapshot:

```json
{
  "duckdb_counts": {
    "orgs": 153, "models": 1034, "datasets": 324, "spaces": 70, "chunks": 3163
  },
  "qdrant_counts": {
    "hf_orgs": 246, "hf_models": 1804, "hf_datasets": 1021, "hf_spaces": 92
  },
  "active_scope": "epfl",
  "rcp_configured": true,
  "hf_token_configured": false
}
```

(snapshot 2026-05-02 — current with the 73-namespace Swiss expansion + 6 Swiss companies + lineage backfill)

`duckdb.chunks == sum(qdrant_counts)` should always hold — DuckDB's
`chunks` table is the bookkeeping ledger for what's been pushed to Qdrant.

## Refresh cycle

```bash
# 1. (optional) discover new EPFL/Swiss namespaces
just hf-discover-orgs --scope switzerland
# Review data/index/huggingface/logs/discover_orgs.jsonl, edit
# config/index/huggingface.yaml seed by hand (never auto-promotes).

# 2. Re-ingest (idempotent on repo_id / slug)
just hf-ingest --scope switzerland --types models,datasets,spaces,orgs

# 3. Re-embed (idempotent — skips chunks already in DuckDB.chunks)
just hf-embed
```

If the anonymous HF API rate limit (500 req / 5 min, shared per IP) bites
mid-ingest, split into per-type runs (`--types orgs`, then `--types
spaces`, etc.) and wait between them. Setting `HF_TOKEN` in `.env`
removes the IP-share and bumps the bucket to 1k API + 5k resolver per 5
min.

## Related documentation

- [`v2-rag-tools.md`](v2-rag-tools.md) — the agent-facing
  `search_huggingface_rag` tool used by v2 LLM agents
- [`.internal/huggingface/README.md`](https://github.com/Imaging-Plaza/git-metadata-extractor/blob/main/.internal/huggingface/README.md)
  — workstream tracker (mirrors this page's access guide)
- [`.internal/huggingface/PLAN.md`](https://github.com/Imaging-Plaza/git-metadata-extractor/blob/main/.internal/huggingface/PLAN.md)
  — full design + rationale + risks
