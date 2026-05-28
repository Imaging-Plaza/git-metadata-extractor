# RAG Indices Overview

The project ships **12 sibling RAG indices** under `src/index/*` plus a
**federated layer** (`src/index/_federated/`) that fans queries out across
all of them. Each index is independent: its own DuckDB, its own Qdrant
collections, its own CLI, its own FastAPI app, its own refresh cadence.
The federated layer never shares state — it just orchestrates.

> **Two modes of access for every index:**
>
> - **SQL** over DuckDB (`<index>-query`) — exact filters, joins, counts
> - **Semantic search** over Qdrant (`<index>-search`) — meaning-based retrieval
>
> Plus a **third mode** at the top: federated search across all of them
> (`gme-search` / `gme-entity`).

## Inventory

| Index | Source | Module | Justfile | Federated? | LLM tool? | What it indexes |
|---|---|---|---|---|---|---|
| **HuggingFace** | huggingface.co | [`src/index/huggingface/`](https://github.com/Imaging-Plaza/git-metadata-extractor/tree/main/src/index/huggingface) | `hf-*` | ✅ | ✅ | EPFL/Swiss orgs+users → models, datasets, spaces |
| **OpenAlex** | api.openalex.org | [`src/index/openalex/`](https://github.com/Imaging-Plaza/git-metadata-extractor/tree/main/src/index/openalex) | `openalex-*` | ✅ | ✅ | Works, authors, institutions, sources, topics, concepts |
| **Infoscience** | infoscience.epfl.ch | [`src/index/infoscience/`](https://github.com/Imaging-Plaza/git-metadata-extractor/tree/main/src/index/infoscience) | (per-CLI) | ✅ | ✅ | EPFL DSpace publications + chunks + author/org refs |
| **ORCID** | pub.orcid.org/v3.0 | [`src/index/orcid/`](https://github.com/Imaging-Plaza/git-metadata-extractor/tree/main/src/index/orcid) | `orcid-*` | ✅ | ✅ | Persons, employments, educations (scoped to EPFL/Switzerland) |
| **ROR** | ror.org dump | [`src/index/ror/`](https://github.com/Imaging-Plaza/git-metadata-extractor/tree/main/src/index/ror) | `ror-*` | ✅ | ✅ | Research organisations (EPFL/ETH, Swiss, EU, worldwide) |
| **Zenodo** | zenodo.org/api | [`src/index/zenodo/`](https://github.com/Imaging-Plaza/git-metadata-extractor/tree/main/src/index/zenodo) | `zenodo-*` | ✅ | ✅ | Records (datasets, software, presentations, posters) |
| **ETH Research Collection** | research-collection.ethz.ch | [`src/index/ethz_research_collection/`](https://github.com/Imaging-Plaza/git-metadata-extractor/tree/main/src/index/ethz_research_collection) | (per-CLI) | ✅ | ✅ | ETHZ DSpace publications (mirrors infoscience pattern) |
| **GitHub** | api.github.com | [`src/index/github/`](https://github.com/Imaging-Plaza/git-metadata-extractor/tree/main/src/index/github) | `gh-*` | ✅ | ✅ (`search_github_rag`) | Repositories + READMEs for the EPFL/Swiss/gimie seed |
| **SNSF** | data.snf.ch | [`src/index/snsf/`](https://github.com/Imaging-Plaza/git-metadata-extractor/tree/main/src/index/snsf) | (per-CLI) | ✅ | (via federated) | Swiss National Science Foundation grants + people + institutions |
| **RenkuLab** | renkulab.io/api/data | [`src/index/renkulab/`](https://github.com/Imaging-Plaza/git-metadata-extractor/tree/main/src/index/renkulab) | `renku-*` | ✅ | ✅ | Projects, groups, users, data connectors hosted by SDSC RenkuLab |
| **EPFL Graph (disciplines)** | graphai.epfl.ch /ontology/* | [`src/index/epfl_graph/`](https://github.com/Imaging-Plaza/git-metadata-extractor/tree/main/src/index/epfl_graph) | `epfl-graph-*` | ✅ | ✅ (`search_epfl_graph_disciplines`) | Curated EPFL Graph academic-discipline ontology (~2226 categories, depth 1..5) backed by anchor Wikipedia articles |
| **SWISSUbase** | swissubase.ch | [`src/index/swissubase/`](https://github.com/Imaging-Plaza/git-metadata-extractor/tree/main/src/index/swissubase) | `swissubase-*` | ✅ | ✅ (`search_swissubase_rag`) | Swiss social-science research-data platform: studies, datasets, persons, institutions. Ingest is Selenium-driven (no public REST API; per-`studyVersionId` enumeration to dodge the search-window cap). Default scope embeds only EPFL / ETHZ / SDSC-affiliated studies. |
| **Federated** | wraps all above | [`src/index/_federated/`](https://github.com/Imaging-Plaza/git-metadata-extractor/tree/main/src/index/_federated) | `gme-*` | — | ✅ (`search_federated_rag`, `lookup_entity_federated`) | one query → all indices in parallel |

> **Support index (no RAG layer).** The [Communities index](communities-index.md)
> (`src/index/communities/`) is a plain DuckDB metadata table — no Qdrant,
> no embeddings. It maps Zenodo community slugs to a `parent_org` so the
> Zenodo index can attribute records to EPFL / ETHZ / CERN. It joins the
> federated layer for lookups but is not a semantic-search index.

## Shared infrastructure

Every index built on the post-2026-05-01 pattern uses:

- **Storage**: DuckDB at `data/index/<name>/duckdb/<name>.duckdb`. Holds canonical records + a `chunks` bookkeeping ledger (one row per Qdrant point, `chunk_id == vector_id`).
- **Vector store**: Qdrant on the compose service `gme-qdrant:6333` (NOT `localhost`!). Per-index collections, cosine distance, 4096-dim.
- **Embeddings**: `Qwen/Qwen3-Embedding-8B` on EPFL RCP, instruction-aware.
- **Reranker**: `Qwen/Qwen3-Reranker-8B` on EPFL RCP.
- **Chunking**: token-aware sliding window via `tiktoken` (`cl100k_base`); window/overlap configured per index in YAML.
- **Auth**: `RCP_TOKEN` (required for embed + rerank); per-index source tokens (`HF_TOKEN`, `GME_GITHUB_TOKEN`, `INFOSCIENCE_TOKEN`, etc.) where the upstream API requires them.

The `ror` index is a partial outlier (no DuckDB layer; flat catalog of orgs in Qdrant + a JSONL dump for lexical lookup). The `infoscience` legacy chunks live alongside the new schema.

## Per-index quickstart

### HuggingFace
Full guide at [`huggingface-index.md`](huggingface-index.md). 147 namespaces, 1034 models, 324 datasets, 70 spaces, ~3151 vector chunks (2026-05-02).

```bash
just hf-status                                      # counts + paths
just hf-ingest --scope switzerland                  # idempotent (skip-already-ingested)
just hf-embed                                       # only embeds new chunks
just hf-search "swiss german LLM" --top-k 5
just hf-search "..." --type orgs --filter namespace_kind=user --filter scope=switzerland
just hf-search "..." --facets license,pipeline_tag,author --facet-top-n 10
just hf-search "medical LLM" --type models --filter base_model=meta-llama/Llama-2-7b
just hf-lineage epfl-llm/meditron-7b                # walk base_models DAG (ancestors + descendants)
just hf-query --predefined models_by_author --param author=epfl-llm --param limit=20
```

### OpenAlex
Indexes EPFL and ETH-affiliated *works* (papers), with derived `authors`, `institutions`, `sources`, `topics`, `concepts`. ~3M+ works total when scoped to switzerland.

```bash
just openalex-status
just openalex-ingest --scope epfl
just openalex-embed --entities works,authors,institutions
just openalex-search "machine learning fairness"
just openalex-query "SELECT title, year, doi FROM works WHERE year=2024 LIMIT 20"
```

### Infoscience
EPFL's DSpace catalogue. Has a `chunks` table (sentence-level) on top of full articles, with a slim `links_index` mapping every external URL (HuggingFace, GitHub, OpenAlex, ORCID, …) back to the citing article.

```bash
just infoscience-status                  # CLI is python -m src.index.infoscience
python -m src.index.infoscience discover
python -m src.index.infoscience embed
python -m src.index.infoscience query "swiss german NLP" --target chunks --top-k 5
```

The links_index file (`data/index/infoscience/dumps/infoscience_links_index.json`, ~8 MB) is what powers the `cited_by_infoscience` field on HF search results.

### ORCID
Scoped person index — finds researchers via discover modes (EPFL email-pattern, ROR-affiliation, etc.) then pulls full ORCID records (employments, educations, works).

```bash
just orcid-status
just orcid-discover --scope epfl
just orcid-ingest --limit 100
just orcid-embed
just orcid-search "computer vision researcher EPFL"
```

### ROR
The Research Organization Registry. Built from the public ROR dump, scoped to EPFL+ETH, Switzerland, Europe, or worldwide (configurable). Lookup is "given a free-text org name, get the ROR ID + canonical record".

```bash
just ror-stats
just ror-build --scope switzerland
just ror-query "EPFL" --top-k 3        # semantic
python -m src.index.ror lookup "epfl"   # lexical, full ROR dump
```

### Zenodo
Indexes records (datasets, software releases, presentations, posters) from EPFL/Swiss communities and authors. The lifecycle mirrors HuggingFace, plus an ID-driven discovery path that mines local Infoscience full-text PDFs for cited Zenodo deposits — typically yields ~5–7× more records than the community filter alone, since most cited Zenodo deposits aren't tagged in the EPFL community. Full operational guide at [`zenodo-index.md`](zenodo-index.md).

```bash
just zenodo-status
just zenodo-ingest --scope epfl                                    # community-driven
python -m src.index.zenodo discover --source infoscience --ingest  # citation-driven delta
python -m src.index.zenodo ingest --ids my_ids.txt                 # bulk by id / DOI / URL
just zenodo-embed
just zenodo-search "EPFL dataset agriculture"
just zenodo-query --predefined records_by_author --param author=...
```

### ETH Research Collection
ETH Zürich's DSpace catalogue — sister of infoscience for ETH publications. Same shape as infoscience (chunks + articles + persons + organizations). Less stable than the infoscience pipeline; check `.internal/ethz_research_collection/` before relying on it.

### GitHub
Pulls repository metadata + READMEs for the EPFL/Swiss org+user seed (overlaps with HF org slugs but covers actual code repos rather than model artefacts). Same DuckDB+Qdrant shape; not yet wired into the federated layer.

```bash
just gh-status
just gh-ingest --scope epfl
just gh-embed
just gh-search "scientific Python data pipeline"
just gh-query --predefined repos_by_author --param author=...
```

### SNSF (Swiss National Science Foundation)
Indexes SNSF P3 grants, the people involved, and the institutions receiving them. Useful for "which EPFL groups got funded for X". CLI lives at `python -m src.index.snsf`. See `.internal/snsf/`.

### EPFL Graph (disciplines)
Mirrors the curated EPFL Graph academic-discipline ontology (`graphai.epfl.ch/ontology/*`) into Qdrant so callers can ask "what discipline is this README closest to?" without burning per-concept HTTP. ~2226 categories arranged in a 6-level tree, each leaf backed by 50–110 anchor Wikipedia articles. Embeds carry the canonical Wikipedia lead-section extract when available (~64% coverage; the remainder are EPFL-Graph synthetic `topics-in-X` / `entities-in-X` taxonomy nodes without a 1:1 Wikipedia article — they fall back to `name + anchor concepts`). Full operational guide at [`epfl-graph-disciplines.md`](epfl-graph-disciplines.md).

```bash
just epfl-graph-status                                       # row counts + Qdrant collection
just epfl-graph-ingest                                       # walk the 2226-node tree (~25 min)
just epfl-graph-enrich-wikipedia                             # fill canonical Wikipedia extracts (~3 min)
just epfl-graph-embed                                        # push vectors to Qdrant (~5 min)
just epfl-graph-search "extracting research metadata from GitHub" --top-k 10
just epfl-graph-search "..." --min-depth 4                   # leaf-only disciplines
```

Available at runtime to the **repository, person, organization, and article** v2 LLM agents as `search_epfl_graph_disciplines`. Skipped on contribution + membership (pure linking agents — no discipline benefit). Federated adapter exposes a `disciplines` entity_type. Gated by `V2_EPFL_GRAPH_RAG_ENABLED` (default true).

### SWISSUbase

Swiss social-science research-data platform ([`swissubase.ch`](https://www.swissubase.ch)), run by FORS / UZH / U. Neuchâtel / DASCH. Catalogues studies (UI label: "Project") plus their datasets, principal investigators, and partner institutions. swissUbase has **no public REST API** — every catalogue endpoint requires the SPA's session cookie plus a quirky `Accept: q=0.8;application/json;q=0.9` header. Ingest therefore drives a Selenium browser session and calls the JSON endpoints from inside the authenticated browser context. Full guide at [`swissubase-index.md`](swissubase-index.md).

The catalogue's search-studies endpoint silently caps deep pagination (~250 items max with pagesize=50, ~1000 with pagesize=10) regardless of filter combos — so the ingest enumerates `studyVersionId ∈ [1..25000]` directly, which has no such cap. Default `INDEX_SWISSUBASE_SCOPE=epfl_sdsc_ethz` ingests every study but only embeds those whose institution string matches EPFL / ETHZ / SDSC; flip to `switzerland` to embed everything.

```bash
just swissubase-status                            # counts + paths
just swissubase-ingest                            # full id-range scan (~6 hours, resumable)
just swissubase-embed --entity studies            # embed in-scope studies only
just swissubase-search "language teacher education" --top-k 5
just swissubase-search "..." --filter '{"entity_type":"persons"}'
just swissubase-query --predefined in_scope_studies --param limit=20
just swissubase-query --predefined top_institutions_in_scope --param limit=10
```

Live snapshot (2026-05-03): **18,209 studies (1,244 in-scope)** · **16,973 persons** · **1,732 institutions** · **4,589 vectors**.

Available at runtime to the **article** v2 LLM agent as `search_swissubase_rag`. Federated adapter exposes four entity types (`studies`, `datasets`, `persons`, `institutions`) under index name `swissubase`. Gated by `V2_SWISSUBASE_RAG_ENABLED` (default true). Every hit carries the canonical `https://www.swissubase.ch/...` URL in `source_url`.

## Federated layer (`src/index/_federated/`)

The federated layer turns N CLIs into one. Adapter pattern: each index module gets a thin adapter that implements `search()` + `lookup()`; the federated layer fans out across registered adapters in parallel via `ThreadPoolExecutor` and merges by score.

```bash
just gme-indices                                 # list registered adapters
just gme-search "Swiss German LLM" --top-k 10    # search every index in parallel
just gme-search "..." --indices huggingface,openalex,orcid    # subset
just gme-entity 0000-0001-9534-3870              # any identifier — every adapter that recognises it returns matches
just gme-entity W2741809807                      # OpenAlex Work
just gme-entity https://ror.org/02s376052        # ROR
just gme-entity epfl-llm/meditron-7b             # HF repo
```

Full guide at [`federated-search.md`](federated-search.md).

**LLM tools**: agents in the v2 pipeline get `search_federated_rag` and `lookup_entity_federated` (both async, both forward to `FederatedRagProvider`). Documented at [`v2-rag-tools.md`](v2-rag-tools.md).

**Adapter coverage**: **12/12** indices have adapters as of 2026-05-03 (HF, OpenAlex, Infoscience, ORCID, ROR, Zenodo, ETH Research Collection, GitHub, SNSF, RenkuLab, EPFL Graph, SWISSUbase). `gme search --rerank` engages the cross-index reranker for globally-fair scoring across adapters.

## Common operations

### Show counts across all indices

```bash
for idx in hf openalex orcid ror zenodo gh; do
  echo "=== $idx ==="
  just $idx-status
done
```

### Re-embed everything

```bash
for idx in hf openalex orcid zenodo gh; do
  just $idx-embed     # idempotent — only embeds new chunks
done
```

### Common failure modes

| Symptom | Likely cause | Fix |
|---|---|---|
| `httpcore.ConnectError` to RCP | EPFL VPN dropped or RCP brief outage | retry; check `curl https://inference-rcp.epfl.ch/v1/models` |
| Qdrant `localhost:6333` connection refused | running from inside the devcontainer | use `gme-qdrant:6333` (compose service name) |
| HF 429 mid-ingest | anonymous IP exhausted 500/5min bucket | add `HF_TOKEN` to `.env`; remember `set -a; source .env; set +a` if shell predates the edit |
| `_duckdb.IOException: lock` | concurrent write+read on same DB | one process at a time per DB |
| `chromadb` blocking `uv sync` | unused leftover dep | already removed; was a one-time fix on 2026-05-01 |

## Storage layout

Each per-index directory carries its own DuckDB store and any
ingest-time scratch (raw downloads, fetch caches, run logs). Qdrant
runs as a single shared service whose persistence lives **outside**
`data/index/` because it backs all indices simultaneously.

```
data/
  index/
    huggingface/{duckdb,cards,cache,logs}/
    openalex/{duckdb,cache,logs}/
    infoscience/{duckdb,raw,text,dumps,chroma,matches.jsonl,organizations.txt,persons.txt,relations.jsonl,discover_state.json}/
    orcid-epfl/{duckdb,cache,logs}/
    orcid-switzerland/{duckdb,cache,logs,discover.log,discover_resume.log}/
    ror/{duckdb,dump,index}/
    zenodo/{duckdb,cache,logs,state}/
    ethz-research-collection/{duckdb,raw,text,matches.jsonl,organizations.txt,persons.txt,relations.jsonl,discover_state.json}/
    github/{duckdb,cards,cache,logs}/
    snsf/{duckdb,raw}/
    renkulab/{duckdb,cache,logs,state}/
    epfl_graph/{duckdb,cache,logs}/
    swissubase/{duckdb,cache,logs,state}/
  qdrant/storage/                          # shared by ALL indices, one collection per (index, entity_type)
```

Per-subdir convention:

- `duckdb/` — the canonical DuckDB store (`<index>.duckdb` + WAL).
- `raw/` — bulk inputs from the upstream source (CSVs, JSON dumps).
  Used by indices whose ingest is local-file-driven (SNSF, Infoscience,
  ETHZ Research Collection).
- `cache/` — per-record HTTP / API cache for incremental ingest.
- `logs/` — ingest run logs.
- `state/` — resumable ingest checkpoints (Zenodo, RenkuLab, SWISSUbase).
- `cards/`, `text/`, `dumps/`, `discover_state.json`, `matches.jsonl`,
  `organizations.txt`, `persons.txt`, `relations.jsonl` — index-specific
  intermediate artefacts. See the per-index docs and CLI help.

Backups: each `.duckdb` file is a self-contained SQLite-ish snapshot — `cp` it. The Qdrant collections can be regenerated from DuckDB via `<index>-embed`, so they don't strictly need to be backed up. Qdrant persistence lives in `data/qdrant/storage/` (bind-mounted into the `gme-qdrant` container at `/qdrant/storage`).

## Related documentation

- [`huggingface-index.md`](huggingface-index.md) — HF index deep-dive (the most-used)
- [`federated-search.md`](federated-search.md) — federated layer design
- [`v2-rag-tools.md`](v2-rag-tools.md) — agent-side tools (per-index + federated)
- [`ROADMAP.md`](ROADMAP.md) — what's left to build
- `.internal/<index>/` — per-index workstream tracker (design + history; not in the public docs nav)
