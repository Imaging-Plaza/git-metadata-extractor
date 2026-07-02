# Git Metadata Extractor

Turn a GitHub URL into a SHACL-validated [Open Pulse Ontology](https://open-pulse.epfl.ch/ontology) graph — repositories, the people who built them, the organizations behind them, and the papers they cite.

> 🌐 **In production at**
> - **[imagingplaza.epfl.ch](https://imagingplaza.epfl.ch)** — discovery portal for EPFL imaging software.
> - **[openpulse.science](https://openpulse.science)** — broader EPFL/Swiss open-science software graph.

---

## What it does

```
                 ┌──────────────────────────────┐
github.com/X  →  │  /v2/extract                 │  →  JSON-LD graph
                 │   classify → gather context  │
                 │   → root + fan-out agents    │     - schema:SoftwareSourceCode
                 │   → reconcile + resolve ROR  │     - schema:Person
                 │   → SHACL validate           │     - org:Organization
                 └──────────────────────────────┘     - org:Membership
                                                     - pulse:Contribution
                                                     - schema:ScholarlyArticle
```

A single GitHub URL → a typed graph you can SPARQL. The pipeline combines deterministic provider lookups (GitHub REST, ORCID, ROR, Infoscience, GIMIE), nine RAG indices, and optional LLM agents.

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
curl "http://localhost:1234/v2/extract/github.com/Imaging-Plaza/git-metadata-extractor?output_format=jsonld"
```

You'll get a JSON-LD `@graph` like:

```json
{
  "@context": "https://open-pulse.epfl.ch/ontology/v2.1.2.jsonld",
  "@graph": [
    {
      "@id": "urn:pulse:Imaging-Plaza/git-metadata-extractor",
      "@type": "schema:SoftwareSourceCode",
      "schema:name": "git-metadata-extractor",
      "schema:license": "https://spdx.org/licenses/MIT.html",
      "schema:author": [
        { "@id": "urn:pulse:caviri" }
      ],
      "pulse:githubRepositoryHandle": "Imaging-Plaza/git-metadata-extractor"
    },
    {
      "@id": "urn:pulse:caviri",
      "@type": "schema:Person",
      "schema:name": "Carlos Vivar Rios",
      "org:hasMembership": [
        { "@id": "urn:pulse:caviri__https://ror.org/02hdt9m26" }
      ]
    },
    {
      "@id": "urn:pulse:caviri__https://ror.org/02hdt9m26",
      "@type": "org:Membership",
      "org:organization": { "@id": "https://ror.org/02hdt9m26" }
    },
    {
      "@id": "https://ror.org/02hdt9m26",
      "@type": "org:Organization",
      "schema:name": "Swiss Data Science Center"
    }
  ]
}
```

That person → membership → organization chain was inferred by the ROR resolver stages — the pipeline reads `_company: "@SwissDataScienceCenter"` from GitHub, hits the ROR index, and materializes a proper `org:Membership` triple. See [`docs/v2-pipeline.md`](docs/v2-pipeline.md) for the full stage walkthrough.

---

## More examples

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
| **[docs/v2-pipeline.md](docs/v2-pipeline.md)** | Pipeline overview, **load-bearing assumptions**, affiliation strategy, env flags. Start here. |
| [docs/getting-started.md](docs/getting-started.md) | Install + first run, the long version |
| [docs/v2-api-reference.md](docs/v2-api-reference.md) | `/v2/extract`, `/v2/jobs`, `/v2/graph` endpoints |
| [docs/rag-indices.md](https://github.com/caviri/open-pulse-sources/blob/main/docs/rag-indices.md) | Nine RAG indices + federated layer |
| [docs/v2-rag-tools.md](docs/v2-rag-tools.md) | Agent-side RAG tools wired into the pipeline |
| [docs/migration-v1-to-v2.md](docs/migration-v1-to-v2.md) | `/v1` → `/v2` endpoint mapping |
| [.env.example](.env.example) | Every env var with defaults and notes |

Versioned doc site: <https://imaging-plaza.github.io/git-metadata-extractor/>

---

## Repository layout

```
src/v2/                  # v2 extraction pipeline (new work here)
  api.py                 # /v2/extract endpoint
  pipeline/stages/       # 25 sequential pipeline stages
  agents/llm/            # LLM-backed entity agents + RAG tools
  agents/rule_based/     # deterministic counterparts
  ingest/providers/      # GitHub, ROR, ORCID, Infoscience clients
  schema/                # JSON Schema + JSON-LD context + Pydantic models
  validation/            # strict-schema + SHACL validators

src/index/               # nine RAG indices (HuggingFace, OpenAlex, Infoscience,
                         # ORCID, ROR, Zenodo, ETHZ, GitHub, SNSF) + federated

src/v1/                  # frozen legacy pipeline — no new work
tests/v2/                # default test target
docs/                    # MkDocs site source
```

---

## Configuration

Everything is in `.env` — copy `.env.example` and fill in what you need. Required minimum:

- `API_TOKEN` — bearer token guarding `/v1/*`, `/v2/extract`, and `/v2/jobs/{id}`. **Fails closed** (unset → `503` on every protected route). Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`.
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

Per-index suites: `just hf-test`, `just orcid-test`, `just openalex-test`, etc.

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
