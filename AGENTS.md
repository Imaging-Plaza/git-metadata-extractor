# Agent Operating Guide for git-metadata-extractor

Operating contract for autonomous and semi-autonomous coding agents working
in this repository. Goal: safe, reproducible contributions with minimal
human back-and-forth.

## What this tool is

A FastAPI service that turns a GitHub URL (repository / user / org) into
JSON-LD aligned with **Open Pulse Ontology v2.1.2**. The service runs the
input through a multi-stage pipeline that combines deterministic rules,
provider lookups (GitHub REST, ROR, ORCID, Infoscience), and optional LLM
agents to produce a graph of `schema:SoftwareSourceCode`,
`schema:Person`, `org:Organization`, `org:Membership`,
`pulse:Contribution`, and `schema:ScholarlyArticle` entities.

The legacy v1 API was **removed in 3.0.0** (repo-split release). All work
targets V2 under `git_metadata_extractor/`; `docs/migration-v1-to-v2.md` maps the removed
endpoints for old consumers.

## Code map

```
git_metadata_extractor/
  app.py                         # FastAPI app: mounts the /v2 router + /docs UI
  api/                           # the /v2 HTTP surface (URL prefix is /v2 — public contract)
    _router.py                   # the single APIRouter
    _helpers.py                  # env gates, stage constants, app-state resolution
    extract.py                   # GET/POST /extract + _run_extract_job + assembly
    auto_ingest.py               # post-extract index write-through (opt-in flags)
    jobs.py                      # /jobs/{id}, cancel, /crawl
    system.py                    # /cache/clear, /health
  jobs.py                        # async job store backing POST /v2/extract
  config.py                      # config knobs
  dependencies.py                # provider wiring, cache resolver
  log_context.py                 # request-id logging context
  observation/query_log.py       # per-request external-query log

  agents/                        # production runtimes only
    models.py                    # AgentResult, ProviderSet, TypedEntityBuckets
    registry.py                  # runtime → runner table
    llm/                         # LLM-backed agents
      _payload_helpers.py        # force_server_uuid + shared post-LLM stamps
      _verdict_cache.py          # per-agent result cache
      model_config.py            # LLM provider/model profiles + credentials
      <kind>/agent.py            # per-entity LLM agents (one Pydantic AI run each)
      agent_tools/               # Tool factories (selenium, ROR, ORCID, etc.)
                                  #   *_rag.py: per-index Qdrant search tools
                                  #   — see docs/v2-rag-tools.md
    rule_based/
      <kind>_agent.py            # deterministic counterparts (no LLM)
    refiners/                    # hybrid-runtime LLM refiners — propose targeted
      <kind>/agent.py            # patches over rule-based output, whitelisted fields only

  providers/                     # the provider/READ layer (was "ingest" pre-split)
    cache.py                     # ProviderCache (SQLite, WAL)
    github_provider.py etc.      # github / ror / orcid / infoscience clients
                                  # *_rag.py: async Qdrant-backed RAG providers
    gimie_api_client.py          # gimie sidecar client (TTL → JSON-LD bridge)
    gimie_extract.py             # extract_gimie intermediate (sidecar/in-process seam)
    github_accounts/             # GitHub user/org GraphQL parsers + models
    detection/                   # GitHub URL classifier

  pipeline/
    orchestrator.py              # stage runner, fan-out concurrency, retries
    stages/                      # the actual stages — see "Pipeline" below

  schema/                        # JSON Schemas (agent + strict) + JSON-LD context
  validation/                    # strict-schema + SHACL validators
  canonicalization/              # ID resolution, string normalisation

  experimental/                  # not production: pi terminal-agent PoC
    terminal/ terminal_subagent/ skills/

# NOTE: the RAG index layer (formerly src/index/ + src/module/ + the
# /v2/indices/* + /v2/manifest API) lives in a separate repo/service:
#   https://github.com/sdsc-ordes/open-pulse-sources
# The v2 read-side providers import it as the `open_pulse_sources`
# library — a declared, tag-pinned dependency in pyproject.toml (see
# "Cross-repo version pin" below). This service only READS the indices
# (Qdrant + DuckDB under data/index/); ingest/embed/reset happen in the
# open-pulse-sources service. config/index/*.yaml stays here because
# the library resolves config/data paths CWD-relative.

tests/v2/                        # default test target
```

## Pipeline (the actual stages, in order)

There are **two sequencers, not one** — a distinction worth holding onto,
because "add a stage" means different work in each:

1. **`PLAN_BY_TYPE`** (`pipeline/orchestrator.py:120`) — the agent-generation
   plan. Three plans, one per detected type; the orchestrator runs them with
   fan-out concurrency and retries. This is the only part that varies with the
   input kind.
2. **`_run_extract_job`** (`api/extract.py`) — everything after the agents:
   reconciliation, ROR resolvers, validation, inference, output. A flat
   sequence of direct calls, not a plan object. Stage banners in that file
   (`# === <name> stage ===`) and the `STAGE_*` constants in
   `api/_helpers.py` are the naming source of truth.

`/v2/extract` runs the same post-agent sequence regardless of
`agent_runtime`; the runtime selects agent implementations and gates the
`[LLM]` stages below.

**Phase 1 — agent generation (orchestrator, `PLAN_BY_TYPE`):**

```
repository:   context_gather -> repo_agent   -> person_agents -> org_agents
                             -> article_agents -> membership_agents -> contribution_agents
user:         context_gather -> person_agent -> repo_agents   -> org_agents
                             -> article_agents -> membership_agents -> contribution_agents
organization: context_gather -> org_agent    -> person_agents -> repo_agents
                             -> article_agents -> membership_agents -> contribution_agents
```

- `classify_url` runs in the API layer *before* the orchestrator, not as a
  plan stage.
- `context_summary_agent` is **not** a plan entry: it runs inside
  `context_gather` (orchestrator.py:377), LLM runtime only, and fails open
  with a warning — the pipeline continues without a compiled summary.
- The root stage per type comes from `ROOT_STAGE_BY_DETECTED_TYPE`.

**Phase 2 — post-agent sequence (`api/extract.py`, in execution order):**

```
permissive_validation          per-entity permissive schema pass
llm_dedup           [LLM]      cross-bucket entity dedup + ID remap (fail-open)
reconcile_entities             deterministic ID canonicalisation + linkage
                               (also anonymises emails via stages/privacy.py)
resolve_company_to_ror         _company -> ROR: Membership + Org stub
resolve_bio_to_ror             bio/blog/email -> ROR: Membership + Org stub
resolve_bio_to_ror_llm  [LLM]  LLM long-tail affiliation resolver
resolve_placeholder_orgs_to_ror  upgrade placeholder Orgs to real ROR entities
llm_critic          [LLM, gated] drop-suggestion stage (off by default)
refine_with_llm     [hybrid]   whitelisted-field patches over rule-based output
guarantee_repo_author          stamp github-owner as schema:author when empty
strict_validation              per-entity strict JSON Schema check
assemble_output                split graph into root + related + excluded
link_veracity       [LLM, gated] verify every URL via Selenium fetch + LLM;
                               on failure promote_failed_id_entities +
                               apply_link_pruning_to_assembled_output
validate_articles              drop placeholder/sentinel-DOI articles
validate_author_classes        drop schema:author refs whose target is missing
                               or not a schema:Person (SHACL class shape)
validate_ownership             strip mismatched pulse:owns
infer_owners                   stamp pulse:owns / pulse:ownedBy from handles;
                               coerces bare-login strings (e.g. "luzpaz") to
                               {"@id": "https://github.com/luzpaz"} so SHACL
                               never sees a <file:///CWD/...> URI
validate_ownership             SECOND pass — catches inverse edges that
                               infer_owners just added
prune_dangling_refs            drop refs whose target left the graph
infer_github_handle_parents    fuzzy-search ROR for each github org's parent;
                               add ROR org entities, stamp unitOf
org_relationships   [LLM]      whole-graph LLM call to refine unitOf edges
infer_org_units                deterministic name-token fallback for unitOf
demote_github_props_to_units   move github-only props off ROR-identified orgs
emit_fork_parent_stubs         materialise the upstream repo of a fork
infer_article_source_organization  attribute articles to a source org
concept_tagging     [gated]    EPFL Graph concepts/keywords/disciplines from
                               README + GitHub description onto the root repo
                               as internal _concepts/_keywords/_disciplines
                               (off by default: V2_CONCEPT_TAGGING_ENABLED)
tag_rule_based_disciplines     deterministic discipline fallback
build_jsonld_output            final JSON-LD graph; strips redundant pulse:ror
                               from any org:Organization whose @id is already
                               the ROR (closed-shape violation fix)
shacl_gate                     SHACL validation — warning-only (see below)
compute_stats                  response counters/timings
```

Verify this list against the code before trusting it — it was reconstructed
from `PLAN_BY_TYPE`, the stage banners, and the `STAGE_*` constants on
2026-07-29. A published, human-facing version lives in
[`docs/v2-pipeline.md`](docs/v2-pipeline.md).

**Gates:**

- Stages tagged `[LLM]` only run in `agent_runtime=llm`.
- `link_veracity` is `[LLM]`-only too: in `agent_runtime=rule_based` it is **always skipped** (rule-based mode is guaranteed LLM-free).
- `agent_runtime=hybrid` runs the rule-based generators (stages 4-9) **and** an LLM refiner stage (`refine_with_llm`, between reconciliation and `guarantee_repo_author`). The refiner proposes whitelisted-field patches per entity type: organizations (`pulse:OrganizationType`), repositories (`pulse:discipline`, `pulse:repositoryType` only when current is `pulse:Other`), and persons (`schema:name` only when current looks like a GitHub handle). LLM-only stages (`llm_dedup`, `llm_critic`, `link_veracity`, `org_relationships`) are **skipped** in hybrid mode. Toggle with `V2_HYBRID_REFINER_ENABLED` (default `true`).
- `llm_critic` is **off by default**. Set `V2_APPLY_CRITIC_PRUNING=true` to enable (LLM mode only).
- `link_veracity` is **on by default in LLM mode**. Set `V2_LINK_VERACITY_ENABLED=false` to skip even in LLM mode (recommended for batch runs).
- `concept_tagging` is **off by default**. Set `V2_CONCEPT_TAGGING_ENABLED=true` to opt in. Backends are pluggable via `V2_CONCEPT_TAGGING_BACKEND` ∈ {`epfl_graph` (default, calls graphai), `wikipedia` (credential-free MediaWiki opensearch), `llm` (pydantic-ai)}. Stamps `_concepts` / `_keywords` / `_disciplines` as internal `_*` metadata (stripped before JSON-LD output and strict validation). Optional OpenAlex enrichment per discipline via `V2_CONCEPT_TAGGING_OPENALEX_RELATED_ENABLED=true` (publications, people, units). Full reference at [`docs/concept-tagging.md`](docs/concept-tagging.md).

**Hallucination guards baked into agents:**

- `force_server_uuid` overwrites whatever UUID the LLM emitted with a server-generated one in `identifiers.uuid` only — never as a top-level field (additionalProperties violations).
- Repository agent post-LLM: stars/forks from GitHub REST (deterministic, not LLM-derived); discipline fallback `wd:Q428691` (computer engineering) when LLM emits empty/null; `pulse:repositoryType` keyword heuristic in rule-based mode.
- Contribution agent post-LLM: stamps `schema:author = target_person.id` and `pulse:contributionTo = target_repository.id` from the orchestrator's authoritative pair, regardless of what the LLM emits.
- Article agent post-LLM: drops the entity if `schema:identifier` is a placeholder DOI (`10.0000/...`) or sentinel string (`UNKNOWN`, `N/A`, `TBD`, etc.) and no `pulse:infoscienceArticleIdentifier` is present.
- Article agent (rule-based) defaults to repo-name-only Infoscience queries; opt in to the wider `include_person_queries=True` / `include_organization_queries=True` blend only when over-attribution risk is low.
- Membership agents (both rule-based and LLM): swap `time:hasBeginning` / `time:hasEnd` when ORCID returns the pair inverted, so `hasBeginning <= hasEnd` always holds.

**SHACL conformance auto-fixes (warning-only `shacl_gate`, but the graph is fixed in place):**

The SHACL gate emits violations as `result.warnings` rather than aborting,
so the responsibility for producing a SHACL-clean graph lives in the
upstream stages and agents. The four most common violations are
addressed deterministically:

1. `pulse:ownedBy` IRI shape — `infer_owners` rewrites bare-login strings
   to `{"@id": "https://github.com/{handle}"}`. Without this, SHACL
   resolves the bare token against the working-directory base URI
   (`<file:///workspaces/project/luzpaz>`) and the closed-shape check on
   `schema:Person | org:Organization` fails.
2. `pulse:ror` redundancy — `build_jsonld_output` strips the field on
   any `org:Organization` whose `@id` is already the ROR. The
   Organization shape is `sh:closed` and rejects `pulse:ror`; the
   `@id` already carries that information.
3. `Membership` date order — both membership agents swap inverted dates
   (above).
4. `schema:author` class — `validate_author_classes` filters refs whose
   target is missing from the graph or not typed `schema:Person`.
   Catches Membership / Contribution ids leaking into author lists.

**Orchestrator fanout filtering:**

- `_filter_person_work_items` skips a queued person fanout when the
  GitHub handle resolves to `type=Organization` (cached
  `provider.github.get_user(login)` lookup).
- `_filter_org_work_items` mirrors the rule for org fanouts: skips
  handles whose GitHub `type` is `User`. This prevents the 4× retry
  loop in `org_agent` for personal handles encoded into
  `org:hasMembership` composite ids.
- `_person_fanout_contexts` materialises a User-account repo owner as a
  Person when missing from `contributors` (abandoned repos, empty
  repos). Prevents the owner from leaking as a bare-string ref in
  `pulse:ownedBy` / `schema:author` with no backing entity.

## API surface

- `GET  /v2/health` — health check (open, no auth)
- `POST /v2/extract` — async job. Body: `{source_url, agent_runtime?, output_format?, include_context_summary?}`. Returns `{job_id, status, status_url}`; poll `GET /v2/jobs/{job_id}`.
- `GET  /v2/jobs/{job_id}` — job status + result when complete
- `GET  /v2/extract/{full_path:path}` — synchronous extract (single repo)
- `GET  /docs` — Swagger UI with auto/manual dark-mode toggle (override persisted in `localStorage`)

**Auth:** `/v2/extract` and `/v2/jobs/{id}` require
`Authorization: Bearer <API_TOKEN>` (see the `API_TOKEN` row below). `/`,
`/docs`, and `/v2/health` are open. The dependency lives in
`git_metadata_extractor/auth.py::verify_token`.

## Configuration (env vars)

| Var | Default | Purpose |
|---|---|---|
| `GME_GITHUB_TOKEN` | — | required for live GitHub provider |
| `API_TOKEN` | — | bearer token guarding `/v2/extract` and `/v2/jobs/{id}`. Fails closed: missing → 503 (no dev bypass). `/`, `/docs`, `/v2/health` stay open. Generate with `python -c "import secrets; print(secrets.token_urlsafe(32))"`. |
| `RCP_TOKEN` / `OPENAI_API_KEY` / `OPENROUTER_API_KEY` | — | one is required for LLM mode |
| `INFOSCIENCE_TOKEN` | unset | only for protected Infoscience routes |
| `SELENIUM_REMOTE_URL` | unset | enables Selenium-backed link veracity + selenium-fetch tool |
| `V2_AGENT_RUNTIME_DEFAULT` | `llm` | default runtime when `/v2/extract` omits `agent_runtime` |
| `V2_USE_MOCK_PROVIDERS` | `true` | swap in mock GitHub/ORCID/Infoscience/ROR providers |
| `V2_LINK_VERACITY_ENABLED` | `true` | turn off to skip the link-veracity stage in LLM mode (rule-based mode skips unconditionally) |
| `V2_CONTEXT_SUMMARY_SCOUT_MODE` | `false` | upgrade `context_summary` LLM stage to scout mode: broad RAG-search toolkit (orcid/ror/infoscience/openalex/zenodo/ethz/huggingface/renkulab/snsf/epfl_graph + selenium_fetch) on top of the legacy `grep_repository_corpus` + DuckDuckGo pair, plus a structured-brief prompt with explicit People / Organizations / Articles / Affiliations / Caveats sections. Per-entity LLM agents (person, org, article, membership, contribution) automatically benefit since they already consume the `summary_markdown`. Trade-off: heavier upfront LLM call, but per-entity calls send less context and duplicate ORCID/ROR lookups across entities collapse into the scout's shared brief. |
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
| `V2_EPFL_GRAPH_RAG_ENABLED` | `true` | enables the EPFL Graph disciplines RAG search tool (`search_epfl_graph_disciplines`). Single Qdrant collection `epfl_graph_disciplines` over the curated EPFL Graph academic-discipline ontology (~2226 categories, depth 1..5, embeddings built from `name + canonical Wikipedia lead-section + top anchor concept names`). Wired into the repository, person, organization, and article LLM agents. Refresh with `just epfl-graph-{ingest,enrich-wikipedia,embed}`. See [`docs/epfl-graph-disciplines.md`](https://github.com/sdsc-ordes/open-pulse-sources/blob/main/docs/epfl-graph-disciplines.md). |
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

Required environment **for serving requests**: `GME_GITHUB_TOKEN` and at least
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

## Cross-repo version pin

The `open_pulse_sources` library version lives in **exactly one place**: the
`open-pulse-sources @ git+https://github.com/sdsc-ordes/open-pulse-sources@<tag>`
entry in `pyproject.toml` `dependencies`. Every install path inherits it —
`just install-dev`, `just install`, CI, and `tools/image/Dockerfile`.

- Never add a second `pip install "open-pulse-sources @ git+…@<tag>"` anywhere;
  `tests/v2/test_open_pulse_sources_pin.py` fails on hardcoded second pins.
- The `gme-sources` image tag in `tools/deploy/docker-compose.yml` must match
  the library pin (same test enforces it). The library reads and that service
  writes the *same* DuckDB/Qdrant stores — skew corrupts shared state.
- Pins must be immutable: a release tag (`vX.Y.Z`) or a full commit SHA.
  `main` / `latest` defaults fail the test.
- Bumping the child = edit the pyproject pin + the compose image tag + add a
  README compatibility-matrix row.
- `OPEN_PULSE_SOURCES_REF` (Dockerfile build arg) defaults to **empty** and is
  an override-only escape hatch for testing unreleased child revisions.
- For cross-repo development, `just install-dev` re-installs a checkout at
  `./open-pulse-sources` or `../open-pulse-sources` as editable.

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

1. `git_metadata_extractor/schema/json/{type}/{entity}.schema.json` (source)
2. `dev/ontology-v2-json-response/a-001/json-schema/{type}/pulse_{Entity}Shape.schema.json` (promoted)
3. `tests/v2/fixtures/schema/{type}/{entity}.schema.json` (test fixture)

After any schema edit: copy to all three and run `just v2-models-generate`
to regenerate Pydantic models in `git_metadata_extractor/schema/models/`. `just v2-models-check`
in CI catches drift.

The check regenerates and compares **byte-for-byte**, so the codegen toolchain
is part of the contract: `datamodel-code-generator` and `ruff` are pinned
exactly in the `dev` extra. Bump them deliberately and regenerate in the same
commit — a range would let any upstream formatting change turn the gate red on
an unrelated PR.

## Identifier conventions

- **Person**: `https://orcid.org/{orcid}` when ORCID known, else `https://github.com/{login}`, else `urn:pulse:{uuid}`
- **Organization**: `https://ror.org/{id}` when ROR known, else `https://github.com/{handle}`, else `urn:pulse:{uuid}`
- **Repository**: `https://github.com/{owner}/{name}`
- **Article**: `https://doi.org/{doi}` when DOI known
- **Membership**: `{person_id}__{org_id}` composite (**double** underscore)
- **Contribution**: `{person_id}__{repo_id}` composite (**double** underscore)

Composites are built with `__` (see `rule_based/membership_agent.py:431`,
`contribution_agent.py:355`, `ownership_check.py:1801`,
`resolve_bio_to_ror_llm.py:209`). `_extract_composite_pair` still accepts a
single `_` when parsing, for back-compat with graphs produced before the
convention changed — never emit that form in new code.

`identifiers.uuid` is always a server-generated UUIDv4 (via
`git_metadata_extractor/agents/models.py::generate_uuid()`); the LLM never controls it.

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
