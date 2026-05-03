# Git Metadata Extractor

A FastAPI service that turns a GitHub URL (repository / user / org) into
JSON-LD aligned with **Open Pulse Ontology v2.0.0**. The service runs the
input through a multi-stage pipeline that combines deterministic rules,
provider lookups (GitHub REST, ROR, ORCID, Infoscience, ETH Research
Collection), and optional LLM agents to produce a graph of
`schema:SoftwareSourceCode`, `schema:Person`, `org:Organization`,
`org:Membership`, `pulse:Contribution`, and `schema:ScholarlyArticle`
entities.

The repository ships **two cooperating subsystems**:

1. **Extraction service** (`src/v1/`, `src/v2/`) — the FastAPI app that
   converts a GitHub URL to JSON-LD. V1 is frozen and kept for backwards
   compatibility; **all new work targets V2** under `src/v2/`.
2. **RAG indices** (`src/index/*`) — nine sibling indices over EPFL/Swiss
   research catalogues (HuggingFace, OpenAlex, Infoscience, ORCID, ROR,
   Zenodo, ETH Research Collection, GitHub, SNSF) plus a federated layer
   that fans out across all of them. The v2 LLM agents call these indices
   as tools during extraction.

## Quick links

- [Documentation site](https://imaging-plaza.github.io/git-metadata-extractor/) (versioned via MkDocs + Mike)
- [`docs/getting-started.md`](docs/getting-started.md) — install + first run
- [`docs/v2-api-reference.md`](docs/v2-api-reference.md) — `/v2/extract`, `/v2/jobs`, `/v2/graph`
- [`docs/rag-indices.md`](docs/rag-indices.md) — the nine RAG indices + federated layer
- [`docs/v2-rag-tools.md`](docs/v2-rag-tools.md) — agent-side RAG tools wired into the v2 pipeline
- [`docs/migration-v1-to-v2.md`](docs/migration-v1-to-v2.md) — endpoint mapping
- [`CLAUDE.md`](CLAUDE.md) / [`AGENTS.md`](AGENTS.md) — agent operating contract

## Features

- **JSON-LD extraction** aligned with Open Pulse Ontology v2.0.0
  (repositories, persons, organizations, articles, memberships,
  contributions).
- **Multi-stage pipeline** with deterministic and LLM-backed agents,
  cross-bucket dedup, reconciliation, strict schema + SHACL validation,
  Selenium-backed link veracity, and ROR-driven org-hierarchy inference.
- **RAG-augmented agents** can query nine Qdrant-backed indices on demand
  (or the federated layer for whole-corpus queries).
- **Async job API** (`POST /v2/extract` + `GET /v2/jobs/{id}`) backed by
  the SQLite provider cache.
- **Three-layer cache** (provider responses, agent verdicts, full pipeline
  responses) sharing one SQLite DB.
- **Versioned documentation** site (`mkdocs` + `mike`).

## Project structure

```
src/
  api.py                       # FastAPI app, mounts /v1 and /v2 routers, /docs UI
  v1/                          # frozen legacy pipeline (no new work)
  v2/
    api.py                     # /v2/extract endpoint + pipeline driver
    jobs.py                    # async job store backing POST /v2/extract
    pipeline/                  # 23-stage extraction pipeline
    agents/llm/                # LLM-backed per-entity agents + RAG tools
    agents/rule_based/         # deterministic counterparts
    ingest/                    # provider clients (github, ror, orcid, infoscience, *_rag)
    schema/                    # JSON Schemas + JSON-LD context + generated Pydantic models
    validation/                # strict-schema + SHACL validators
  index/
    huggingface/               # HuggingFace Hub RAG index
    openalex/                  # OpenAlex RAG index
    infoscience/               # EPFL Infoscience RAG index
    ethz_research_collection/  # ETH Research Collection RAG index
    orcid/                     # ORCID person RAG index
    ror/                       # ROR organisation RAG index
    zenodo/                    # Zenodo records RAG index
    github/                    # GitHub repo+README RAG index
    snsf/                      # Swiss National Science Foundation grants
    _federated/                # cross-index search + entity lookup

config/index/                  # static YAML config per RAG index
docs/                          # mkdocs site source
tests/v2/                      # default test target
tests/index/                   # per-index test suites
scripts/v2/                    # batch extraction + debug runners
justfile                       # task runner — source of truth for commands
```

## Installation

This project uses [`uv`](https://docs.astral.sh/uv/) for dependency
management. Install with the dev extras and scaffold a `.env` from the
template:

```bash
just install-dev          # uv pip install -e ".[dev]"
cp .env.example .env
```

Edit `.env` and fill in at minimum:

- `GITHUB_TOKEN` — required for `/v2/health` to report `ok` and for any
  real GitHub call (extraction service + `gh-*` indexer).
- One LLM credential — `RCP_TOKEN` (EPFL RCP), `OPENAI_API_KEY`, or
  `OPENROUTER_API_KEY`, depending on the model profile in
  `src/v2/agents/llm/model_config.py`. Validated at startup; missing keys
  surface a clear error.

Optional (only set if you use the corresponding feature):

- `INFOSCIENCE_TOKEN` — protected Infoscience routes only.
- `SELENIUM_REMOTE_URL` — enables the link-veracity stage and the
  `fetch_link_content_via_selenium` agent tool.
- `HF_TOKEN` — recommended for the HuggingFace indexer (anonymous IPs hit
  a 500-req / 5-min bucket and stall around ~50 orgs).
- `ZENODO_TOKEN`, `OPENALEX_MAILTO`, `EPFL_GRAPH_USERNAME` /
  `EPFL_GRAPH_PASSWORD` — per-indexer politeness.

See `.env.example` for the full annotated list.

## Running the extraction service

Development (uvicorn, auto-reload on `src/**/*.py`):

```bash
just serve-dev
```

Production-shape (gunicorn, 4 workers):

```bash
just serve-gunicorn
```

Stop whatever is bound to `:$PORT` (default `1234`):

```bash
just serve-stop
```

Then:

- Swagger UI: <http://localhost:1234/docs> (with auto/manual dark-mode toggle)
- v2 health: <http://localhost:1234/v2/health>
- Extract a repo (sync): `curl "http://localhost:1234/v2/extract/github.com/octocat/Hello-World?output_format=jsonld"`
- Extract a repo (async): `POST /v2/extract` returns `202` + `job_id`; poll `GET /v2/jobs/{job_id}`.

V1 endpoints (`/v1/extract`, `/v1/cache/*`) are still mounted but frozen.
See [`docs/migration-v1-to-v2.md`](docs/migration-v1-to-v2.md) for the
endpoint mapping.

For batch extractions, `scripts/v2/batch_extract.sh` reads a hardcoded URL
list and drives `/v2/extract` with configurable parallelism (resumable —
skips already-completed result files).

## Running the RAG indices

Each index is independent: its own DuckDB, its own Qdrant collections, its
own CLI, its own justfile recipes. The full inventory and per-index
quickstarts live in [`docs/rag-indices.md`](docs/rag-indices.md). Common
shape:

```bash
# HuggingFace example
just hf-status                                      # counts + paths
just hf-ingest --scope switzerland                  # idempotent
just hf-embed                                       # only embeds new chunks
just hf-search "swiss german LLM" --top-k 5
just hf-lineage epfl-llm/meditron-7b                # ancestors + descendants

# Cross-index federated search
just gme-indices                                    # list registered adapters
just gme-search "Swiss German LLM" --top-k 10
just gme-entity 0000-0001-9534-3870                 # by any identifier
```

All indices share a Qdrant instance on the compose service
`gme-qdrant:6333` (NOT `localhost:6333` — running from inside the
devcontainer requires the service name). Embeddings come from
`Qwen/Qwen3-Embedding-8B` on EPFL RCP; reranking from
`Qwen/Qwen3-Reranker-8B`. See [`docs/rag-indices.md`](docs/rag-indices.md)
for storage layout, scopes, and failure modes.

The v2 LLM agents reach the same indices through async providers in
`src/v2/ingest/providers/*_rag.py` and pydantic-ai tool factories in
`src/v2/agents/llm/agent_tools/*_rag.py`. Toggle each with
`V2_<INDEX>_RAG_ENABLED` (all default to `true`); see
[`docs/v2-rag-tools.md`](docs/v2-rag-tools.md) for tool schemas, filter
allowlists, and the federated tool flow.

## Testing

The `justfile` is the source of truth — it invokes
`.venv/bin/python -m pytest`, so you don't need to set `PYTHONPATH` or
rely on a globally available `pytest`.

```bash
just test                  # fast loop via testmon
just test-full             # full deterministic run
just test-coverage         # with coverage report
just test-llm-integration  # opt-in real-provider LLM tests
just test-live             # opt-in live-provider smoke tests
just lint                  # ruff
just type-check            # mypy
just check                 # lint + type-check
just ci                    # lint + type-check + coverage
```

Per-index test suites live under `tests/index/<name>/` and have their own
recipes (`just hf-test`, `just orcid-test`, `just openalex-test`).

If testmon selection looks stale, `rm -f .testmondata` then `just
test-full`.

## Configuration knobs

The most-touched env vars (full list in
[`CLAUDE.md`](CLAUDE.md#configuration-env-vars) and `.env.example`):

| Var | Default | Purpose |
|---|---|---|
| `V2_AGENT_RUNTIME_DEFAULT` | `llm` | default for `/v2/extract` when query omits `agent_runtime` |
| `V2_USE_MOCK_PROVIDERS` | `true` | mock GitHub/ORCID/Infoscience/ROR; set `false` for real APIs |
| `V2_LINK_VERACITY_ENABLED` | `true` | turn off in batch runs to skip Selenium fetches |
| `V2_APPLY_CRITIC_PRUNING` | `false` | enable critic drop suggestions (LLM mode only) |
| `V2_MAX_CONCURRENT_AGENTS` | `6` | per-stage fan-out concurrency |
| `V2_PROVIDER_CACHE_PATH` | `.cache/v2/providers.db` | shared provider + verdict + pipeline cache |
| `V2_PROVIDER_CACHE_TTL_DAYS` | `30` | TTL for cached entries |
| `V2_PIPELINE_CACHE_ENABLED` | `true` | full `/extract` response cache |
| `V2_QUERY_LOG_DIR` | `logs/v2_queries` | per-request external-query log destination |
| `V2_<INDEX>_RAG_ENABLED` | `true` | toggle each RAG index tool (`INFOSCIENCE`, `ETHZ_RESEARCH_COLLECTION`, `HUGGINGFACE`, `OPENALEX`, `ZENODO`, `ORCID`, `ROR`) |
| `INDEX_QDRANT_URL` | `http://qdrant:6333` (yaml default) | Qdrant endpoint; use `http://gme-qdrant:6333` inside the devcontainer |
| `LOG_LEVEL` | `INFO` | DEBUG/INFO/WARNING/ERROR |

Use a different `V2_PROVIDER_CACHE_PATH` per run profile (e.g.
`.cache/v2-rule-based/providers.db`) to keep LLM and rule-based runs
independently invalidatable.

## Versioned documentation

The repository ships a versioned MkDocs Material + Mike site under
`docs/`.

```bash
uv pip install -e ".[docs]"
just docs-serve                      # local preview
just docs-build                      # strict build
just docs-deploy-dev                 # publish dev + latest from current branch
just docs-deploy-release 2.0.1       # publish a release version + update stable alias
just docs-set-default stable         # set default version in selector
```

`.github/workflows/docs_pages.yml` publishes docs on:

- pushes to `main` → `dev` + `latest`
- tags matching `v*` → release version + `stable`

GitHub Pages must be configured to serve from the `gh-pages` branch root.

## Docker

```bash
docker build -t git-metadata-extractor -f tools/image/Dockerfile .
docker run -it --rm --env-file .env -p 1234:1234 \
    -v ./data:/app/data \
    --name git-metadata-extractor --network dev \
    git-metadata-extractor
```

To use the link-veracity stage / ORCID Selenium-backed tooling, start a
Selenium container alongside (Grid mode recommended for concurrent
requests):

```bash
docker run --rm -d -p 4444:4444 -p 7900:7900 --shm-size="2g" \
  -e SE_NODE_MAX_SESSIONS=5 -e SE_NODE_SESSION_TIMEOUT=300 \
  --name selenium-standalone-firefox --network dev \
  selenium/standalone-firefox
# then set SELENIUM_REMOTE_URL=http://selenium-standalone-firefox:4444 in .env
```

Qdrant is wired up via the devcontainer compose file
(`.devcontainer/docker-compose.yml`); inside the devcontainer reach it at
`http://gme-qdrant:6333`.

## Credits

- Quentin Chappuis — EPFL Center for Imaging
- Robin Franken — SDSC
- Carlos Vivar Rios — SDSC / EPFL Center for Imaging
