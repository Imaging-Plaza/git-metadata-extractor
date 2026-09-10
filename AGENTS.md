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
    run.py                       # the post-agent sequence, as ordered chains
    runner.py                    # one Stage signature + the loop that runs them
    state.py                     # PipelineState: the value the chains thread
    stages/                      # the actual stages — see "Pipeline" below
      raw_projection.py          # flat v2 -> the v3 *raw* shapes
      canonical_projection.py    # flat v2 -> the v3 *canonical* shapes
      substrate.py               # the raw layer grouped into named graphs
      extraction_run.py          # pulse:ExtractionRun + prov:SoftwareAgent

  store/                         # the WRITE layer, counterpart to providers/
    oxigraph.py                  # load quads, run SPARQL, check health
    terms.py                     # SPARQL term serialisation + the prefix table.
                                 #   The one place an injection can happen, so
                                 #   it is written and tested once

  unify/                         # store-side unification: substrate -> canonical
    policy.py                    # per-type match keys + per-property dispositions
    cluster.py                   # union-find over match keys (type-agnostic)
    merge.py                     # union / select / drop, and why (type-agnostic)
    remap.py                     # rewrite refs, composite ids and selections
    provenance.py                # the decisions, as RDF-star in graph:prov
    runner.py                    # read the store, decide, write both graphs

  schema/                        # JSON Schemas (agent + strict) + JSON-LD context
  validation/                    # strict-schema + SHACL validators
    layers.py                    # which shape set validates which layer,
                                 #   and which layer refuses vs reports
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
build_jsonld_output            v2-shaped JSON-LD graph; strips redundant
                               pulse:ror from any org:Organization whose @id is
                               already the ROR (closed-shape violation fix)
substrate_projection [gated]   project the SAME intermediate into the v3 raw
                               shapes, grouped into one named graph per
                               pulse:ExtractionOutput (one platform's slice of
                               one run). Must run before canonical_projection,
                               which overwrites the intermediate both read
                               (off by default: V2_SUBSTRATE_ENABLED)
canonical_projection           project the graph into the v3 canonical shapes
                               and swap in the generated @context; the v2 graph
                               above becomes an internal intermediate
                               (V2_CANONICAL_OUTPUT_ENABLED, default true)
shacl_gate                     SHACL validation — warning-only (see below).
                               Validates against the v3 canonical shapes when
                               the projection ran, the v2.1.2 bundle otherwise
extraction_run                 describe this run as a pulse:ExtractionRun +
                               prov:SoftwareAgent, returned beside `output`
substrate_write     [gated]    fold the run descriptor into the substrate's
                               meta graph and POST the quads to Oxigraph.
                               Last, because the run descriptor only exists
                               once extraction_run has run
                               (V2_SUBSTRATE_ENABLED + V2_SUBSTRATE_STORE_URL)
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
- `substrate_projection` / `substrate_write` are **off by default**. Set
  `V2_SUBSTRATE_ENABLED=true` to project the raw layer; the write additionally
  needs `V2_SUBSTRATE_STORE_URL`. With the flag on and no store URL the
  substrate is projected and returned but stored nowhere — the useful middle
  state for inspecting the layer. Both fail open: an unreachable store costs a
  warning, never the graph.
- `concept_tagging` is **off by default**. Set `V2_CONCEPT_TAGGING_ENABLED=true` to opt in. Backends are pluggable via `V2_CONCEPT_TAGGING_BACKEND` ∈ {`epfl_graph` (default, calls graphai), `wikipedia` (credential-free MediaWiki opensearch), `llm` (pydantic-ai)}. Stamps `_concepts` / `_keywords` / `_disciplines` as internal `_*` metadata (stripped before JSON-LD output and strict validation). Optional OpenAlex enrichment per discipline via `V2_CONCEPT_TAGGING_OPENALEX_RELATED_ENABLED=true` (publications, people, units). Full reference at [`docs/concept-tagging.md`](docs/concept-tagging.md).

**Hallucination guards baked into agents:**

- `force_server_uuid` overwrites whatever UUID the LLM emitted with a server-generated one in `identifiers.uuid` only — never as a top-level field (additionalProperties violations).
- Repository agent post-LLM: stars/forks from GitHub REST (deterministic, not LLM-derived); `pulse:repositoryType` keyword heuristic in rule-based mode. **No discipline fallback** — both the LLM and rule-based agents leave `pulse:discipline` empty when no domain signal is found. `pulse:DisciplineShape` has no `sh:minCount`, so `[]` validates. The former `wd:Q428691` (computer engineering) catch-all was removed because it hid the absence of signal: 77% of a 441-repo batch carried it *only*.
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

## Output shape (v3 canonical, since 2026-09-08)

`/v2/extract` returns the graph in the **v3 canonical shapes**. What changed
from the v2 form:

- **Identity is a profile, not a field.** A Person's `pulse:githubUsername` is
  gone; it is now `pulse:hasProfile` → a `pulse:PlatformProfile` node carrying
  `pulse:platform` + `pulse:platformUsername`. Organizations use
  `pulse:hasOrganizationProfile` → `pulse:OrganizationProfile`. This is what
  lets one person's GitHub and ORCID identities be linked without collapsing
  them, and it is what the store-side unifier keys on.
- **Repositories are not profiles.** `pulse:platform` and
  `pulse:repositoryHandle` sit directly on `schema:SoftwareSourceCode`;
  `pulse:githubRepoStars` / `Forks` are renamed `pulse:repositoryStars` /
  `repositoryForks`.
- **Identifiers go bare**: `pulse:orcidIdentifier` is `0000-...`,
  `pulse:doi` is `10.x/y`, `pulse:repositoryHandle` is `owner/name`.
  `pulse:ror` deliberately stays a URL.
- **Articles carry a `pulse:Deposit`**, which holds `schema:datePublished` —
  `ArticleShape` has no date of its own.
- **Contributions are in the substrate** (since 2026-09-09, ontology patch 06).
They were excluded as "derived", on the premise that the pipeline computes
commit counts — it does not: `contribution_agent.py` reads GitHub's
`contributions` field, so the count is reported and belongs in the layer that
records what sources said. The canonical `ContributionShape` requires it
(`sh:minCount 1`), so with the count absent from the substrate the store-side
canonical graph had **zero** contributions while `/v2/extract` returned 46.
What is genuinely derived is the aggregate across platforms and runs, which is
the unifier's `MAX`.

**Not carried by the canonical layer**: follower counts, biographies,
  locations, avatars, a person's homepage. These are not lost from the
  ontology — `RawPlatformProfileShape` declares `pulse:followerCount`,
  `pulse:biography`, `pulse:location`, `pulse:company`, `pulse:socialLink`,
  `schema:image`, `schema:url` and more. They belong to the **raw** layer,
  which `V2_SUBSTRATE_ENABLED` now emits (see "Substrate layer" below) — so
  they are dropped from `output` but not lost when the substrate is on. The
  `affiliations` context alias is genuinely gone.

The `@context` is now generated from the SHACL shapes
(`schema/generated/context.jsonld`). The hand-written v2 context is still used
*internally* — `build_jsonld_output` reads it to decide which values serialise
as `{"@id": ...}` references, and it describes the intermediate the projection
consumes.

Measured 120/120 SHACL-conformant against `ontology-shapes-canonical.ttl` over
the 120-repo corpus. Regenerate the measurement with
`python scripts/v2/canonical_conformance.py <corpus dir>`.

## Substrate layer (opt-in, since 2026-09-09)

`PROVENANCE_ARCHITECTURE.md` phase 3. With `V2_SUBSTRATE_ENABLED=true`,
`/v2/extract` also returns a `substrate` field: the same extraction in the v3
**raw** shapes, grouped into named graphs.

```json
{
  "output": { "@context": {...}, "@graph": [ ... ] },
  "extraction_run": { "@graph": [ <ExtractionRun>, <SoftwareAgent> ] },
  "substrate": {
    "@context": {...},
    "@graph": [
      { "@id": "urn:pulse:output:<run>:github",      "@graph": [ ... ] },
      { "@id": "urn:pulse:output:<run>:ror",         "@graph": [ ... ] },
      { "@id": "urn:pulse:run:<run>#meta",           "@graph": [ ... ] }
    ]
  }
}
```

Four things about it are load-bearing:

- **The unit of grouping is one platform's slice of one run**, not one source.
  That comes from the shapes: `pulse:partOfRun` points at a
  `pulse:ExtractionOutput`, which carries `prov:wasGeneratedBy` → the
  `ExtractionRun` and `pulse:platform`. So `extraction_output_iri` mints the
  IRI and the grouping only has to route to it.
- **The graph IRIs embed the run id**, which is what makes the substrate
  append-only. A second run over the same repository writes beside the first
  rather than over it — "raw assertions, never rewritten" is enforced by the
  IRI, not by the writer.
- **It is a projection of the same intermediate `output` is**, not a step
  before or after it. `substrate_projection` therefore has to run *before*
  `canonical_projection`, which replaces `state.payload["@graph"]` in place.
- **The meta graph holds only the extraction's own description** — the
  outputs, the run, the agent. An *entity* there is a routing defect, and
  SHACL cannot see it: the raw entity shapes are open and require no anchor,
  so a stranded entity conforms perfectly while claiming a fact about the
  world is a fact about the run. `canonical_conformance.py --layer substrate`
  counts them for exactly that reason.

Measured 119/119 conformant against `ontology-shapes-raw.ttl` with **zero
stranded entities**, 434 flat nodes → 1,021 raw nodes across 2-4 named graphs
per run. Regenerate with:

```bash
python scripts/v2/canonical_conformance.py <corpus dir> --layer substrate
```

**A pipeline-cache hit writes nothing to the store.** The cache-hit branch in
`api/extract.py` returns the stored response before the stage chain runs, so
`substrate_write` never executes — and the replayed `substrate` field carries
the *original* run's graph IRIs. That is consistent with `extraction_run`
(provenance records when the data was produced, and a cache hit produced
nothing new), but it means accumulating a corpus into the store needs
`V2_PIPELINE_CACHE_ENABLED=false`, or `refresh` per request. A cached URL will
otherwise look extracted and be absent from the store.

`V2_SUBSTRATE_STORE_URL` additionally POSTs each run's quads to Oxigraph
(`gme-oxigraph` in `tools/deploy/docker-compose.yml`). One request per run:
`POST /store` with **no** `graph` parameter and an N-Quads body dispatches each
quad to the graph its fourth term names. Adding `?graph=` would silently
retarget every quad into a single named graph — the whole layer collapsed, and
still a 204.

## Unification (opt-in, since 2026-09-09)

`PROVENANCE_ARCHITECTURE.md` phase 4. The substrate accumulates one named graph
per `(platform x run)`; the unifier reads all of it, collapses records that
describe the same entity, and writes `urn:pulse:graph:canonical`.

```bash
# report what unification would do, touching nothing
python scripts/v2/unify.py http://localhost:7878 --dry-run

# do it
python scripts/v2/unify.py http://localhost:7878
```

A script rather than an endpoint: the query/provenance API is phase 7, and
`unify.runner.unify_store` is the entry point either way.

**Split as the architecture doc specifies** — a type-agnostic layer plus a thin
per-type resolver, which is the only part that varies:

| Module | Varies by type? | Job |
|---|---|---|
| `policy.py` | **yes** | match keys, id promotion, per-property disposition |
| `cluster.py` | no | union-find over the keys |
| `merge.py` | no | union / select / drop, and a `Selection` explaining each choice |
| `remap.py` | no | rewrite references and composite ids after a rename |

**Extraction keeps its own identity resolution.** The decision (2026-09-09) is
that the unifier writes *beside* `canonicalization/id_resolution.py` rather
than replacing it: same `ORCID -> ROR -> handle -> urn:pulse:{uuid}` priority
on both sides, so they agree wherever they saw the same evidence, and
`pulse:samePersonAs` / `pulse:sameOrganizationAs` bridge the divergence where
the unifier saw more. That keeps `/v2/extract`'s contract intact and makes
ontology patch 02 load-bearing for the first time.

**Three dispositions, and the interesting one is not `SELECT`:**

- `UNION` — accumulate. `pulse:owns`, `org:hasUnit`, `pulse:hasProfile`. This
  is where the value is: EPFL's ten units are scattered across ten runs and
  only the union knows it has ten.
- `SELECT` — one winner, with the losers and the rule recorded for the
  provenance writer. `schema:name`, `pulse:ror`, `pulse:doi`.
- `MAX` / `MIN` — an extremum, for a value that is neither accumulated nor
  chosen. `pulse:contributionCount` is GitHub's *running total* for a person
  on a repository, so two runs a week apart report 40 then 43 — **not** 40 and
  3 more, which is why this is not a `SUM`. `pulse:firstContributionDate` is
  `MIN`, `lastContributionDate` is `MAX`.
- `PER_RUN` — never reaches canonical. `pulse:partOfRun` is the substrate's
  anchor and differs per run *because* that is its job.

The dispositions **cannot be read off the shapes**: 45 of 78 canonical
properties carry no `sh:maxCount`, and among them `schema:name` is
semantically single while `pulse:owns` is genuinely many. So `policy.py`
classifies them by hand and `tests/v2/test_unify_policy.py` guards the table
against the real TTL — no capped property may be `UNION`, and every uncapped
one must have an entry. Exactly one property is *shape-dependent*
(`schema:author`: capped on `pulse:Contribution`, unbounded on articles and
repositories), which is why `single_valued_for(entity_type)` reads the
generated per-type cap sets rather than a global set.

**Measured over the 119-run corpus:** 670 records from 185 substrate graphs →
633 clusters, **16 cross-run**, 0 renames, **0 contested values**. Every
cross-run difference in the real corpus is either `pulse:partOfRun` or set
accumulation — so the `SELECT` path has no corpus coverage and is tested
against purpose-built fixtures instead. `graph:canonical` comes out at 3,278
triples and **0 SHACL violations** against `ontology-shapes-canonical.ttl`, and
unification is idempotent (three passes, byte-identical).

**`graph:canonical` is replaced, not merged into.** Unification is a pure
function of the substrate, so the writer `DROP`s and rewrites it. Losing it
costs one re-run; losing the substrate — which is append-only and never
rewritten — loses data.

## Provenance (`graph:prov`, since 2026-09-09)

`PROVENANCE_ARCHITECTURE.md` phase 5. A value the unifier *chose* is a derived
fact — not what any one source said, but what was decided among what several
said. `unify/provenance.py` records the decision on the triple itself, as
RDF-star:

```turtle
<< <https://orcid.org/0000-0002-1825-0097> schema:name "Jane Doe" >>
    prov:wasDerivedFrom   <urn:pulse:output:run-b:github> ;
    pulse:observationKind "most-complete-source" ;
    pulse:observedOn      "2026-09-09T11:00:00"^^xsd:dateTime ;
    pulse:firstObservedOn "2026-09-01T09:00:00"^^xsd:dateTime ;
    pulse:lastConfirmedOn "2026-09-09T11:00:00"^^xsd:dateTime ;
    pulse:observationCount 2 .
```

The vocabulary and that exact shape are the pinned ontology's, not this
repo's: `ontology-definitions-provenance.ttl` documents every annotation
property with a worked `<< ?s ?p ?o >>` example *and* the query to read it
back. Nothing here invents a term.

Four rules worth knowing:

- **RDF-star is store-only.** rdflib 6.3.2 cannot serialise a quoted triple and
  pyshacl 0.28.1 cannot validate one, so the writer emits `INSERT DATA` text
  through `store.update` rather than going through `substrate.to_nquads`.
  `graph:prov` is therefore never SHACL-validated and never serialised to
  JSON-LD — which is why it is not one of the shape sets. Individual *terms*
  are still serialised by rdflib (`Literal.n3()`), because escaping a
  `schema:name` that contains a quote, a brace or `<< >>` is not something to
  hand-roll over data from arbitrary repositories.
- **Derived-only.** An uncontested value is already attributed by the named
  graph it sits in, so only contested selections are recorded. The ontology's
  own example kind `"single-source"` therefore never appears.
- **Upsert, not replace** — unlike `graph:canonical`. `pulse:observationCount`
  and `pulse:firstObservedOn` are history: the writer reads them back and
  carries them forward, so a re-observation bumps the counter and keeps the
  first date. Superseded values are pruned, which discards their history —
  the "cheap corner" the cost profile chooses over a full audit trail.
- **`pulse:samePersonAs` / `sameOrganizationAs` live here as plain triples**,
  not on the canonical node. The ontology says so, and the shapes require it:
  `PersonShape` is `sh:closed` and ignores only `( rdf:type owl:sameAs )`, so a
  *subproperty* of `owl:sameAs` is rejected.
- **No `pulse:observationConfidence`.** Declared by the ontology, left empty on
  purpose: the only thing available to derive it from is the selection rule,
  which `pulse:observationKind` already states.

Verified against `ghcr.io/oxigraph/oxigraph:0.4.11` — insert, upsert with a
bumped counter, the ontology's documented read-back query, the prune, and
eight adversarial literals (quotes, backslashes, newlines, braces, a literal
`<< nested star >>`) all round-tripping. Built against fixtures by decision:
the 119-run corpus produces **zero** contested selections, so nothing real
exercises this until a second platform is harvested.

## Validation split (phase 6, since 2026-09-09)

Three layers, three contracts, one place that knows the pairing —
`validation/layers.py`. Getting it wrong is silent in both directions:
validating the substrate against the canonical shapes reports the raw layer's
deliberate openness as violations, and validating canonical against the raw
shapes checks almost nothing.

| Layer | Shapes | Closed? | On violation |
|---|---|---|---|
| substrate | `ontology-shapes-raw.ttl` + 4 | mostly open | **report** (`V2_SUBSTRATE_VALIDATE`) |
| canonical | `ontology-shapes-canonical.ttl` + 3 | closed | **refuse to publish** |
| provenance | — | — | cannot be validated at all |

**Why the severities differ.** The substrate is append-only and the only
durable copy of what a run found, so refusing a slice over one malformed entity
loses the rest of it permanently — and the raw shapes are open precisely so a
source can assert something canonical has no slot for. The canonical graph is
the opposite: a pure function of the substrate, rewritten every pass, and the
one consumers query. Refusing costs one re-run and leaves the previous valid
graph answering.

**The order is the whole point.** `write_canonical` validates *before* it
`DROP`s. Validate first and a bad pass leaves the previous graph intact;
validate after and a bad pass leaves nothing. Verified live: an invalid graph
is refused, `graph:canonical` keeps its 3 triples and still answers the query.
`unify.py --no-enforce` publishes anyway, for inspecting a broken graph; exit
code 3 marks a refusal.

**This gate is also the sufficiency check.** Each layer passing its own shapes
does not mean the substrate holds what canonical needs — the substrate was
119/119 raw-conformant while missing two things `ArticleShape` and
`OrganizationShape` require (§3j of `REFACTOR_HANDOFF.md`). The only way to
find that out is to build canonical *from* the substrate and validate the
result, which is exactly what this call does.

**`graph:prov` is structurally outside all of it.** pyshacl 0.28.1 cannot
target a quoted triple as a focus node, so no shape can ever apply to an
RDF-star annotation. `ontology-shapes-provenance.ttl` says so in its own
header.

Note the in-request `shacl_gate` stage stays **warning-only** and that is not
an inconsistency: there the alternative is returning nothing to a caller who
asked for a graph. Store-side, the alternative is leaving a valid graph in
place. Different trade, different answer.

## API surface

- `GET  /v2/health` — health check (open, no auth)
- `GET  /v2/graph/status` — what the accumulated store holds: graph counts,
  canonical vs provenance triples, runs, canonical entities by type
- `GET  /v2/graph/entity?iri=…` — one entity from `graph:canonical`, as a
  JSON-LD node in the same shape `/v2/extract` returns for it
- `GET  /v2/graph/provenance?subject=…[&property=…]` — for each *chosen*
  value: the `pulse:ExtractionOutput` it came from, the run that produced that
  output, the selection rule, and the observation counters. Plus the
  `owl:sameAs` identity links, which are plain triples in `graph:prov`
- `POST /v2/extract` — async job. Body: `{source_url, agent_runtime?, output_format?, include_context_summary?}`. Returns `{job_id, status, status_url}`; poll `GET /v2/jobs/{job_id}`.
- `GET  /v2/jobs/{job_id}` — job status + result when complete
- `GET  /v2/extract/{full_path:path}` — synchronous extract (single repo)
- `GET  /docs` — Swagger UI with auto/manual dark-mode toggle (override persisted in `localStorage`)

The three `/v2/graph/*` endpoints are **new**, not a reinterpretation of
`/v2/extract` — the decision on record is *"version the endpoint rather than
reinterpret it"*, so `/v2/extract` keeps meaning "extract this URL now" and
these mean "tell me what the store knows". All three need
`V2_SUBSTRATE_STORE_URL`; without it they answer **503**, not 404, because
"nothing about that IRI" and "no store" are different answers and a caller
that cannot tell them apart will cache the wrong one.

**No endpoint accepts a SPARQL string**, and that is a security property rather
than a simplification. Oxigraph ships no authentication and its `/store`
endpoint is writable by anyone who can reach it — which is why the compose
service is not port-published — so an endpoint that proxied a caller's query
would be an unauthenticated write primitive one `INSERT` away. Every query is
fixed text with IRIs substituted through `store.terms.iri_term`, which
**refuses** anything RDF forbids in an IRIREF rather than escaping it (a bad
IRI is a 400). `test_no_endpoint_accepts_a_sparql_string` asserts it over the
whole route table, so a new endpoint with a `query` parameter fails without
anyone remembering to add a test.

`/v2/graph/provenance` cannot be JSON-LD: `graph:prov` holds RDF-star quoted
triples and the pinned rdflib cannot serialise one, so it answers with a flat
list of records built from SPARQL bindings. That is permanent, not a stopgap.
An empty `records` list means **no value on that subject was contested**, not
that provenance is missing — which is what every subject answers on real data
today.

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
| `V2_CANONICAL_OUTPUT_ENABLED` | `true` | `/v2/extract` returns the graph in the **v3 canonical shapes** — platform profiles instead of flat handles, bare identifiers, deposits. Set `false` to restore the v2-shaped output without a redeploy. See "Output shape" below. |
| `V2_SUBSTRATE_ENABLED` | `false` | project the **raw/substrate layer**: the extracted entities in the v3 raw shapes, grouped into one named graph per `pulse:ExtractionOutput` (one platform's slice of one run) and returned as the `substrate` field beside `output`. Off by default — additive, and unconsumed until the store-side unifier. |
| `V2_SUBSTRATE_STORE_URL` | unset | Oxigraph server root, e.g. `http://gme-oxigraph:7878`. With `V2_SUBSTRATE_ENABLED` on, each run's named graphs are POSTed there as N-Quads. Unset means the substrate is projected and returned but stored nowhere. Fails open: an unreachable store adds a warning, never costs the caller a graph. |
| `V2_SUBSTRATE_VALIDATE` | `true` | validate each substrate slice against the **raw** shapes at write time. Reports, never refuses — the substrate is append-only and the only durable copy of what a run found, so losing a slice over one malformed entity is worse than keeping it. Turn off for a bulk backfill where the SHACL pass dominates. |
| `V2_SUBSTRATE_STORE_TIMEOUT_SECONDS` | `30` | HTTP timeout for the substrate write |
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

JSON Schemas live in **one** place:

    git_metadata_extractor/schema/json/{agent,strict}/{entity}.schema.json

These are the files the service opens at runtime —
`agents/models.py::load_agent_schema` and
`validation/schema_validation.py::_load_strict_schema`. Nothing copies them,
and nothing should: there were previously three copies kept byte-identical by
hand (source, a promoted set under `dev/`, and a test fixture set), and by the
time they were removed on 2026-09-08 four of the agent copies had drifted,
missing `pattern` constraints the real schemas carry. The whole test suite was
therefore asserting a laxer contract than production enforces, which hid five
invalid Person stubs per 120-repo corpus run.

After any schema edit, run `just v2-models-generate` to regenerate the Pydantic
models in `git_metadata_extractor/schema/models/`. `just v2-models-check` in CI
catches drift. The *ontology*-driven generator (`just ontology-models-generate`)
additionally emits `MODELS_BY_TARGET_CLASS` and
`SINGLE_VALUED_BY_TARGET_CLASS` per layer — the second is per-`sh:targetClass`
because exactly one canonical property has shape-dependent cardinality
(`schema:author`), and the unifier's merge policy has to honour it per type. Two invariants are enforced by `tests/v2/test_json_schemas.py`:
each schema is valid JSON Schema, and the agent (permissive) schema is a
superset of the strict schema's property names — a field strict demands but
agent omits is unreachable, since `validate_permissive` soft-drops what it does
not know about.

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
