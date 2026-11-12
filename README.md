# Git Metadata Extractor

Turn a GitHub URL into an [Open Pulse Ontology](https://open-pulse.epfl.ch/ontology) graph with SHACL validation feedback — repositories, the people who built them, the organizations behind them, and the papers they cite.

> 🌐 **In production at**
> - **[imagingplaza.epfl.ch](https://imagingplaza.epfl.ch)** — discovery portal for EPFL imaging software.
> - **[openpulse.science](https://openpulse.science)** — broader EPFL/Swiss open-science software graph.

---

## What it does

The `/v2` API connects a repository, user or organization to software, people,
affiliations and publications. Default output uses the **pinned v3 development
ontology**, including platform profiles. API and ontology versions are separate.
The pipeline combines deterministic provider lookups, indexed research sources
and optional LLM agents; extraction SHACL violations are reported as warnings.

**[Open the visual field guide](docs/guide/index.html)**
for C4 architecture diagrams, animated extraction sequences and mapping recipes.
Download the guide directory for offline viewing, or run `just docs-serve` and
open `http://127.0.0.1:8000/`. The guide is the landing page; reference docs live
under `/docs/`. See [publishing and preview instructions](docs/publishing.md).
Use the [maintenance guide](docs/architecture/maintenance.md) for searchable
mapping, ontology and optional run-report instructions.

Deterministic mappings live in versioned YAML: source adapters prepare facts,
entity mappings build agent payloads, and projections map the reconciled graph
into raw and canonical ontology shapes. Each source/entity has a small mapping;
shared policies cover routing, classification and merging. Start with the
[mapping overview](docs/architecture/overview.md#source-to-ontology-mappings)
or the [mapping editing guide](git_metadata_extractor/mapping/config/README.txt).

---

## Try it in 30 seconds

```bash
# 1. Setup
git clone https://github.com/Imaging-Plaza/git-metadata-extractor.git
cd git-metadata-extractor
just install-dev
cp .env.example .env   # fill in API_TOKEN, GME_GITHUB_TOKEN + one LLM credential

# 2. Run
just serve-dev         # starts on http://localhost:1234

# 3. Extract
curl -H "Authorization: Bearer $API_TOKEN" \
  "http://localhost:1234/v2/extract/github.com/Imaging-Plaza/git-metadata-extractor?output_format=jsonld"
```

The response wraps a JSON-LD graph in `output`, with an extraction-run
descriptor beside it. The default generated context follows the pinned
`v3.0.0-develop` ontology; repository metrics and platform profiles use that
contract. Add `include_run_report=true` to inspect observed stages, retrievals,
validation and known model-usage subtotals without mixing them into entity data.
See the [API reference](docs/v2-api-reference.md) for response fields and the
[pipeline walkthrough](docs/v2-pipeline.md) for execution gates.

---

## More examples

See [runnable Python examples](examples/README.md) for V2 user and organization
extraction, ORCID affiliations, and an offline cache demonstration.

```bash
# Async extraction (returns a job id)
curl -X POST http://localhost:1234/v2/extract \
     -H "Content-Type: application/json" \
     -d '{"url": "https://github.com/epfl-llm/meditron-7b"}'
# {"job_id": "0193ab12-...", "status": "queued"}

curl http://localhost:1234/v2/jobs/0193ab12-...

# Extract a person profile (fans out to their owned repos)
curl "http://localhost:1234/v2/extract/github.com/caviri"

# Extract an organization
curl "http://localhost:1234/v2/extract/github.com/Imaging-Plaza"

# Surface the internal pipeline fields too (gme-internal: + publiccode: namespaces)
curl "http://localhost:1234/v2/extract/github.com/X?include_internal_fields=true"

# Switch runtime per request
curl "http://localhost:1234/v2/extract/github.com/X?agent_runtime=rule_based"
```

Swagger UI: <http://localhost:1234/docs>

---

## Documentation

| Doc | What's in it |
|---|---|
| **[docs/v2-pipeline.md](docs/v2-pipeline.md)** | Every stage, **load-bearing assumptions**, affiliation strategy, env flags. Start here. |
| **[docs/architecture/overview.md](docs/architecture/overview.md)** | Layers, request lifecycle, the three runtimes, hallucination guards, caches |
| [docs/cross-repo-contract.md](docs/cross-repo-contract.md) | The index-layer split: who writes, who reads, version pinning |
| [docs/getting-started.md](docs/getting-started.md) | Install + first run, the long version |
| [docs/v2-api-reference.md](docs/v2-api-reference.md) | `/v2/extract`, `/v2/jobs`, `/v2/graph` endpoints |
| [docs/rag-indices.md](https://github.com/sdsc-ordes/open-pulse-sources/blob/main/docs/rag-indices.md) | Nine RAG indices + federated layer |
| [docs/v2-rag-tools.md](docs/v2-rag-tools.md) | Agent-side RAG tools wired into the pipeline |
| [docs/migration-v1-to-v2.md](docs/migration-v1-to-v2.md) | `/v1` → `/v2` endpoint mapping |
| [Ontology proposal adoption](dev/investigations/2026-11-11-proposal-adoption/adoption.txt) | Caviri's 22 requests assessed against Robin's implementation; adopted fixes and remaining ontology work |
| [Refactor development notes](dev/refactor/README.md) | Architecture, ontology requirements and historical handoffs |
| [.env.example](.env.example) | Every env var with defaults and notes |

Project site: <https://imaging-plaza.github.io/git-metadata-extractor/>

After the Pages workflow publishes this layout, the visual guide is at the site
root and versioned reference documentation is under `/docs/`.

---

## Repository layout

```
git_metadata_extractor/
  app.py                 # FastAPI app
  api/                   # /v2 routes (extract, jobs, auto-ingest, system)
  pipeline/stages/       # 25 sequential pipeline stages
  agents/llm/            # LLM-backed entity agents + RAG tools
  agents/rule_based/     # deterministic counterparts
  providers/             # GitHub, gimie, ROR, ORCID, Infoscience clients + RAG readers
  schema/                # JSON Schema + JSON-LD context + Pydantic models
  validation/            # strict-schema + SHACL validators
  experimental/          # pi terminal-agent PoC (not production)

# RAG indices moved to https://github.com/sdsc-ordes/open-pulse-sources —
# extraction providers call that service over HTTP; retrieval, ingest/embed
# and all index configuration/storage live in that repo/service.

tests/v2/                # default test target
config/                  # extractor experiment profiles and benchmark corpora
docs/                    # MkDocs site source
```

See [configuration ownership](config/README.md) before editing an index config
or choosing an extraction/benchmark profile.

### Sources service compatibility

The extractor uses HTTP; it no longer installs the sources Python library or
opens DuckDB/Qdrant indices. Local development keeps sources as an independent
Git checkout in `dev/open-pulse-sources`, excluded from this repository and its
image build context. The current refactor requires that checkout's new
`/v2/retrieval/*` and `/v2/extractor/*` routes; published sources `0.1.2` is not
compatible with this boundary yet.

```bash
docker compose -f tools/deploy/docker-compose.yml build gme-api gme-sources
# Start only after configuring API_TOKEN and provider credentials in your environment.
docker compose -f tools/deploy/docker-compose.yml up -d
```

`SOURCES_API_URL` and `SOURCES_API_TOKEN` connect the extractor to sources.
Compose wires these and gives sources exclusive access to the existing index
volume; the extractor gets a separate cache/output volume. No indices are reset
or moved when updating the Compose configuration. See the
[cross-repo contract](docs/cross-repo-contract.md) for endpoints and migration.


## Configuration

Everything is in `.env` — copy `.env.example` and fill in what you need. Required minimum:

- `API_TOKEN` — bearer token guarding `/v2/extract` and `/v2/jobs/{id}`. **Fails closed** (unset → `503` on every protected route). Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
- `GME_GITHUB_TOKEN` — required for any real GitHub call.
- `GIMIE_API_URL` — points at the `gme-gimie-api` sidecar (e.g. `http://gme-gimie-api:15400`); **required for repository extraction** (the `gimie` package was removed from the image).
- **One LLM credential** — `RCP_TOKEN` (EPFL), `OPENAI_API_KEY`, or `OPENROUTER_API_KEY`.

Optional (only when you use the feature): `INFOSCIENCE_TOKEN`, `SELENIUM_REMOTE_URL`, `HF_TOKEN`, `ZENODO_TOKEN`, `OPENALEX_MAILTO`, `EPFL_GRAPH_USERNAME` / `EPFL_GRAPH_PASSWORD`.

All ~40 env vars (with defaults + per-feature explanations) are in [`.env.example`](.env.example).

---

## Testing

```bash
just test              # fast loop via testmon (recompiles only what changed)
just test-full         # full deterministic run
just lint              # ruff
just type-check        # mypy
just ci                # lint + type-check + coverage
```

Index-layer test suites live in the
[open-pulse-sources](https://github.com/sdsc-ordes/open-pulse-sources) repo
(`just test` there).

---

## Docker

```bash
docker build -t git-metadata-extractor -f tools/image/Dockerfile .
docker run -it --rm --env-file .env -p 1234:1234 \
    -v ./data:/app/data --name gme --network dev \
    git-metadata-extractor
```

Selenium (for the link-veracity stage) and Qdrant (for the RAG indices) wire up through the devcontainer compose file. See [docs/getting-started.md](docs/getting-started.md) for the full setup.

---

## Credits

- **Quentin Chappuis** — EPFL Center for Imaging
- **Robin Franken** — SDSC
- **Carlos Vivar Rios** — SDSC / EPFL Center for Imaging

Built at [SDSC](https://datascience.ch) and the [EPFL Center for Imaging](https://imaging.epfl.ch).
