# Agent Operating Guide for git-metadata-extractor

Operating contract for autonomous and semi-autonomous coding agents working
in this repository. Goal: safe, reproducible contributions with minimal
human back-and-forth.

## What this tool is

A FastAPI service that turns a GitHub URL (repository / user / org) into
JSON-LD aligned with **Open Pulse Ontology v2.0.0**. The service runs the
input through a multi-stage pipeline that combines deterministic rules,
provider lookups (GitHub REST, ROR, ORCID, Infoscience), and optional LLM
agents to produce a graph of `schema:SoftwareSourceCode`,
`schema:Person`, `org:Organization`, `org:Membership`,
`pulse:Contribution`, and `schema:ScholarlyArticle` entities.

V1 (under `src/v1/`) is a **frozen** legacy pipeline kept around for
backwards-compatible endpoints. **All new work targets V2** under
`src/v2/`.

## Code map

```
src/api.py                       # FastAPI app, mounts /v1 and /v2 routers, /docs UI
src/v1/                          # frozen legacy pipeline (no new work)
src/v2/
  api.py                         # /v2/extract endpoint + pipeline driver
  jobs.py                        # async job store backing POST /v2/extract
  config.py                      # config knobs
  dependencies.py                # provider wiring, cache resolver
  log_context.py                 # request-id logging context
  observation/query_log.py       # per-request external-query log

  agents/
    models.py                    # AgentResult, ProviderSet, TypedEntityBuckets
    registry.py                  # runtime → runner table
    llm/                         # LLM-backed agents
      _payload_helpers.py        # force_server_uuid + shared post-LLM stamps
      _verdict_cache.py          # per-agent result cache
      <kind>/agent.py            # per-entity LLM agents (one Pydantic AI run each)
      agent_tools/               # Tool factories (selenium, ROR, ORCID, etc.)
                                  #   *_rag.py: per-index Qdrant search tools
                                  #   (infoscience, huggingface, openalex,
                                  #    zenodo, orcid, ror, renkulab,
                                  #    epfl_graph_rag) — see
                                  #    docs/v2-rag-tools.md
    rule_based/
      <kind>_agent.py            # deterministic counterparts (no LLM)

  ingest/
    cache.py                     # ProviderCache (SQLite, WAL)
    providers/                   # github / ror / orcid / infoscience clients
                                  # *_rag.py: async Qdrant-backed RAG providers
                                  # _rag_helpers.py: shared filter/rerank utils

  pipeline/
    orchestrator.py              # stage runner, fan-out concurrency, retries
    stages/                      # the actual stages — see "Pipeline" below

  schema/                        # JSON Schemas (agent + strict) + JSON-LD context
  validation/                    # strict-schema + SHACL validators
  canonicalization/              # ID resolution, string normalisation

src/index/                       # 11 sibling RAG indices (DuckDB + Qdrant per index) +
                                 # _federated/ adapter layer.
                                 #   epfl_graph/  — disciplines ontology RAG
                                 #                  (see docs/epfl-graph-disciplines.md)
                                 # See docs/rag-indices.md for the full inventory.

src/module/                      # Standalone analytical modules complementing v2.
                                 #   dependents/  — GitHub `/network/dependents` scraper
                                 #   epfl_graph/  — graphai-client wrapper + ontology
                                 #                  endpoints + OpenAlex bridge.
                                 #                  Used by concept_tagging and the
                                 #                  src/index/epfl_graph/ ingest pass.

tests/v2/                        # default test target
```

## Pipeline (the actual stages, in order)

`/v2/extract` runs the same pipeline regardless of `agent_runtime`. The
runtime only controls which agent implementations execute (LLM agents vs.
deterministic rule-based agents). All other stages run unconditionally.

```
1.  classify_url               classify the input as repository / user / org
2.  gather_context             fetch GitHub metadata + GIMIE JSON-LD
3.  context_summary  [LLM]     compile a markdown summary; raw blobs are stripped
                               from per-agent prompts in LLM mode
4.  repo_agent                 produce the root repository entity
5.  person_agents (fan-out)    one agent per discovered contributor / user
6.  org_agents    (fan-out)    one agent per discovered organisation
7.  article_agents (fan-out)   discover scholarly articles tied to the repo
8.  membership_agents (fan-out) one agent per (person, org) pair
9.  contribution_agents (fan-out) one agent per (person, repo) pair
10. llm_dedup       [LLM]      cross-bucket entity dedup + ID remap (fail-open)
11. reconcile_entities         deterministic ID canonicalisation + linkage
12. llm_critic      [LLM, gated] drop-suggestion stage (off by default)
13. guarantee_repo_author      stamp github-owner as schema:author when empty
14. strict_validation          per-entity strict JSON Schema check
15. assemble_output            split graph into root + related + excluded
16. link_veracity   [LLM, gated] verify every URL via Selenium fetch + LLM
17. validate_articles          drop placeholder/sentinel-DOI articles
18. validate_ownership         strip mismatched pulse:owns
19. infer_owners               stamp pulse:owns / pulse:ownedBy from handles
20. infer_github_handle_parents  fuzzy-search ROR for parent of every github
                                 org; add ROR org entities, stamp unitOf
21. org_relationships [LLM]    whole-graph LLM call to refine unitOf edges
22. infer_org_units            deterministic name-token fallback for unitOf
23. concept_tagging  [gated]   pull EPFL Graph concepts/keywords/disciplines
                               from the README onto the root repo entity as
                               internal `_concepts`/`_keywords`/`_disciplines`
                               metadata (off by default; opt-in with
                               `V2_CONCEPT_TAGGING_ENABLED=true`)
24. build_jsonld_output        produce the final JSON-LD graph
```

**Gates:**

- Stages tagged `[LLM]` only run in `agent_runtime=llm`.
- `link_veracity` is `[LLM]`-only too: in `agent_runtime=rule_based` it is **always skipped** (rule-based mode is guaranteed LLM-free).
- `llm_critic` is **off by default**. Set `V2_APPLY_CRITIC_PRUNING=true` to enable (LLM mode only).
- `link_veracity` is **on by default in LLM mode**. Set `V2_LINK_VERACITY_ENABLED=false` to skip even in LLM mode (recommended for batch runs).
- `concept_tagging` is **off by default**. Set `V2_CONCEPT_TAGGING_ENABLED=true` to opt in. Backends are pluggable via `V2_CONCEPT_TAGGING_BACKEND` ∈ {`epfl_graph` (default, calls graphai), `wikipedia` (credential-free MediaWiki opensearch), `llm` (pydantic-ai)}. Stamps `_concepts` / `_keywords` / `_disciplines` as internal `_*` metadata (stripped before JSON-LD output and strict validation). Optional OpenAlex enrichment per discipline via `V2_CONCEPT_TAGGING_OPENALEX_RELATED_ENABLED=true` (publications, people, units). Full reference at [`docs/concept-tagging.md`](docs/concept-tagging.md).

**Hallucination guards baked into agents:**

- `force_server_uuid` overwrites whatever UUID the LLM emitted with a server-generated one in `identifiers.uuid` only — never as a top-level field (additionalProperties violations).
- Repository agent post-LLM: stars/forks from GitHub REST (deterministic, not LLM-derived); discipline fallback `wd:Q428691` (computer engineering) when LLM emits empty/null; `pulse:repositoryType` keyword heuristic in rule-based mode.
- Contribution agent post-LLM: stamps `schema:author = target_person.id` and `pulse:contributionTo = target_repository.id` from the orchestrator's authoritative pair, regardless of what the LLM emits.
- Article agent post-LLM: drops the entity if `schema:identifier` is a placeholder DOI (`10.0000/...`) or sentinel string (`UNKNOWN`, `N/A`, `TBD`, etc.) and no `pulse:infoscienceArticleIdentifier` is present.
- Article agent (rule-based) defaults to repo-name-only Infoscience queries; opt in to the wider `include_person_queries=True` / `include_organization_queries=True` blend only when over-attribution risk is low.

## API surface

- `GET  /v2/health` — health check
- `POST /v2/extract` — async job. Body: `{source_url, agent_runtime?, output_format?, include_context_summary?}`. Returns `{job_id, status, status_url}`; poll `GET /v2/jobs/{job_id}`.
- `GET  /v2/jobs/{job_id}` — job status + result when complete
- `GET  /v2/extract/{full_path:path}` — synchronous extract (single repo)
- `GET  /docs` — Swagger UI with auto/manual dark-mode toggle (override persisted in `localStorage`)

V1 endpoints (`/v1/extract`, `/v1/cache/*`) are still mounted but frozen.

## Configuration (env vars)

| Var | Default | Purpose |
|---|---|---|
| `GITHUB_TOKEN` | — | required for live GitHub provider |
| `RCP_TOKEN` / `OPENAI_API_KEY` / `OPENROUTER_API_KEY` | — | one is required for LLM mode |
| `INFOSCIENCE_TOKEN` | unset | only for protected Infoscience routes |
| `SELENIUM_REMOTE_URL` | unset | enables Selenium-backed link veracity + selenium-fetch tool |
| `V2_AGENT_RUNTIME_DEFAULT` | `llm` | default runtime when `/v2/extract` omits `agent_runtime` |
| `V2_USE_MOCK_PROVIDERS` | `true` | swap in mock GitHub/ORCID/Infoscience/ROR providers |
| `V2_GITHUB_BASE_URL` | `https://github.com` | for GitHub Enterprise |
| `V2_LINK_VERACITY_ENABLED` | `true` | turn off to skip the link-veracity stage in LLM mode (rule-based mode skips unconditionally) |
| `V2_INFOSCIENCE_RAG_ENABLED` | `true` | enables the Infoscience RAG agent tools (Qdrant-backed semantic search + on-demand chunk/record fetch). Construction degrades gracefully when Qdrant or RCP is unreachable. |
| `V2_ETHZ_RESEARCH_COLLECTION_RAG_ENABLED` | `true` | enables the ETH Research Collection RAG agent tools (DSpace-backed sister index to Infoscience for ETHZ research outputs). Same shape: search + fetch_chunks + fetch_records. |
| `V2_HUGGINGFACE_RAG_ENABLED` | `true` | enables the HuggingFace Hub RAG search tool (collections: `hf_models`, `hf_datasets`, `hf_spaces`, `hf_orgs`). |
| `V2_OPENALEX_RAG_ENABLED` | `true` | enables the OpenAlex RAG search tool (collections: `works`, `authors`, `institutions`, `sources`, `topics`, `concepts`). |
| `V2_ZENODO_RAG_ENABLED` | `true` | enables the Zenodo RAG search tool (collection: `zenodo_records`). |
| `V2_ORCID_RAG_ENABLED` | `true` | enables the ORCID RAG search tool (entities: `persons`, `employments`, `educations`; collections namespaced by scope). |
| `V2_ROR_RAG_ENABLED` | `true` | enables the ROR RAG search tool (scopes: `epfl_ethz`, `switzerland`, `europe`, `worldwide`). |
| `V2_SWISSUBASE_RAG_ENABLED` | `true` | enables the SWISSUbase RAG search tool (collection: `swissubase_entities`; entities: `studies`, `datasets`, `persons`, `institutions`). Ingest is Selenium-driven; default scope embeds only EPFL/ETHZ/SDSC-affiliated studies. |
| `V2_RENKULAB_RAG_ENABLED` | `true` | enables the RenkuLab RAG search tool (renkulab.io). One Qdrant collection per entity type: `renkulab_projects`, `renkulab_groups`, `renkulab_users`, `renkulab_data_connectors`. The single tool searches across all four by default; the `entity_types` argument scopes to a subset. |
| `RENKULAB_TOKEN` | unset | optional; without it the indexer can still ingest public projects/groups/data_connectors and harvest users via `/search/query?q=type:User`. With it, set on `https://renkulab.io/api/data` for richer user records. |
| `V2_EPFL_GRAPH_RAG_ENABLED` | `true` | enables the EPFL Graph disciplines RAG search tool (`search_epfl_graph_disciplines`). Single Qdrant collection `epfl_graph_disciplines` over the curated EPFL Graph academic-discipline ontology (~2226 categories, depth 1..5, embeddings built from `name + canonical Wikipedia lead-section + top anchor concept names`). Wired into the repository, person, organization, and article LLM agents. Refresh with `just epfl-graph-{ingest,enrich-wikipedia,embed}`. See [`docs/epfl-graph-disciplines.md`](docs/epfl-graph-disciplines.md). |
| `EPFL_GRAPH_USERNAME`, `EPFL_GRAPH_PASSWORD` | unset | required by the `epfl-graph-ingest` recipe (the auth handshake against `graphai.epfl.ch`). Not needed at runtime once the index is hydrated — `search_epfl_graph_disciplines` only hits Qdrant + RCP. |
| `INDEX_QDRANT_URL` | `http://qdrant:6333` (yaml default) | Qdrant endpoint for every RAG index. Inside the devcontainer use `http://gme-qdrant:6333`. |
| `V2_APPLY_CRITIC_PRUNING` | `false` | turn on to enable critic drop suggestions |
| `V2_MAX_CONCURRENT_AGENTS` | `6` | per-stage fan-out concurrency |
| `V2_PROVIDER_CACHE_PATH` | `.cache/v2/providers.db` | SQLite path for the provider+verdict+pipeline cache. Use a different path per run profile (e.g. LLM vs rule-based) for isolation. |
| `V2_PROVIDER_CACHE_TTL_DAYS` | `30` | TTL for cached entries |
| `V2_PROVIDER_CACHE_ENABLED` | `true` | when `false`, every external lookup is fresh |
| `V2_PIPELINE_CACHE_ENABLED` | `true` | when `false`, every `/extract` re-runs the full pipeline |
| `V2_QUERY_LOG_DIR` | `logs/v2_queries` | per-request external-query log destination |
| `LOG_LEVEL` | `INFO` | DEBUG/INFO/WARNING/ERROR |

Required environment **for serving requests**: `GITHUB_TOKEN` and at least
one LLM credential (when LLM mode is the default).

Rules:
- Never print, log, or commit secrets.
- Never modify `.env`, `.env2`, or other secret-bearing files unless explicitly asked.
- Fail fast and report the missing variable name (no value) when a required var is absent.

## Common commands

The `justfile` is the source of truth. Prefer `just <recipe>` over ad-hoc shell.

| Recipe | Purpose |
|---|---|
| `just install-dev` | install with dev extras |
| `just setup` | install + scaffold `.env` |
| `just serve-dev` | uvicorn + auto-reload (watches only `src/**/*.py`) |
| `just serve-gunicorn` | gunicorn with 4 workers (production-shape) |
| `just serve-stop` | stop whatever's bound to `:$PORT` |
| `just test` | fast tests via testmon |
| `just test-full` | full deterministic test run |
| `just test-file <path>` | one file |
| `just lint` / `just type-check` / `just check` | quality gates |
| `just v2-models-generate` | regenerate Pydantic models from strict schemas |
| `just v2-models-check` | assert generated models are in sync |

For batch extractions over many repos: `scripts/v2/batch_extract.sh` reads
a hardcoded URL list and runs them through `/v2/extract` with configurable
parallelism. Resumable: skips repos whose result file already exists with
a non-`running` status.

## Pipeline cache topology

Three caches share a single SQLite DB (path: `V2_PROVIDER_CACHE_PATH`):

1. **Provider cache** — gimie payloads, GitHub REST responses, ROR / ORCID / Infoscience hits. Deterministic, content-addressed.
2. **Agent verdict cache** — LLM agent results keyed on agent name + identity. Skips a repeat LLM call for a known-good payload.
3. **Pipeline cache** — full `/v2/extract` response for a given source URL.

Set `V2_PROVIDER_CACHE_PATH` to a different file per run profile (e.g.,
`.cache/v2-rule-based/providers.db` for rule-based runs) to keep them
isolated and independently invalidatable.

## Editing rules

- Keep diffs minimal and scoped to the requested task.
- Preserve existing code style, project conventions, and import patterns.
- Do not rename or move public modules unless explicitly requested.
- Do not modify secret-bearing files unless explicitly requested.
- Do not run destructive git/file operations unless explicitly requested.
- If unrelated local changes exist, do not revert them — work around them and report context.

## Schema change rules

JSON Schemas live in **three byte-identical copies** that must stay in sync:

1. `src/v2/schema/json/{type}/{entity}.schema.json` (source)
2. `dev/ontology-v2-json-response/a-001/json-schema/{type}/pulse_{Entity}Shape.schema.json` (promoted)
3. `tests/v2/fixtures/schema/{type}/{entity}.schema.json` (test fixture)

After any schema edit: copy to all three and run `just v2-models-generate`
to regenerate Pydantic models in `src/v2/schema/models/`. `just v2-models-check`
in CI catches drift.

## Identifier conventions

- **Person**: `https://orcid.org/{orcid}` when ORCID known, else `https://github.com/{login}`, else `urn:pulse:{uuid}`
- **Organization**: `https://ror.org/{id}` when ROR known, else `https://github.com/{handle}`, else `urn:pulse:{uuid}`
- **Repository**: `https://github.com/{owner}/{name}`
- **Article**: `https://doi.org/{doi}` when DOI known
- **Membership**: `{person_id}_{org_id}` composite
- **Contribution**: `{person_id}_{repo_id}` composite

`identifiers.uuid` is always a server-generated UUIDv4 (via
`src/v2/agents/models.py::generate_uuid()`); the LLM never controls it.

## Internal pipeline metadata

Fields whose names start with `_` (e.g. `_person_ref` on Memberships) are
internal pipeline metadata. They are **stripped** before strict
validation, JSON-LD output, RDF serialisation, and any external
artefact. Never expose `_`-prefixed fields in API responses.

## Reporting contract

Completion reports must include:
- Files changed
- Behaviour change
- Commands run + key results
- Risks / follow-ups

No vague "done". Reports must include verifiable evidence.

## Definition of done

- Requested scope implemented
- Relevant tests/checks passed (or blockers explicitly documented)
- No secret leakage
- No unrelated mutations
- No undocumented behaviour changes
