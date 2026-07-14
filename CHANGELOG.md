# Changelog

All notable changes to this project will be documented in this file.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/). Output is aligned with **Open Pulse Ontology v2.1.2** (see `src/v2/schema/json/context/v2.0.jsonld`).

## [Unreleased]

### Removed (breaking — v1 API retired)

- **The legacy v1 API is gone.** All `/v1/*` routes (`/v1/repository/*`,
  `/v1/org/*`, `/v1/user/*`, `/v1/cache/*`) now return 404 — `src/v1/`,
  `tests/v1/`, the v1 justfile recipes, and the v1-only env knobs
  (`CACHE_DB_PATH`, `MAX_CACHE_ENTRIES`, `MAX_SELENIUM_SESSIONS`) were
  removed. Consumers: see `docs/migration-v1-to-v2.md` for the `/v1` → `/v2`
  endpoint mapping. Modules v2 shared with v1 moved into v2 homes:
  the LLM model config (`src/v2/agents/llm/model_config.py`), the gimie
  extraction intermediate (`src/v2/ingest/providers/gimie_extract.py`),
  the GitHub user/org parsers + models
  (`src/v2/ingest/github_accounts/`), and the Infoscience data models
  (`src/v2/ingest/infoscience_models.py`).

### Fixed

- **GIMIE extraction was silently dead in every sidecar deployment.** Two
  independent defects: (1) the client requested `/gimie/jsonld/…`, a route
  that never existed on gimie-api — it now fetches `/gimie/ttl/…` and
  converts with rdflib, byte-compatible with the historical in-process
  serialization; (2) the upstream `ghcr.io/sdsc-ordes/gimie-api` images
  (pinned digest and `:latest`) ship gimie 0.6.1 with an app written for
  the 0.7.x API, failing every request and JSON-encoding the error to an
  empty payload — replaced by a **GME-maintained sidecar**
  (`tools/gimie-api/`, gimie 0.7.2 pinned) that both compose stacks build.
  Also: the sidecar needs a **single** GitHub PAT (`GIMIE_ACCESS_TOKEN`)
  when `GME_GITHUB_TOKEN` is a comma-separated pool. Effect: repository
  descriptions, contributors, and the derived Person/Membership/
  Contribution entities are extracted again (verified live: 3 → 66
  entities on `sdsc-ordes/gimie`). See
  `dev/split-rag-indices/11-gimie-sidecar-jsonld-broken.md`.

### Changed (breaking — repo split)

- **The RAG index layer moved to its own repo/service:
  [open-pulse-sources](https://github.com/sdsc-ordes/open-pulse-sources).**
  `src/index/`, `src/module/`, `src/v2/indices/` and the `/v2/manifest` +
  `/v2/indices/*` API surface were removed from this repo; the same routes are
  now served by the `gme-sources` compose service (same paths, same auth).
  The v2 read-side RAG providers import the split-out code as the
  `open_pulse_sources` library (installed by `just install-dev`; baked into
  the Docker image from git). Extract-side auto-ingest is unchanged — it
  writes through the library into the same `data/index` + Qdrant stores.
  Index ops (ingest/embed/reset recipes, seeds, reingest/migration scripts,
  per-index docs) live in the new repo. `config/index/*.yaml` intentionally
  remains here: the library resolves config/data paths CWD-relative.

### Changed (breaking — deployment)

- **GIMIE moved to a sidecar; `gimie` dependency removed.** The `gimie` package
  (and its `calamus`/`marshmallow` chain, which hard-pinned vulnerable
  `python-dotenv<0.22` + `marshmallow<3.24`) is gone from the image. Repository
  GIMIE metadata is now fetched from the **`gimie-api` sidecar**
  (`ghcr.io/sdsc-ordes/gimie-api`) via HTTP. **Every deployment must run the
  sidecar and set `GIMIE_API_URL`** (e.g. `http://gme-gimie-api:15400`) — see
  the [operations runbook](docs/OPERATIONS_RUNBOOK.md). `extract_gimie` is now a
  single intermediate (sidecar when `GIMIE_API_URL` is set, else in-process gimie
  if separately installed). This frees `python-dotenv` (→1.2.2) and removes
  `marshmallow`, clearing the last 3 Dependabot alerts.

### Added

- **GitLab index family** — nine new RAG stores
  (`gitlab_{epfl,ethz,datascience}_{projects,groups,users}`) over the EPFL,
  ETHZ, and Datascience self-hosted GitLab instances, built on a shared
  `src/index/_gitlab_base/` engine (one REST v4 client + parallel
  project/group/user pipelines). All nine are vector-backed, registered in the
  federated layer, and appear in `GET /v2/manifest`. GitLab user records carry
  no ORCID (GitLab exposes no verified-ORCID field). See
  [`docs/gitlab-index.md`](https://github.com/sdsc-ordes/open-pulse-sources/blob/main/docs/gitlab-index.md).
- **HTTP ingest + search endpoints for the GitLab family** —
  `POST /v2/indices/<name>/ingest` (full-instance crawl + embed, async job;
  optional `limit`) and `POST /v2/indices/<name>/search` for all nine gitlab
  stores, at parity with the other indices.
- **Deploy-time index bootstrap** — the Gunicorn `on_starting` hook runs the
  federated bootstrap once in the master process before workers fork, so every
  index store exists with its schema before the first request. Idempotent and
  best-effort; toggle with `INDEX_BOOTSTRAP_ON_START` (default `true`).
- **LLM README enrichment** — a `repo_signals` refiner that reads the README to
  populate `gme-internal:hasDocumentation` (documentation URLs) and fill the
  test-coverage signal when the deterministic badge parse found none. Gated by
  `V2_REPO_SIGNALS_AGENT_MODE` (`apply`/`shadow`/`off`).
- **Repository enrichment fields (`gme-internal:*`)** — a broad deterministic
  layer surfaced under `?include_internal_fields=true`. See
  [`docs/repository-enrichment-fields.md`](docs/repository-enrichment-fields.md):
    - **Releases** — `release_count`, `first_release_date`, `latest_release_date`
      (+ raw `releases`, `latest_version`); **git tags** (`git_tags`,
      `git_tag_count`).
    - **Container distribution** — `docker_hub_url`; GHCR `package_count`,
      `package_names`, `package_image_refs`, `package_tags`,
      `latest_package_updated_at` (+ raw `container_images`).
    - **Published packages** for **npm, PyPI, conda, crates.io, RubyGems, Maven,
      Go, NuGet** — each with `_package`, `_latest_version`, `_versions`,
      `_latest_release_date`, `_registry_url`, and a `_link`
      (`verified`/`name_only`) back-reference (+ `conda_channel`,
      `maven_group_id`/`maven_artifact_id`). Discovered from manifests
      (`package.json`, `pyproject.toml`, `cargo.toml`, `pom.xml`, `go.mod`) and
      README badges; name-collision results are dropped.
    - **README badges** — `badges` (label/image/link) + `badge_count`.
    - **Funding** — `funding_urls` from `.github/FUNDING.yml`.
    - **Community health** — `community_health_percentage`,
      `has_code_of_conduct`, `has_issue_template`, `has_pull_request_template`.
    - **CI / coverage** — `has_ci`, `test_coverage`; governance-file URL
      pointers (`code_of_conduct_url`, `security_url`, …).
  - Public-registry queries gated by `V2_PACKAGE_REGISTRY_ENABLED` (default on).

### Fixed

- **Federated bootstrap `_LEAF_STORES`** — the gitlab `groups` (and now `users`)
  leaf stores were missing from the leaf-opener allowlist and silently
  bootstrapped as "skipped: no duckdb store"; all nine gitlab leaves now
  bootstrap correctly.

## [3.0.0rc1] — Proposed — Identifier URL canonicalisation + per-entity RAG indices (breaking)

> **Status: proposed.** This is the candidate for the next major release
> (v3.0.0), sitting above the released `2.1.0rc1` below. Breaking
> identifier-shape and env-var changes warrant the major bump. Nothing
> here is tagged yet; the version string is `3.0.0rc1`.

This release standardises **every external identifier** to its canonical
HTTPS URL form, end-to-end. Previously the codebase carried a split
convention: ROR was URL-form, DOI/ORCID/Infoscience/GitHub were bare.
All identifiers now match.

### Fixed — read-only DuckDB snapshot (Hub 404s from write-lock contention)

The serving process holds a persistent **read-write** DuckDB handle on
every v2-ingest provider's store (cached on `app.state`). DuckDB allows
N readers **or** 1 writer — so a separate process (the Hub) opening the
live file read-only to sniff the schema failed with a lock conflict, the
collection never registered, and queries 404'd.

Fix (zero-contention via separate files): after each ingest+embed job,
`run_embed_step` publishes a read-only copy to `<provider>.ro.duckdb`
(`src/index/_snapshot.py`) — `CHECKPOINT`, copy the data tables into a
temp DB via `CREATE TABLE … AS SELECT`, atomic `os.replace`. The heavy
`chunks` table is skipped. The Hub points at the `.ro.duckdb` snapshot;
the live file stays owned solely by GME (which reads it in-process via
its own writer connection — no change to serving). Writer and readers
operate on different files → zero contention, even mid-ingest.

- Toggles: `INDEX_DUCKDB_SNAPSHOT` (default on),
  `INDEX_SNAPSHOT_MIN_INTERVAL_SECONDS` (debounce for large stores).
- `reset` deletes the snapshot alongside the live DB.
- The CLI-managed catalogs (ror/infoscience/snsf/epfl_graph/
  zenodo_communities) and ethz already serve read-only-per-request /
  config-only, so they never held the lock and need no snapshot.
- **Hub-side change required:** point its read-only opens at
  `<provider>.ro.duckdb` instead of the live `<provider>.duckdb`.

### Fixed — SHACL gate ships its ontology + invalid `pulse:Company` enum

- **SHACL gate was dead in the container.** The open-pulse ontology TTL
  lived under `dev/`, which the Docker image does not copy, so
  `ontology_ttl_path()` resolved to `/app/dev/…` → `FileNotFoundError`
  and the SHACL gate never ran in production. Moved the TTL into the
  package (`src/v2/validation/open-pulse-ontology-v2.1.2.ttl`, shipped by
  `COPY src` and via `[tool.setuptools.package-data]`). Resolution is now
  a chain: `GME_ONTOLOGY_TTL` env override → packaged copy → `dev/`
  source-checkout fallback, with an informative error listing all three.
- **Invalid `pulse:OrganizationType` value.** Two LLM refiners
  (`discovery`, `org_resolver`) could emit `pulse:Company`, which is not
  a member of `pulse:OrganizationTypeEnumeration` (the ontology defines
  `pulse:PrivateCompany`). `org_resolver` even allowed it via its
  `Literal` with no normalisation, so it reached the graph and failed
  `sh:class` (`ClassConstraintComponent`) even when the ontology was
  loaded. Removed `pulse:Company` from both prompts + the Literal;
  refiners now emit only the 8 real enum members.

Note for downstream SHACL validators: the remaining bulk of
`ClassConstraintComponent` findings on `pulse:repositoryType` /
`pulse:OrganizationType` / `pulse:discipline` are **not** output defects
— those values are enum IRIs whose class-membership triples live in the
ontology. Validate `data + ontology` (load the TTL as `ont_graph`, as the
in-pipeline gate does); validating data-only reports them spuriously.

### Added — `GET /v2/crawl/{job_id}` extract-job status endpoint

Lightweight status endpoint for async extract jobs, for cheap polling
and parity with the v1 crawl-status surface. Returns just the lifecycle
fields (`status` + timestamps + `error`) plus a `result_url` pointing at
the full record/graph — previously a job's status could only be read
from the `status` field buried inside the full `GET /v2/jobs/{job_id}`
response. Shares the same store lookup + orphaned-job (stale-heartbeat)
detection as `/v2/jobs/{job_id}` via extracted helpers, so both agree on
liveness; 503/404 behaviour matches.

### Added — `dockerhub` RAG index

New per-provider index for **Docker Hub repositories (images)**, with
full parity to the existing indices: dedicated DuckDB store + `dockerhub`
Qdrant collection, `POST /v2/indices/dockerhub/{ingest,search}` routes
(ingest chains the embed step + WAL checkpoint like the others),
federated search/lookup adapter, reset spec, `IndexName` enum entry,
`seeds/dockerhub.txt`, and a `dockerhub` entry in the cold-start
re-ingest driver.

- One row per `namespace/name` (official images under `library/`);
  metadata from the public Docker Hub v2 API
  (`https://hub.docker.com/v2/repositories/{namespace}/{name}`), which
  serves public repos anonymously. `DOCKERHUB_TOKEN` is optional (raises
  the rate limit only).
- Ingest accepts flexible references: `namespace/name`, bare official
  names, `hub.docker.com/r/…` and `/_/…` URLs, and `docker.io/…` pull
  refs (any `:tag` is dropped — repositories are the indexed unit).
- Embedding text = `repo_id` + short description + `full_description`
  (README); tags / pull_count / star_count ride in the payload.

### Added — Repository releases + GHCR container images

The repository extractor now surfaces a repo's **published releases**
and the **GHCR container (Docker) images** built from it:

- `GitHubProvider.get_repository_releases` — thinned release list
  (tag, name, dates, draft/prerelease flags, assets) from
  `/repos/{owner}/{repo}/releases`. Public endpoint, no extra scope.
- `GitHubProvider.get_repository_container_images` — owner-scoped
  `container` packages filtered to those linked to (or named after)
  the repo, each with its `ghcr.io/...` reference and version tags.
  Requires the `read:packages` token scope; degrades to an empty list
  without it.
- `context_gather` fetches both (best-effort, like aux-files) and the
  repository agent stamps them onto the internal `_releases` /
  `_container_images` fields (surfaced when
  `include_internal_fields=true`).

Layer 1 only: the Pulse ontology has no predicate for releases or
container images yet (v1 carried a never-shipped `hasSoftwareImage`),
so these ride under the `_`-prefix convention. Promoting them to
canonical `schema:`/`pulse:` terms is a tracked v3.0.0 ontology
follow-up.

### Breaking — Persisted identifier shapes

Every `pulse:*Identifier` / `pulse:github*Handle` field now stores the
canonical URL form. The wire-input layer accepts either shape (bare or
URL) on ingest; persisted output is always URL.

| Property                                       | v2.1.x (bare)                    | v3.0.0 (URL)                                                                     |
|-----------------------------------------------|----------------------------------|----------------------------------------------------------------------------------|
| `schema:identifier` (DOI on Article)           | `10.1038/s41586-024-...`         | `https://doi.org/10.1038/s41586-024-...`                                         |
| `pulse:orcidIdentifier` / `pulse:orcid`        | `0000-0001-2345-6789`            | `https://orcid.org/0000-0001-2345-6789`                                          |
| `pulse:infosciencePersonIdentifier`            | `f97b60da-...`                   | `https://infoscience.epfl.ch/entities/person/f97b60da-...`                       |
| `pulse:infoscienceOrganizationIdentifier`      | `95372c6b-...`                   | `https://infoscience.epfl.ch/entities/orgunit/95372c6b-...`                      |
| `pulse:infoscienceArticleIdentifier`           | `dbce93b0-...`                   | `https://infoscience.epfl.ch/entities/publication/dbce93b0-...`                  |
| `pulse:githubUsername`                         | `caviri`                         | `https://github.com/caviri`                                                      |
| `pulse:githubOrganizationHandle`               | `EPFL-ENAC`                      | `https://github.com/EPFL-ENAC`                                                   |
| `pulse:githubRepositoryHandle`                 | `EPFL-ENAC/geodata-toolkit`      | `https://github.com/EPFL-ENAC/geodata-toolkit`                                   |

ROR was already URL-form; unchanged.

### Breaking — Resolved `id` values

`resolve_*_id()` helpers now produce the canonical URL for every
identifier source (not just ROR / DOI). Composite IDs in Membership
(`{personId}__{orgId}`) and Contribution (`{personId}__{repoId}`)
therefore carry URLs on both sides:

  `https://orcid.org/0000-0001-2345-6789__https://ror.org/02s376052`
  `https://orcid.org/0000-0001-2345-6789__https://github.com/EPFL-ENAC/geodata-toolkit`

### SPARQL migration

Existing graph stores carry the old (bare) values. To migrate:

```sparql
# DOI (schema:identifier on ScholarlyArticle)
DELETE { ?article schema:identifier ?bare }
INSERT { ?article schema:identifier ?url }
WHERE  { ?article a schema:ScholarlyArticle ; schema:identifier ?bare .
         FILTER(STRSTARTS(STR(?bare), "10."))
         BIND(IRI(CONCAT("https://doi.org/", STR(?bare))) AS ?url) }

# ORCID
DELETE { ?person pulse:orcidIdentifier ?bare }
INSERT { ?person pulse:orcidIdentifier ?url }
WHERE  { ?person pulse:orcidIdentifier ?bare .
         FILTER(REGEX(STR(?bare), "^[0-9]{4}-[0-9]{4}-[0-9]{4}-[0-9]{3}[0-9X]$"))
         BIND(IRI(CONCAT("https://orcid.org/", STR(?bare))) AS ?url) }

# Infoscience Person
DELETE { ?p pulse:infosciencePersonIdentifier ?bare }
INSERT { ?p pulse:infosciencePersonIdentifier ?url }
WHERE  { ?p pulse:infosciencePersonIdentifier ?bare .
         FILTER(REGEX(STR(?bare), "^[0-9a-f]{8}-"))
         BIND(IRI(CONCAT("https://infoscience.epfl.ch/entities/person/", STR(?bare))) AS ?url) }

# Infoscience Organization
DELETE { ?o pulse:infoscienceOrganizationIdentifier ?bare }
INSERT { ?o pulse:infoscienceOrganizationIdentifier ?url }
WHERE  { ?o pulse:infoscienceOrganizationIdentifier ?bare .
         FILTER(REGEX(STR(?bare), "^[0-9a-f]{8}-"))
         BIND(IRI(CONCAT("https://infoscience.epfl.ch/entities/orgunit/", STR(?bare))) AS ?url) }

# Infoscience Article
DELETE { ?a pulse:infoscienceArticleIdentifier ?bare }
INSERT { ?a pulse:infoscienceArticleIdentifier ?url }
WHERE  { ?a pulse:infoscienceArticleIdentifier ?bare .
         FILTER(REGEX(STR(?bare), "^[0-9a-f]{8}-"))
         BIND(IRI(CONCAT("https://infoscience.epfl.ch/entities/publication/", STR(?bare))) AS ?url) }

# GitHub user / org handles (same URL shape)
DELETE { ?s ?p ?bare }
INSERT { ?s ?p ?url }
WHERE  { VALUES ?p { pulse:githubUsername pulse:githubOrganizationHandle }
         ?s ?p ?bare .
         FILTER(REGEX(STR(?bare), "^[A-Za-z0-9][A-Za-z0-9-]{0,38}$"))
         BIND(IRI(CONCAT("https://github.com/", STR(?bare))) AS ?url) }

# GitHub repository handle
DELETE { ?r pulse:githubRepositoryHandle ?bare }
INSERT { ?r pulse:githubRepositoryHandle ?url }
WHERE  { ?r pulse:githubRepositoryHandle ?bare .
         FILTER(REGEX(STR(?bare), "^[A-Za-z0-9][A-Za-z0-9-]{0,38}/[A-Za-z0-9_.][A-Za-z0-9_.-]{0,99}$"))
         BIND(IRI(CONCAT("https://github.com/", STR(?bare))) AS ?url) }
```

### Added

- `src/v2/canonicalization/{doi,orcid,infoscience,github}.py` — shared
  identifier helpers (`*_iri()` to build the canonical URL, `parse_*`
  to extract the bare form). Each helper is idempotent on canonical
  input and tolerates every wire shape that arrived during the
  v2.1.x lifetime.

### Changed — SHACL ontology

- `dev/ontology-v2-json-response/open-pulse-ontology-v2.1.2.ttl`:
  `sh:pattern` on all eight identifier shapes (DOI, ORCID, Infoscience
  person/org/article, GitHub username/org/repo) now constrains the
  URL form. ROR was already URL-form; unchanged.

### Changed — Pipeline

- Rule-based and LLM agents stamp identifiers in URL form via the
  canonicalisation helpers.
- `reconciliation` promotes pre-resolved bare identifiers to URL form
  on ingest, normalises legacy `core/items/<uuid>` URLs to the
  `entities/<kind>/<uuid>` canonical form, and registers bare-shape
  aliases in the entity-lookup tables so cross-references in either
  shape resolve correctly.
- `id_resolution.resolve_*_id()` returns the canonical URL on every
  resolution path (no more f-string concatenation against base URIs).
- `ownership_check._entity_owner_handle` returns the bare GitHub
  handle regardless of whether the persisted property carries the
  URL form or the legacy bare shape — keeps `pulse:owns` owner-equality
  checks correct post-migration.

## [2.1.0rc1] — 2026-05-28

First release candidate for v2.1.0. Consolidates the v2 pipeline buildout, the nine RAG indices, the LLM agent toolkit, and the affiliation / publiccode / repo-aux-file work into a tagged release. **All `[Unpublished]` entries previously at the top of this file are part of this release** and are preserved verbatim below.

### Highlights

- **Membership-based affiliation resolution** — three-stage ladder (`resolve_company_to_ror` → `resolve_bio_to_ror` → `resolve_bio_to_ror_llm`) that materialises proper `org:Membership` + `org:Organization` entities into the reconciled graph instead of stamping `schema:affiliation` on Person. Keeps JSON-LD output ontology-pure.
- **Repository supplementary metadata** — parses `publiccode.yml` (v0.2 / v0.3 / v0.4) into a typed `_publiccode` field; emits URL pointers for `CITATION.cff` / `AUTHORS` / `CONTRIBUTING.md` / `publiccode.yml`; forwards their content excerpts to the LLM refiners.
- **`publiccode:` JSON-LD namespace** — scalar/list top-level publiccode fields hoisted to `publiccode:<field>` predicates when `include_internal_fields=true`, so SPARQL queries hit them directly.
- **Operational hardening** — DuckDB IRI-migration CTAS-swap now atomic (BEGIN/COMMIT). Six new GitHub-org REST fields surfaced (`_is_verified`, `_archived_at`, `_public_gists`, `_following_count`, `_has_organization_projects`, `_has_repository_projects`).
- **Public documentation** — `docs/v2-pipeline.md` (operator-facing overview with node-graph, load-bearing assumptions, affiliation strategy, env-flag reference). README rewritten for v2-first with worked extract example and production-use callout (`imagingplaza.epfl.ch`, `openpulse.science`).

### Added (since v2.0.1, late-release additions)

- New pipeline stage `resolve_company_to_ror` (`src/v2/pipeline/stages/resolve_company_to_ror.py`): reads each Person's `_company` field, queries the ROR RAG, and materialises a `org:Membership` + `org:Organization` stub when a hit clears the strict acceptance gate (score ≥ 0.55, type in {company, education, funder, facility, government, nonprofit}, gap ≥ 0.02 OR exact-name override). Idempotent across re-runs. Gated by `V2_RESOLVE_COMPANY_TO_ROR` (default `true`).
- New pipeline stage `resolve_bio_to_ror` (`src/v2/pipeline/stages/resolve_bio_to_ror.py`): deterministic backstop for persons the company stage missed. Regex extraction over `_bio` / `_orcid_biography` (`at <Org>`, `@handle`, `, <Org>`, `from <Org>`) + `DOMAIN_HINTS` lookup over `_blog` and `_email` hosts. Email-domain match works on the hashed-local-part shape `_anonymize_email` produces. Gated by `V2_RESOLVE_BIO_TO_ROR` (default `true`).
- New pipeline stage `resolve_bio_to_ror_llm` (`src/v2/pipeline/stages/resolve_bio_to_ror_llm.py`) + new agent `src/v2/agents/llm/refiners/bio_resolver/`: LLM long-tail over persons still without a Membership. Requires `confidence >= 0.7` + verbatim quote in `reason`. Bounded concurrency via `V2_RESOLVE_BIO_TO_ROR_LLM_CONCURRENCY` (default 4). Gated by `V2_RESOLVE_BIO_TO_ROR_LLM` + LLM/hybrid runtime (default `true`).
- New module `src/v2/parsers/publiccode.py` (`parse_publiccode`): full v0.4 core schema parser, forward-compatible with country-extension blocks. Lenient — drops bad sub-trees instead of failing whole parse. Real-world fixture pinned at `tests/v2/fixtures/publiccode_foodsoft_1e3adaef.yml`.
- Repository agent emits four URL pointers (`_citation_cff_url`, `_authors_url`, `_contributing_url`, `_publiccode_url`) when the corresponding file lives at the repo root. Case-insensitive filename match.
- Repository agent emits `_publiccode` (parsed payload) when `publiccode.yml` (or `.yaml`) exists at root.
- `refine_with_llm._build_repo_context_summary` forwards 4 KB excerpts of CITATION.cff / AUTHORS / CONTRIBUTING / publiccode.yml to LLM refiners, plus the parsed publiccode payload as a typed dict.
- `GitHubProvider._AUX_FILE_PATTERNS` extended with `publiccode.yaml` (alt extension) and `contribution.md` (alt spelling).
- Six new internal fields on Organization (from existing `/orgs/{org}` REST response): `_is_verified`, `_archived_at`, `_public_gists`, `_following_count`, `_has_organization_projects`, `_has_repository_projects`.
- New `publiccode:` JSON-LD prefix registered in `build_jsonld_output` when `include_internal_fields=true`. Resolves to `https://yml.publiccode.tools/`.
- Four new env vars in `.env.example`: `V2_RESOLVE_COMPANY_TO_ROR`, `V2_RESOLVE_BIO_TO_ROR`, `V2_RESOLVE_BIO_TO_ROR_LLM`, `V2_RESOLVE_BIO_TO_ROR_LLM_CONCURRENCY`.
- New docs: `docs/v2-pipeline.md` (pipeline overview + assumptions + node-graph). `.internal/v2-pipeline-reference.md` extended with stages 8b/c/d and "Internal Namespaces" section (private; tracked locally only).
- Cross-stage helpers exposed: `_build_org_stub`, `_build_membership`, `_existing_org_ids`, `_existing_membership_keys`, `_materialise`, `_person_canonical_id` (resolve_company_to_ror); `_persons_with_memberships` (resolve_bio_to_ror).
- Regression coverage for new work: `tests/v2/test_resolve_company_to_ror.py` (30), `tests/v2/test_resolve_bio_to_ror.py` (53), `tests/v2/test_resolve_bio_to_ror_llm.py` (24), `tests/v2/test_parsers_publiccode.py` (17), plus repo-agent / refine_with_llm extensions and a CTAS-swap rollback test in `tests/index/zenodo/test_iri.py`.

### Fixed (deploy-blockers caught in 2.1.0rc1)

- **Affiliation triples silently dropped from SPARQL output.** Resolver stages wrote `person["http://schema.org/affiliation"] = ror_url` using the full-IRI key. rdflib's JSON-LD parser dropped the key because the `@context` had no matching term mapping and no `@vocab` fallback — `0` triples landed in the SPARQL store despite stage logs reporting `persons_resolved > 0`. Fixed by switching to Membership materialisation (no `schema:affiliation` on Person). (PR #85)
- **DuckDB CTAS-swap not atomic.** HuggingFace + Zenodo IRI migrations ran `CREATE shadow → INSERT → DROP original → RENAME shadow` outside a transaction; a mid-migration crash left the original table gone and the replacement under its `__iri_migrate` shadow name. Wrapped in `BEGIN TRANSACTION / COMMIT` with `ROLLBACK` on exception. (PR #84)

### Changed (in 2.1.0rc1)

- **Affiliation contract**: resolver stages no longer write `person["schema:affiliation"] = ror_url`. Instead they materialise `org:Membership` + `org:Organization` entities into `reconciled.entities`. SPARQL queries that expected `<person> schema:affiliation <ror>` must switch to the chain `<person> org:hasMembership <m> ; <m> org:organization <ror>`. (PR #85)
- README rewritten for v2-first (287 → 199 lines). Production-use callout with `imagingplaza.epfl.ch` + `openpulse.science`. Worked extract example. Docs index table replaces inline docs.
- `.env.example` adds documentation for the four resolver env vars.
- `pyproject.toml`: version bumped to `2.1.0rc1` (was `3.0.0`, speculatively bumped before this release was scoped).
- `src/api.py`: FastAPI app `version` bumped to `2.1.0rc1` (was `2.0.1`).

---

## Accumulated entries from [Unpublished] (now part of 2.1.0rc1)

### Changed
- `POST /v2/extract` is now an async job submitter rather than a synchronous proxy to the GET handler. The handler validates the URL synchronously (still 422 for unsupported URLs), persists a `pending` job in the `ProviderCache`-backed job store, schedules the pipeline as a background task, and returns `202 Accepted` with `{job_id, status, status_url, submitted_at}`. Returns `503` if the provider cache is disabled. `GET /v2/extract/{full_path}` remains synchronous and unchanged.
- In `agent_runtime=llm`, orchestrator now performs an initial fail-open context-compilation pass immediately after `context_gather`: downstream LLM agents receive a compiled markdown summary block in prompt appendix context, while raw README/GIMIE/repository-file blobs are stripped from per-agent runtime context payloads.
- `/v2/extract` adds `include_context_summary=true|false` (default `false`). When enabled, responses include `context_summary_markdown` containing the compiled context brief provided to downstream agents (if available for the run/runtime).
- `/v2/extract` link validation is now always-on (`link_veracity` stage) for all runtimes (no request flag). The stage now validates all discovered HTTP(S) links from assembled entities and records per-link diagnostics/intermediates.
- Article link validation now enforces DOI-link checking by normalizing bare `schema:identifier` DOI strings to `https://doi.org/<doi>` before verification. Canonical article identity hierarchy remains `DOI > Infoscience > UUID`.
- Link-pruning policy now removes only explicitly unreachable links (`fetched_successfully=false`) and prunes entities when canonical URL/DOI checks fail or no valid URL remains after pruning.
- Added two always-on LLM-only global stages in `/v2/extract`: `llm_dedup` (after permissive validation, before reconciliation) and `llm_critic` (after reconciliation, before strict validation). Both stages are fail-open (warning-only) and append to `stats.stages_completed` in LLM runs.
- `llm_dedup` now applies deterministic constrained merge acceptance on LLM cluster suggestions across organizations/persons/repositories/articles, enforces identifier-conflict rejections, resolves canonical IDs by hierarchy, remaps references, and recomputes membership/contribution composite IDs.
- `llm_critic` now applies deterministic non-root pruning from LLM suggestions with root protection, cascade cleanup for memberships/contributions, relation-array cleanup, and `critic_pruned` entries in `excluded_entities`.
- `llm_critic` now receives explicit `owner_provenance`, can call `search_on_the_internet` (DuckDuckGo) and GitHub organization metadata tooling for relevance checks, and deterministically protects (a) repository owner-organization ancestry (`org:unitOf` chain) and (b) contributor-affiliation organizations tied to kept repository contributors from critic pruning.
- Strengthened v2 organization identity reconciliation to be ROR-first end-to-end. `resolve_organization_id()` now enforces hierarchy precedence even when incoming `id/idSource` is prefilled with a lower-priority source, and reconciliation now merges high-confidence duplicate organization variants (ROR/Infoscience/GitHub signals) before relationship remapping.
- Improved cross-source organization merge equivalence to normalize common name spelling variants (for example `center`/`centre`) so ROR + Infoscience duplicates merge more reliably and membership/org remaps propagate to canonical ROR IDs.
- Changed v2 organization lookup collision behavior during reconciliation to prefer ROR-backed canonical organizations when multiple candidates share tokens, while keeping ambiguity warning-only (no extract hard-fail).
- Changed `PipelineOrchestrator` default prompt-context behavior so downstream LLM agents receive serialized upstream stage JSON by default (`include_upstream_stage_outputs_in_prompt=True`).
- Updated LLM organization and membership system prompts with explicit acronym-disambiguation guidance, context-grounded ROR selection rules, and “leave ROR null when ambiguous” instructions.
- Updated membership fanout context to provide explicit `target_person` and `target_organizations` blocks, and wired `LLMMembershipAgentV2` to expose ORCID lookup tooling (`get_orcid_record`) for role/date grounding when ORCID identifiers are available.
- Tightened organization ownership semantics: reconciliation now rebuilds `pulse:owns` from canonical repository `pulse:ownedBy` links and clears ownership for organizations without a GitHub organization handle, preventing non-owner affiliation organizations from owning source repositories.
- Disabled ownership propagation from GitHub org-account units to canonical parent organizations; `pulse:owns` now remains direct-owner-only while preserving GitHub org-account nodes as `org:Organization` entities for hierarchy linkage.
- Reconciliation now strips organization lookup-only fields (`aliases`, `acronyms`, `labels`) before strict validation/output so `org:Organization` payloads stay strict-schema compliant (`additionalProperties: false`).
- GitHub org-account unit entities synthesized during reconciliation now use canonical GitHub URL IDs (`https://github.com/<handle>`) instead of bare-handle IDs, keeping repository ownership links URL-consistent end-to-end.
- Added a dual-provider organization LLM tool (`search_organization_identity`) that queries ROR and Infoscience together and returns linked candidate pairs for coherent identifier assignment.
- Replaced opaque `urn:git-metadata-extractor:entity:` URI prefix with shorter `urn:pulse:` for fallback entity `@id` values. Canonical entities (persons, repositories, organizations, articles) now use dereferenceable URLs (`https://orcid.org/`, `https://github.com/`, `https://ror.org/`, `https://doi.org/`) as `@id` wherever the `resolve_*_id()` functions in `id_resolution.py` produce them. Only UUID-fallback or pre-reconciliation entities receive the `urn:pulse:` prefix.
- Membership and contribution composite IDs now use full canonical URIs for both parts (e.g., `https://github.com/user_https://ror.org/02s376052`). The pipeline no longer relies on splitting composite IDs by `_` to extract person/org references — `_person_ref` and `schema:author`/`org:organization` fields are used instead.
- Crossref validation no longer parses composite membership IDs via string splitting. Ownership checks use an explicit `_person_ref` → person lookup map built from membership entities.
- Strict schema validation now strips `_`-prefixed internal fields before checking `additionalProperties`, preventing false rejections from pipeline-internal metadata.

### Fixed
- Fixed a regression where link-checker/runtime errors could be misclassified as invalid URLs and incorrectly prune root entities. Link-veracity checker errors are now fail-open warnings and do not trigger automatic pruning.
- Fixed `LLMPersonAgentV2` null optional-field leak: `model_dump(by_alias=True, mode="json")` in pydantic-ai returns all fields including `None`-valued optionals. The strict SHACL schema requires absent fields rather than explicit nulls, so top-level `None` values are now stripped from the payload dict before validation (`{k: v for k, v in payload.items() if v is not None}`). The nested `identifiers` sub-object is preserved intact.
- Fixed `LLMPersonAgentV2` null-list iteration bug: when the LLM returned `"pulse:hasContribution": null` or `"org:hasMembership": null`, `payload.get(key, [])` returned `None` (key exists with null value, default not used), causing `TypeError: 'NoneType' object is not iterable`. Changed to `(payload.get(key) or [])` to handle both absent key and null value.
- Fixed `V2LLMRuntime` token count extraction: `result.usage` in pydantic-ai 1.5.0 is a method, not a property. Added `if callable(usage): usage = usage()` guard before field access so `tokens_prompt`/`tokens_completion` are populated from the real `RunUsage` object instead of always returning `None`. V1 agents had the same bug but silently fell back to tiktoken estimates; v2 now uses the actual API-reported counts.
- Fixed v2 provider rate-limiter cross-loop lock reuse: shared `asyncio.Lock` instances could be reused across different event loops and fail with `"... is bound to a different event loop"`. Locks are now keyed per `(provider, event-loop-id)` to support mixed loop execution safely (script and orchestrator paths).
- Fixed person-stage unbounded waits in LLM fanout by adding a hard timeout around `LLMPersonAgentV2` runtime calls (`llm_call_timeout_seconds`, default `180s`) with explicit timeout errors per contributor.
- Fixed GIMIE-only repository runs when LLM is disabled: if GIMIE returns JSON-LD on `self.gimie`, the run is now marked successful instead of failing with `no data generated`.
- Fixed end-of-run logging noise for GIMIE-only runs (`run_llm=False`): suppress the LLM token-usage summary banner.
- Fixed `/v1/repository/gimie/json-ld` GitHub rate-limit surfacing: map GIMIE `ConnectionError` messages (secondary/primary rate limits) to clearer HTTP `429` / `503` responses instead of generic failures.

### Added
- Added `make_query_dependencies_tool` factory (`src/v2/agents/llm/agent_tools/query_dependencies.py`): creates a pydantic-ai `Tool` named `query_dependencies` that returns the parsed SPDX dependency list for a repository by calling GitHub's dependency-graph REST endpoint (`GET /repos/{owner}/{repo}/dependency-graph/sbom`). Each entry is shaped as `{name, ecosystem, version, spdxId}` with `ecosystem` parsed from the package's `purl` external reference (pypi, npm, cargo, maven, githubactions, ...). Supports optional `ecosystem` exact-match and `name_contains` (case-insensitive substring) filters, plus a `limit` cap (default 50, max 500). Returns `[]` when no SBOM is available (dependency graph disabled, 404, or 403 for private repos without `repo` scope) so the LLM treats absence as "no data" rather than as an error. Wired into `LLMRepositoryAgentV2` as an optional tool — the system prompt instructs the model to skip the call unless dependency context would inform `pulse:repositoryType`, `pulse:discipline`, or `schema:programmingLanguage`.
- Extended the `GitHubProvider` interface with `get_repository_sbom(full_name) -> list[dict] | None` (`src/v2/ingest/providers/base.py`), with a default `return None` impl so optional fakes need not stub it. Implemented in `RealGitHubProvider` (`github_provider.py`) by calling the SPDX SBOM endpoint with the existing `GITHUB_TOKEN` auth and `ProviderCache` integration; 404/403 collapse to `None`. `MockGitHubProvider` (`mock_github.py`) returns a small fixture for `octocat/Hello-World`.
- Added regression coverage in `tests/v2/test_github_sbom.py` (purl parsing across pypi/npm/maven/cargo/githubactions, version-fallback to `versionInfo`, unwrapped SPDX shapes, HTTP 200/404/403/500/non-JSON/network-failure paths) and `tests/v2/test_llm_query_dependencies_tool.py` (filter combinations, limit clamping, copy-not-reference output, empty-SBOM behavior).
- Added `make_query_orcid_tool` factory (`src/v2/agents/llm/agent_tools/query_orcid.py`): creates a pydantic-ai `Tool` named `query_orcid` that searches ORCID's expanded-search endpoint (`/v3.0/expanded-search/`) by free-text name. The tool wraps the standard edismax query with boosts on `given-and-family-names`, `family-name`, `given-names`, `credit-name`, `other-names`, and `text`, plus boost queries on current/past institution affiliations, and returns up to `rows` candidate hits (`orcid_id`, given/family/credit/other names, `institution_names`, `emails`). Wired into both `LLMPersonAgentV2` and `LLMMembershipAgentV2` so the LLM can discover an ORCID before calling `get_orcid_record` for the full profile.
- Extended the `ORCIDProvider` interface with `search_persons(query, *, rows=50, start=0) -> list[ORCIDSearchHit]` (`src/v2/ingest/providers/base.py`) and implemented it in both `RealORCIDProvider` (`orcid_provider.py`) and `MockORCIDProvider` (`mock_orcid.py`). Real-provider results are cached through `ProviderCache` keyed on `(query, rows, start)`; the mock matches case-insensitive substrings against the existing fixtures so unit tests don't hit the network.
- Updated `LLMPersonAgentV2` and `LLMMembershipAgentV2` system prompts to document `query_orcid`, including when to call it (no ORCID available, no Infoscience match), how to disambiguate hits via `institution_names`, and the rule that `pulse:orcid` and membership dates may only be filled after `get_orcid_record` confirms the candidate.
- Added regression coverage in `tests/v2/test_real_orcid_search.py` for the `RealORCIDProvider.search_persons` path: edismax `q` construction, pagination params, malformed-payload tolerance, query trimming + `rows`/`start` clamping, empty-query short-circuit, missing-`expanded-result` handling, and `ProviderCache` hit/miss behavior.
- Added prototype body-based extract endpoint `POST /v2/extract` (keeps existing `GET /v2/extract/{full_path}` intact). Request body mirrors extract options (`source_url`, `output_format`, `agent_runtime`, `include_intermediates`, `include_context_summary`) and returns the same `V2ExtractResponse` contract.
- Added `GET /v2/jobs/{job_id}` companion retrieval endpoint for jobs submitted via `POST /v2/extract`. Returns the persisted `V2ExtractJob` record (`pending|running|completed|failed`) including `request`, timestamps, and either `result` (full `V2ExtractResponse`) or `error` (`V2ErrorResponse`). Returns `404` for unknown / TTL-expired ids, `503` if the provider cache is disabled. Records persist in `ProviderCache` under the `v2-extract-job` namespace, governed by `V2_PROVIDER_CACHE_PATH` / `V2_PROVIDER_CACHE_TTL_DAYS`.
- Added `JobStore` (`src/v2/jobs.py`), a thin wrapper around `ProviderCache` for `V2ExtractJob` reads/writes, plus models `V2ExtractJob`, `V2ExtractJobAccepted`, and `V2ExtractJobStatus` in `src/v2/api_models/contracts.py`.
- Added `LLMContextSummaryAgentV2` (`src/v2/agents/llm/context_summary/agent.py`) as a beginning-of-pipeline LLM context compiler that ingests raw gathered repository material and emits `summary_markdown` for downstream agent grounding.
- Added `make_repository_corpus_grep_tool` (`src/v2/agents/llm/agent_tools/repository_corpus_grep.py`), a provenance-aware corpus grep tool returning markdown snippets with source metadata and line-numbered context blocks.
- Added `make_duckduckgo_search_tool` (`src/v2/agents/llm/agent_tools/duckduckgo_search.py`), which exposes `search_on_the_internet` for compact DuckDuckGo-backed external context retrieval (title/url/snippet rows) and wired it into `LLMContextSummaryAgentV2`.
- Added `hash_user_email_tool` (`src/v2/agents/llm/agent_tools/email_hash.py`) and wired it into `LLMPersonAgentV2` so person extraction can call a canonical email-anonymization tool instead of reimplementing hashing in-prompt.
- Added `LLMDedupAgentV2` (`src/v2/agents/llm/dedup/agent.py`) and `LLMCriticAgentV2` (`src/v2/agents/llm/critic/agent.py`) with structured JSON outputs for global duplicate-cluster and prune suggestions.
- Added stage helpers `run_llm_dedup_stage` and `run_llm_critic_stage` (`src/v2/pipeline/stages/llm_dedup.py`, `src/v2/pipeline/stages/llm_critic.py`) plus stage result dataclasses in `src/v2/pipeline/stages/models.py`.
- Added regression coverage:
  - `tests/v2/test_llm_dedup_stage.py`
  - `tests/v2/test_llm_critic_stage.py`
  - `tests/v2/test_extract_e2e.py` (LLM stage sequencing, intermediates, and fail-open behavior)
- Added `reconciliation_debug` intermediate emission in `/v2/extract` (when `include_intermediates=true`) with merge/remap diagnostics: merged group/entity counts, org remap sample, and organization lookup token-collision sample.
- Added full 7-stage LLM repository debug flow in `scripts/v2/run_llm_repo_persons_and_orgs.py`:
  `context_gather -> repo_agent -> person_agents -> org_agents -> article_agents -> membership_agents -> contribution_agents`,
  with deterministic class-stage seed fanout, partial-failure tolerance, stage summaries, and final combined JSON-LD aggregation including class entities from both primary `result.data` and stage list stats.
- Added independent `LLMLinkVeracityAgentV2` (`src/v2/agents/llm/link_veracity/agent.py`) to verify extracted link relationships with boolean verdicts using Selenium-backed content retrieval.
- Added shared LLM agent tools:
  - `generate_uuid_v4_tool` / `generate_uuid_v4_batch_tool` (`src/v2/agents/llm/agent_tools/uuid.py`)
  - `fetch_link_content_via_selenium_tool` (`src/v2/agents/llm/agent_tools/selenium_fetch.py`)
  and wired Selenium tool availability into repository/person/organization/article/membership/contribution LLM agents.
- Added `LLMPersonAgentV2` (`src/v2/agents/llm/person/agent.py`) as the LLM-backed person extraction agent. Uses the same runtime infrastructure as `LLMRepositoryAgentV2` but accepts any combination of identifiers (GitHub username, ORCID, Infoscience ID, or name) and routes tool calls through provider-aware factories built at `run()` time.
- Added `make_orcid_person_tool` factory (`src/v2/agents/llm/agent_tools/orcid_person.py`): creates a pydantic-ai `Tool` named `get_orcid_record` that fetches a full ORCID profile (name, employment, education, affiliations) by ORCID identifier. Tool closure captures the `ORCIDProvider` instance at construction time.
- Added `make_infoscience_search_tool` factory (`src/v2/agents/llm/agent_tools/infoscience_search.py`): creates a pydantic-ai `Tool` named `search_infoscience_person` that queries Infoscience for person records by name or query string. Returns ranked results with `infosciencePersonIdentifier`, `name`, `orcid`, `affiliations`, `profileUrl`, and `score`.
- Added GIMIE JSON-LD context enrichment to both LLM agents. `get_repository_jsonld()` (new non-abstract method on `GitHubProvider` base class) returns the cached raw GIMIE JSON-LD dict populated during `gather_context`. Both `LLMRepositoryAgentV2` (8 000-char limit) and `LLMPersonAgentV2` (4 000-char limit) serialize it as a JSON string and include it in the LLM input, enabling the model to extract DOIs, license IRIs, timestamps, and author credits from the richer RDF graph.
- Added `max_concurrent_agents: int = 3` parameter to `PipelineOrchestrator.__init__`. All fanout stages (`_execute_stage`) now create a fresh `asyncio.Semaphore(max_concurrent_agents)` per call and wrap each agent execution, preventing unbounded parallelism that was saturating the LLM endpoint when many person/org agents were launched simultaneously.
- Added prompt-context propagation controls in `PipelineOrchestrator`: `include_upstream_stage_outputs_in_prompt` (serialized `upstream_stage_outputs_json`) and `user_prompt_appendix` (verbatim text block) to feed downstream child-agent prompts without parsing.
- Added shared prompt helper `src/v2/agents/llm/prompt_context.py` and wired both `LLMRepositoryAgentV2` and `LLMPersonAgentV2` to append optional runtime sections to `user_prompt`.
- Added runtime usage observability fields (`requests`, `tool_calls`) to `LLMRuntimeResult` and runtime completion logs.
- Added local test-runtime tooling for faster loops: `pytest-xdist` and `pytest-testmon` in dev dependencies, plus a new `llm_integration` pytest marker and explicit `just test-llm-integration` command for opt-in real-provider LLM tests.
- Added `src/v2/agents/llm/agent_tools/` package as the shared tool registry for LLM agents. Each tool module exposes a named pydantic-ai `Tool` instance that any LLM agent can import and pass to `V2LLMRuntime.run_json_prompt`.
- Added `list_disciplines_tool` (`src/v2/agents/llm/agent_tools/disciplines.py`): a pydantic-ai tool named `list_disciplines` that returns the complete `DisciplineV2` mapping as a list of `{"wikidata_id", "name"}` objects. The tool description is generated at import time from the enum and contains the full Wikidata-ID-to-name table. Logs an INFO line on each call for observability.
- Extended `V2LLMRuntime.run_json_prompt` with a `tools: list[Any] | None` parameter forwarded directly to the pydantic-ai `Agent` constructor, enabling per-agent tool customization without subclassing the runtime.
- Wired `list_disciplines_tool` into `LLMRepositoryAgentV2`: the agent passes `tools=[list_disciplines_tool]` to the runtime call so the model can look up valid `pulse:discipline` Wikidata IRIs during generation.
- Updated `LLMRepositoryAgentV2` system prompt to add an "Available tools" section that names `list_disciplines`, describes its return shape, and instructs the model to call it before assigning any `pulse:discipline` values.
- Added Plan D runtime-migration implementation artifacts and task breakdown docs in `.internal/plan-d/PD-02` through `.internal/plan-d/PD-10`, including updated Plan D dependency/index metadata in `.internal/plan-d/README.md`.
- Added v2 runtime-selection primitives (`AgentRuntime`, parser, runtime protocol, runtime registry, rule-based/llm namespace scaffolding) to support staged runtime switching.
- Added v2 LLM runtime adapter (`src/v2/llm/runtime.py`) that reuses `src/llm/model_config.py`, enforces provider credential presence by env-var name, and normalizes structured JSON output + token metadata.
- Added `LLMRepositoryAgentV2` as the wave-1 LLM stage replacement for repository extraction, with permissive schema validation and contract-failure rejection behavior.
- Added runtime regression coverage for:
  - config/runtime parsing (`tests/v2/test_config.py`),
  - API runtime query validation (`tests/v2/test_api_extract_stub.py`),
  - orchestrator runtime routing/hard-fail semantics (`tests/v2/test_orchestrator_execution.py`),
  - LLM runtime adapter behavior (`tests/v2/test_llm_runtime_adapter.py`),
  - LLM repository agent contract behavior (`tests/v2/test_llm_repository_agent.py`),
  - runtime-default and no-fallback e2e behavior (`tests/v2/test_extract_e2e.py`).
- Added focused v2 regression coverage for Plan C issues 07/08/09 in `tests/v2/test_jsonld_build.py`, `tests/v2/test_context_versioning.py`, and `tests/v2/test_reconciliation.py`:
  - literal `schema:name` / `pulse:githubUsername` values that match entity IDs remain literals,
  - `schema:url` compacts/expands as an IRI-valued term,
  - organization Infoscience identifiers normalize from URL input to UUID tokens.

### Changed
- Changed `just v2-run-repo-full-llm` to run the full 7-stage script with `--verify-links`, so the command now appends end-of-run link-veracity checks by default.
- Changed local test workflows in `justfile` to a fast-default model: `just test` now runs `--testmon --no-cov -n auto --dist=loadfile`, while `just test-full` and `just test-coverage` provide deterministic full-suite and coverage-focused runs.
- Changed test invocation guidance and automation to prefer `.venv/bin/python -m pytest` (instead of relying on ad-hoc `PYTHONPATH`/global `pytest`) across `just` recipes, docs, and CI test steps.
- Changed v2 test isolation for safe parallel execution by resetting shared `src.api.app.state` fields between tests, using per-test DB paths (`V2_GRAPH_DB_PATH`/`CACHE_DB_PATH`), and hardening cache-singleton cleanup in v1 cache tests.
- Changed CI test throughput by parallelizing the default v2 suite (`-n 4 --dist=loadfile`) and separating `llm_integration` into a dedicated scheduled/manual job instead of default PR execution.
- Renamed repository identifier field `schema:identifier` to `schema:citation` in both agent and strict repository schemas (`src/v2/schemas/*/repository.schema.json`) to correctly represent the DOI/citation link. Updated `idSource` enum accordingly (`"schema:citation"` replaces `"schema:identifier"`). Regenerated Pydantic models (`IdSource3.schema_citation`). Updated all fixture copies and promoted dev schemas.
- Removed `schema:alternateName` from organization handling end-to-end: agent schema (`src/v2/schemas/agent/organization.schema.json`), generated models, organization agents (rule-based and LLM), prompts, and downstream consumers (article/membership/reconciliation). Alias matching now relies on `aliases`/`acronyms`/`labels` plus normalized name/identifier/handle tokens.
- Added `/v2/extract` runtime selector support with `agent_runtime=rule_based|llm`, defaulting from `V2_AGENT_RUNTIME_DEFAULT` when omitted.
- Updated pipeline orchestrator to resolve stage runners through runtime registry and enforce hard-fail policy for repository-stage LLM failures (`agent_runtime=llm`) without rule-based fallback.
- Updated `AGENTS.md` handoff to Plan D entry task `.internal/plan-d/PD-02-runtime-enum-and-config.md`.
- Updated the v2 standalone script `scripts/v2/run_llm_repo_and_persons.py` to use bounded person-agent concurrency with heartbeat progress output, per-contributor timeout handling, and partial-result completion summaries (`ok`, `timeout`, `error`) plus final successful `Person entities` JSON.
- Updated LLM fanout prompt behavior so downstream child agents can receive accumulated upstream stage JSON context (repository -> persons -> organizations -> ...) and optional raw multi-file text appendix blocks.
- Updated v2 API reference docs with runtime selector semantics and `V2_AGENT_RUNTIME_DEFAULT` environment variable.
- Removed v2 dependency on the v1 TTL-based cache system (`cache_manager`). `RealGitHubProvider` now calls base parsers (`GitHubUsersParser`, `GitHubOrganizationsParser`) directly instead of cached wrappers. The `force_refresh` query parameter and `V2_DISABLE_CACHE` environment variable have been removed from v2 endpoints and provider initialization.
- Removed reconciliation cleanup that explicitly stripped `schema:alternateName` because the field is no longer emitted anywhere in the v2 organization pipeline.
- Updated JSON-LD build normalization to be context-property-aware so string values are promoted to `{"@id": ...}` only for terms declared with `@type: @id`.
- Updated v2 JSON-LD context promotion with explicit `schema:url` IRI typing (`"schema:url": {"@type": "@id"}`) to satisfy SHACL IRI node-kind expectations.
- Updated reconciliation organization identifier normalization so `pulse:infoscienceOrganizationIdentifier` is persisted as UUID form (including URL-input extraction) in both top-level and `identifiers` payload fields.
- Updated `AGENTS.md` handoff to set the next entry task to `.internal/phase-8/P8-01-basic-live-connectivity.md` after completing Plan C issues 07, 08, and 09.
- Updated RDF graph coercion to be predicate-aware for string-constrained fields so URL-looking `schema:identifier` values are emitted as `xsd:string` literals instead of IRIs.
- Updated reconciliation organization normalization to prune unresolved `org:hasUnit` / `org:unitOf` links and emit explicit dropped-reference counters, preventing dangling organization hierarchy edges.
- Updated `AGENTS.md` handoff to set the next entry task to `.internal/plan-c/issue-07-jsonld-literal-to-id-conversion.md` after completing Plan C issues 5 and 6.
- Updated v2 organization alias propagation and reconciliation lookup matching for membership/source-organization resolution to use `aliases`/`acronyms`/`labels` (without `schema:alternateName`) while preserving accent/punctuation-insensitive token variants and GitHub handle matching with/without `@`.
- Updated v2 article-author resolution to use richer person-name alias tokens (including ORCID/Infoscience/GitHub display-name style inputs plus comma-order normalization) before strict unresolved-author skip decisions.
- Updated strict article skip warnings to include per-candidate matched/unmatched author counts for clearer operational diagnostics.
- Updated v2 person-fanout orchestration to skip GitHub contributor accounts whose resolved profile type is `Organization`, preventing organization handles (for example `sdsc-ordes`) from being emitted by `person_agent` as `schema:Person`.
- Updated reconciliation to model GitHub organization accounts as organization units when they act as repository owners under a canonical organization, adding `org:hasUnit` (canonical org) and `org:unitOf` (GitHub org-account node) links.
- Updated `AGENTS.md` handoff to set the next entry task to `.internal/plan-c/issue-05-schema-identifier-literal-vs-iri.md`.
- Coerced Infoscience person `profile_url` values to plain strings in `RealInfoscienceProvider.search_person(...)` so `schema:url` is no longer dropped due to `HttpUrl` object typing in permissive agent validation.
- Updated `PersonAgentV2` payload assembly to omit `schema:email` when anonymization returns `None`, removing noisy optional-field validation warnings without changing SHACL semantics.
- Updated `AGENTS.md` handoff to set the next entry task to `.internal/plan-c/issue-03-membership-organization-resolution.md` after completing Plan C issues 1 and 2.
- Enforced v2 production-safe fallback behavior in `/v2/extract` by introducing an explicit runtime flag (default `false`) and wiring it through article generation and reconciliation to prevent generated fallback entities/values in default production output.
- Standardized v2 agent-emitted `identifiers.uuid` generation on shared UUIDv4 helper `src/v2/agents/models.py::generate_uuid()` across person/repository/organization/article/membership/contribution agents.
- Updated reconciliation controls so unresolved article authors, fallback memberships, and fallback contributions are only generated when explicit fallback mode is enabled.
- Updated article-agent handling for strict mode to drop unresolved author references, reject placeholder-author/date coercion, and skip invalid candidates with explicit warnings.
- Normalized repository `schema:dateCreated` values in `RepositoryAgentV2` to strict UTC timestamp format (`YYYY-MM-DDTHH:MM:SSZ`) so live extracts do not fail root strict validation when providers return date-only strings.
- Updated `AGENTS.md` handoff to the next Phase 8 entry task `.internal/phase-8/P8-02-live-smoke-test-harness.md` after completing live connectivity stabilization in `P8-01`.
- Updated v2b phase-6 regression coverage to lock extract output contracts and stage ordering across repository/user/organization flows (`tests/v2/test_extract_e2e.py`, `tests/v2/test_extract_golden.py`).
- Updated extract golden fixtures to assert broader clean-break JSON envelope expectations (`id` coverage and full six-bucket `entities_by_type` shape for user/org payloads).
- Updated graph regression coverage to assert source-scoped intermediate filtering behavior when secondary-source intermediates exist (`tests/v2/test_api_graph.py`).
- Refreshed v2 API documentation with six-class runtime stage flow, JSON/JSON-LD output contract details, graph-write semantics, and corrected `/v2/graph` intermediate defaults.
- Updated `AGENTS.md` handoff to `.internal/phase-8/P8-01-basic-live-connectivity.md` after completing v2b phase-6 tasks.
- Implemented v2b phase-5 graph integration by wiring `/v2/extract` graph-write execution through `GraphStore.upsert_entity(...)` and persisting agent intermediates through new GraphStore intermediate APIs.
- Refactored intermediates assembly to read from `GraphStore.get_intermediates(...)` instead of direct stage-layer SQLite access, with optional run scoping for extract responses.
- Preserved source-scoped graph filtering correctness by persisting only final included entity IDs in `runs.stats.entity_ids` and excluding strict-invalid entities from graph writes.
- Updated `AGENTS.md` handoff to the next v2b entry task `.internal/v2b-plan/phase-6-regression-docs/P2B-27-e2e-golden-graph-regressions.md` after completing phase-5 tasks `P2B-24` through `P2B-26`.
- Implemented v2b phase-4 output contracts for `/v2/extract`: output assembly now emits a clean JSON envelope (`root_entity`, `related_entities`, `excluded_entities`, `entities_by_type`) and JSON-LD responses are built through a dedicated `jsonld_build` stage before SHACL validation.
- Updated v2 extract contract models to typed output unions (`V2JSONOutputEnvelope` and `V2JSONLDOutput`) with `output_format`/payload consistency checks.
- Promoted additional JSON-LD context term mappings in `src/v2/schemas/context/v2.0.jsonld` (relationship `@id` bindings and xsd datatype annotations) to keep phase-4 JSON-LD payloads compact/typed.
- Updated `AGENTS.md` handoff to the next v2b entry task `.internal/v2b-plan/phase-5-graph-integration/P2B-24-graphstore-intermediates-apis.md` after completing phase-4 tasks `P2B-20` through `P2B-23`.
- Integrated phase-3 v2b validation/reconciliation runtime gates into `/v2/extract`: reconciliation now runs before strict validation, strict root failures return typed 422, non-root strict failures are excluded with warnings, and SHACL validation executes as a non-fatal gate.
- Refactored reconciliation precedence to treat class-agent `articles`/`memberships`/`contributions` as primary outputs, synthesize fallback membership/contribution entities only for uncovered links, and emit explicit synthesis-traceability warnings.
- Aligned canonicalization `idSource` output with strict enums (`pulse:*` / `schema:identifier`) while preserving legacy alias compatibility for pre-existing payloads.
- Restored extract response compatibility for existing v2 contracts by preserving legacy stage-report filtering and legacy `output.entities` keying while keeping strict/SHACL gate execution active.
- Expanded v2 orchestration to a six-class stage graph (`repo/person/org/article/membership/contribution`) for repository/user/organization execution plans.
- Added derivation metadata emission in `AgentResult.stats["derivation"]` for repository, person, and organization agents while keeping entity payloads schema-clean.
- Updated `AGENTS.md` handoff to the next v2b entry task `.internal/v2b-plan/phase-3-validation-reconciliation/P2B-16-reconciliation-primary-class-outputs.md`.
- Added v2 class-agent exports for `ArticleAgentV2`, `MembershipAgentV2`, and `ContributionAgentV2` in `src/v2/agents/__init__.py`.
- Updated `AGENTS.md` v2 handoff to the next v2b orchestration entry task `.internal/v2b-plan/phase-2-orchestration/P2B-11-derivation-metadata-existing-agents.md` after phase-1 class-agent completion.
- Expanded the v2 Infoscience publication provider contract to include normalized article-linking fields (`authors`, `publicationDate`, `doi`, `url`, and optional `sourceOrganization`) with consistent `None`/empty-list fallback semantics.
- Updated `AGENTS.md` v2 handoff to the v2b continuation entry task `.internal/v2b-plan/phase-1-class-agents/P2B-05-article-agent-skeleton.md` after phase-0 foundations completion.
- Restricted v2 repository-mode GitHub expansion to direct entities only:
  - Disabled GitHub repo-list expansion for user/org lookups in repository extracts.
  - Scoped organization fanout so only direct repository owner org keeps GitHub lookup; membership-derived orgs use non-GitHub enrichment paths.
  - Kept ORCID/Infoscience/ROR enrichment active for person/org entities in repository mode.
  - Constrained repository-mode `pulse:owns` emission to source repository context where provided.
- Added v2 cache-bypass controls for testing runs:
  - `/v2/extract?force_refresh=true` now propagates to real-provider dependency wiring.
  - New env switch `V2_DISABLE_CACHE=true` disables v1-backed provider cache for all v2 runs.
- Updated `AGENTS.md` environment/testing guidance with explicit no-cache run instructions for v2 (`V2_DISABLE_CACHE` and `force_refresh`).
- Added CI migration gates for Phase 7 completion:
  - JSON-LD roundtrip regression gate (`tests/v2/test_roundtrip.py`)
  - v1 parity regression gate (`tests/test_v1_parity.py` plus legacy v1 suites in CI)
  - TTL/schema alignment and generated-model freshness gates remain enforced.
- Added v2 privacy parity stage with deterministic v1-compatible email anonymization (`sha256(local_part)[:12]@domain`) and reconciliation-stage application for person entities.
- Added provider-level rate-limit handling with per-provider tracking, proactive throttling near quota exhaustion, and exponential backoff + jitter retry on 429/`ProviderRateLimitError`.
- Changed `BaseProvider` initialization to remain backward-compatible for existing mock/dummy providers by defaulting `provider_name` when omitted.
- Changed v1 parity CI execution to force a writable cache path (`CACHE_DB_PATH=.tmp/cache.db`) for deterministic test runs.
- Added migration documentation for v1-to-v2 endpoint mapping, response-shape differences, environment variables, and deprecation timeline.
- Updated `AGENTS.md` handoff to advance the current entry task to `.internal/phase-8/P8-01-basic-live-connectivity.md`.
- Added Phase 7 CI gating workflow (`.github/workflows/ci.yml`) that runs generated-model freshness checks and the TTL-schema alignment test within scoped `tests/v2` execution.
- Added `just v2-models-generate` / `just v2-models-check` commands and wired deterministic schema-bundle generation so codegen freshness checks fail with schema-specific drift messages.
- Added `datamodel-code-generator` dev dependency and shared `[tool.datamodel-codegen]` defaults in `pyproject.toml`.
- Updated `AGENTS.md` handoff to advance the current v2 entry task to `.internal/v2-plan/phase-7-ci-migration/P7-04-ci-roundtrip.md`.
- Added v2 run-id correlation via `contextvars` so request traces, pipeline/agent spans, structured error events, and response headers share the same `run_id` per request.
- Changed `/v2/extract` run lifecycle handling to persist a run row up-front, propagate that run identifier through stats and response headers, and finalize run status with completion/failure metadata.
- Changed `/v2/extract` stats run-id format from generated `pipeline-*` strings to canonical UUID run identifiers.
- Added v2 structured error event emission (`record_error`) alongside standard Python logging for classified and pipeline execution failures.
- Added v2 observability metrics primitives for token usage, stage latency, validation failure counts, alias lookup hit/miss tracking, and graph upsert counters.
- Updated `AGENTS.md` handoff to advance the current v2 entry task to `.internal/v2-plan/phase-7-ci-migration/P7-01-codegen-setup.md`.
- Added v2 FastAPI request tracing middleware on `/v2/*` routes that emits `X-Run-Id` response headers and records request span attributes (`path`, `method`, `status_code`, `duration_ms`, `response_size`) without affecting non-v2 routes.
- Added v2 pipeline-stage span instrumentation for URL classification, context gather, agent-stage execution, permissive/strict validation, reconciliation, graph-write, and output assembly; strict/reconciliation/graph-write are currently emitted as explicit `status=skipped` spans where execution is not yet wired.
- Added v2 agent-run span instrumentation around retry-wrapped agent execution, including status (`success`/`retry`/`failure`/`error`), model/provider metadata, token usage, and retry count attributes.
- Extended `AgentResult`/pipeline serialization with optional model/provider/token fields to support observability payloads.
- Updated v2 observability probes to be true no-ops unless Logfire is both importable and initialized via bootstrap (`logfire.configure(...)`), avoiding unconfigured-runtime warnings.
- Updated `AGENTS.md` handoff to advance the current v2 entry task to `.internal/v2-plan/phase-6-observability/P6-05-token-latency-metrics.md`.
- Removed hardcoded US Logfire base-url fallback in preflight; base URL now resolves from `LOGFIRE_BASE_URL`, `.logfire` credentials, or token inference, otherwise fails explicitly.
- Changed Logfire preflight credential precedence to accept token from `.logfire/logfire_credentials.json` (from `logfire projects use`) and prefer it over `LOGFIRE_TOKEN` when both are present.
- Changed Logfire preflight behavior to validate connectivity/auth directly against `GET /v1/info` instead of SDK flush heuristics.
- Extended Phase 8 live preflight provider selection to include `logfire` by default.
- Added v2 Logfire bootstrap integration notes to `AGENTS.md` and advanced the current entry task to `.internal/v2-plan/phase-6-observability/P6-02-fastapi-instrumentation.md`.
- Switched v2 intermediates response assembly to a shared stage (`assemble_intermediates`) and reused it in both `/v2/extract` and `/v2/graph`.
- Switched v2 stats generation to a shared stage (`compute_stats`) and reused it in both `/v2/extract` and `/v2/graph`, including run-aware duration/stage metadata and graph-derived triple counts.
- Updated agent handoff in `AGENTS.md` to set the next entry task to `.internal/v2-plan/phase-6-observability/P6-01-logfire-bootstrap.md` after completing Phase 5.
- Replaced the `/v2/graph` stub with a graph-store-backed implementation that exports JSON-LD, applies `source_url`/`entity_type` filters, supports optional intermediate snapshots, and reports computed graph stats.
- Updated v2 graph serialization behavior by expanding namespace-prefix resolution in RDF sync and moving JSON-LD context loading to a versioned context file.
- Updated agent handoff in `AGENTS.md` to set the next entry task to `.internal/v2-plan/phase-5-export/P5-05-intermediates-envelope.md` after completing `P5-01` through `P5-04`.
- Added SQLite concurrent-write safeguards in `GraphStore` by enabling WAL mode, configuring `busy_timeout`/`synchronous`, and applying bounded retry handling for transient busy/locked write failures.
- Updated agent handoff in `AGENTS.md` so the next entry task advances to `.internal/v2-plan/phase-5-export/P5-01-jsonld-export.md` after completing Phase 4.
- Normalized RDF `rdf:type` generation for built-in v2 entity kinds so lowercase stored types (`person`, `repository`, etc.) emit ontology class URIs (`pulse:Person`, `pulse:Repository`, ...).
- Extended `GraphStore` with run tracking (`create_run`, `complete_run`, `fail_run`, `get_run`, `get_runs_by_source`) and entity upsert behavior powered by merge policy + field-level provenance.
- Integrated in-memory RDF synchronization into graph-store lifecycle:
  - bootstraps RDF graph from SQLite on startup
  - applies entity/edge deltas on insert/update/upsert/delete
  - exposes `get_rdf_graph()` for query/serialization surfaces.
- Updated v2 progress handoff in `AGENTS.md` to set the next entry task to `.internal/v2-plan/phase-4-graph-store/P4-09-intermediate-snapshots.md`.
- Clarified the v2 progress handoff in `AGENTS.md` by pointing the entry task to `.internal/v2-plan/phase-4-graph-store/P4-05-runs-table.md` after completing `P4-01` through `P4-04`.
- Added explicit guardrails for destructive graph rollback: `MigrationRunner.rollback_to()` now requires explicit opt-in with `allow_destructive_rollback=True` or `V2_GRAPH_ALLOW_DESTRUCTIVE_ROLLBACK=1`.

### Testing
- `.venv/bin/python -m pytest tests/v2/test_api_mount_v2_router.py -q`
- `.venv/bin/python -m pytest tests/test_cache.py -q`
- `.venv/bin/python -m pytest tests/v2/test_llm_repository_agent.py -q -m 'not llm_integration'`
- `.venv/bin/python -m pytest tests/v2/test_dependencies.py tests/v2/test_extract_golden.py tests/v2/test_api_extract_stub.py -q -m 'not llm_integration and not live_provider'`
- `.venv/bin/python -m pytest tests/v2 -q -n 4 --dist=loadfile -m 'not live_provider and not llm_integration'`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_config.py tests/v2/test_api_extract_stub.py tests/v2/test_orchestrator_execution.py tests/v2/test_llm_runtime_adapter.py tests/v2/test_llm_repository_agent.py tests/v2/test_agent_runtime_scaffolding.py tests/v2/test_extract_e2e.py -q`
- `PYTHONPATH=. .venv/bin/ruff check src/v2/agents/__init__.py src/v2/agents/contracts.py src/v2/agents/runtime.py src/v2/agents/registry.py src/v2/agents/llm/repository_agent.py src/v2/agents/rule_based/__init__.py src/v2/llm/runtime.py src/v2/llm/__init__.py src/v2/config.py src/v2/api.py src/v2/pipeline/orchestrator.py`
- `PYTHONPATH=. .venv/bin/ruff check tests/v2/test_llm_runtime_adapter.py tests/v2/test_llm_repository_agent.py tests/v2/test_agent_runtime_scaffolding.py tests/v2/test_api_extract_stub.py tests/v2/test_config.py`
- `PYTHONPATH=. .venv/bin/mypy src/v2/agents/runtime.py src/v2/agents/contracts.py src/v2/agents/registry.py src/v2/agents/llm/repository_agent.py src/v2/llm/runtime.py src/v2/config.py src/v2/api.py src/v2/pipeline/orchestrator.py`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_jsonld_build.py tests/v2/test_context_versioning.py tests/v2/test_reconciliation.py -q`
- `PYTHONPATH=. .venv/bin/ruff check src/v2/pipeline/stages/jsonld_build.py src/v2/pipeline/stages/reconciliation.py tests/v2/test_jsonld_build.py tests/v2/test_context_versioning.py tests/v2/test_reconciliation.py`
- `PYTHONPATH=. .venv/bin/mypy src/v2/pipeline/stages/jsonld_build.py src/v2/pipeline/stages/reconciliation.py`
- `python -m json.tool src/v2/schemas/context/v2.0.jsonld`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_rdf_sync.py tests/v2/test_reconciliation.py -q`
- `PYTHONPATH=. .venv/bin/ruff check src/v2/graph/rdf_sync.py src/v2/pipeline/stages/reconciliation.py tests/v2/test_rdf_sync.py tests/v2/test_reconciliation.py`
- `PYTHONPATH=. .venv/bin/mypy src/v2/graph/rdf_sync.py src/v2/pipeline/stages/reconciliation.py`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_article_agent.py tests/v2/test_reconciliation.py tests/v2/test_organization_agent.py tests/v2/test_membership_agent.py tests/v2/test_provider_interfaces.py -q`
- `PYTHONPATH=. .venv/bin/ruff check src/v2/providers/ror_provider.py src/v2/agents/organization_agent.py src/v2/pipeline/stages/reconciliation.py src/v2/agents/membership_agent.py src/v2/agents/article_agent.py tests/v2/test_reconciliation.py tests/v2/test_organization_agent.py tests/v2/test_article_agent.py tests/v2/test_provider_interfaces.py`
- `PYTHONPATH=. .venv/bin/mypy src/v2/providers/ror_provider.py src/v2/agents/organization_agent.py src/v2/pipeline/stages/reconciliation.py src/v2/agents/membership_agent.py src/v2/agents/article_agent.py`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_promoted_agent_schemas.py tests/v2/test_promoted_strict_schemas.py -q`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_orchestrator_execution.py tests/v2/test_reconciliation.py -q`
- `PYTHONPATH=. .venv/bin/ruff check src/v2/pipeline/orchestrator.py src/v2/pipeline/stages/reconciliation.py tests/v2/test_orchestrator_execution.py tests/v2/test_reconciliation.py`
- `PYTHONPATH=. .venv/bin/mypy src/v2/pipeline/orchestrator.py src/v2/pipeline/stages/reconciliation.py`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/` (1 known failure remains: `tests/v2/test_extract_golden.py::test_extract_endpoint_matches_golden_contract[...]` expected `entities_count=3`, actual `5`)
- `curl -sS -m 180 "http://localhost:1234/v2/extract/https%3A%2F%2Fwww.github.com%2Fsdsc-ordes%2Fgimie?output_format=json&force_refresh=true"` (timed out with `curl: (28)`; local `serve-dev` endpoint not responding during this run)
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_provider_interfaces.py tests/v2/test_person_agent.py -q`
- `PYTHONPATH=. .venv/bin/ruff check src/v2/providers/infoscience_provider.py src/v2/agents/person_agent.py tests/v2/test_provider_interfaces.py tests/v2/test_person_agent.py`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/` (1 known failure remains: `tests/v2/test_extract_golden.py::test_extract_endpoint_matches_golden_contract[...]` expected `entities_count=3`, actual `5`)
- `PYTHONPATH=. .venv/bin/pytest -m v2` (same single known golden failure; no non-v2 collection/import failures)
- `curl -sS -m 180 "http://localhost:1234/v2/extract/https%3A%2F%2Fwww.github.com%2Fsdsc-ordes%2Fgimie?output_format=json&force_refresh=true" > /tmp/issue01_issue02_extract.json && jq '{status: .status, source_url: .source_url, detected_type: .detected_type, warnings_count: (.warnings|length), first_warning: (.warnings[0] // null)}' /tmp/issue01_issue02_extract.json`
- `jq '{schema_url_httpurl_warnings: ([.warnings[] | select(test("schema:url") and test("HttpUrl"))] | length), schema_email_none_warnings: ([.warnings[] | select(test("schema:email") and test("None is not of type '\\''string'\\''"))] | length), sample_schema_url_warning: ([.warnings[] | select(test("schema:url"))][0] // null), sample_schema_email_warning: ([.warnings[] | select(test("schema:email"))][0] // null)}' /tmp/issue01_issue02_extract.json`
- `curl -sS -m 180 "http://localhost:1234/v2/extract/https%3A%2F%2Fwww.github.com%2Fsdsc-ordes%2Fgimie?output_format=json" | jq '{source_url, detected_type, output_format, error_type, entities_count: .stats.entities_count, stages_completed: .stats.stages_completed, output_keys: (.output|keys)}'`
- `curl -sS -m 180 "http://localhost:1234/v2/extract/https%3A%2F%2Fwww.github.com%2Fsdsc-ordes%2Fgimie?output_format=json" | jq '{root_id: .output.root_entity.id, date_created: .output.root_entity[\"schema:dateCreated\"]}'`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_repository_agent.py tests/v2/test_extract_e2e.py -q`
- `PYTHONPATH=. .venv/bin/ruff check src/v2/agents/repository_agent.py tests/v2/test_repository_agent.py`
- `PYTHONPATH=. .venv/bin/mypy src/v2/agents/repository_agent.py`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/`
- `PYTHONPATH=. .venv/bin/pytest -m v2`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_extract_e2e.py tests/v2/test_extract_golden.py tests/v2/test_api_graph.py -q`
- `PYTHONPATH=. .venv/bin/ruff check tests/v2/test_extract_e2e.py tests/v2/test_extract_golden.py tests/v2/test_api_graph.py`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/`
- `PYTHONPATH=. .venv/bin/pytest -m v2`
- `curl -sS -m 30 "http://localhost:1234/v2/extract/https%3A%2F%2Fwww.github.com%2Fsdsc-ordes%2Fgimie?output_format=json"` (timed out with `curl: (28)`; no local server response during run)
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_intermediates_envelope.py tests/v2/test_api_graph.py tests/v2/test_extract_e2e.py -q`
- `PYTHONPATH=. .venv/bin/ruff check src/v2/api.py src/v2/graph/store.py src/v2/pipeline/stages/intermediates.py tests/v2/test_intermediates_envelope.py tests/v2/test_api_graph.py tests/v2/test_extract_e2e.py`
- `PYTHONPATH=. .venv/bin/mypy src/v2/api.py src/v2/graph/store.py src/v2/pipeline/stages/intermediates.py`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/`
- `PYTHONPATH=. .venv/bin/pytest -m v2`
- `curl -sS -m 30 "http://localhost:1234/v2/extract/https%3A%2F%2Fwww.github.com%2Fsdsc-ordes%2Fgimie?output_format=json"` (timed out with `curl: (28)`; no local server response during run)
- `PYTHONPATH=. .venv/bin/ruff check src/v2/api.py src/v2/models/contracts.py src/v2/pipeline/stages/output_assembly.py src/v2/pipeline/stages/jsonld_build.py src/v2/pipeline/stages/models.py src/v2/pipeline/stages/__init__.py tests/v2/test_extract_e2e.py tests/v2/test_response_contracts.py tests/v2/test_context_versioning.py tests/v2/test_pipeline_spans.py`
- `PYTHONPATH=. .venv/bin/mypy src/v2/api.py src/v2/models/contracts.py src/v2/pipeline/stages/output_assembly.py src/v2/pipeline/stages/jsonld_build.py src/v2/pipeline/stages/models.py src/v2/pipeline/stages/__init__.py`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/`
- `PYTHONPATH=. .venv/bin/pytest -m v2`
- `curl -sS -m 180 "http://localhost:1234/v2/extract/https%3A%2F%2Fwww.github.com%2Fsdsc-ordes%2Fgimie?output_format=json" | jq '{status: .status, error_type: .error_type, message: .error.message}'`
- `PYTHONPATH=. .venv/bin/ruff check src/v2/api.py src/v2/canonicalization/id_resolution.py src/v2/pipeline/stages/reconciliation.py src/v2/pipeline/stages/output_assembly.py src/v2/validation/shacl_validation.py tests/v2/test_reconciliation.py tests/v2/test_canonical_id_person.py tests/v2/test_canonical_id_organization.py tests/v2/test_canonical_id_repository.py tests/v2/test_canonical_id_article.py tests/v2/test_strict_validation_gate.py tests/v2/test_extract_e2e.py tests/v2/test_shacl_validation.py`
- `PYTHONPATH=. .venv/bin/mypy src/v2/api.py src/v2/canonicalization/id_resolution.py src/v2/pipeline/stages/reconciliation.py src/v2/pipeline/stages/output_assembly.py src/v2/validation/shacl_validation.py`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/`
- `PYTHONPATH=. .venv/bin/pytest -m v2`
- `PYTHONPATH=. .venv/bin/ruff check src/v2/agents/repository_agent.py src/v2/agents/person_agent.py src/v2/agents/organization_agent.py src/v2/pipeline/orchestrator.py tests/v2/test_repository_agent.py tests/v2/test_person_agent.py tests/v2/test_organization_agent.py tests/v2/test_orchestrator_graph.py tests/v2/test_orchestrator_execution.py tests/v2/test_pipeline_spans.py`
- `PYTHONPATH=. .venv/bin/mypy src/v2/agents/repository_agent.py src/v2/agents/person_agent.py src/v2/agents/organization_agent.py src/v2/pipeline/orchestrator.py`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_orchestrator_graph.py tests/v2/test_orchestrator_execution.py tests/v2/test_pipeline_spans.py tests/v2/test_repository_agent.py tests/v2/test_person_agent.py tests/v2/test_organization_agent.py -q`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/` (fails only `tests/v2/test_extract_golden.py` fixture drift on `entities_count`/`stages_completed` now that six-class stages are active)
- `PYTHONPATH=. .venv/bin/pytest -m v2` (same three `test_extract_golden.py` failures; no non-v2 collection/import failures)
- `curl -sS -m 180 "http://localhost:1234/v2/extract/https%3A%2F%2Fwww.github.com%2Fsdsc-ordes%2Fgimie?output_format=json&force_refresh=true" | jq '{source_url, detected_type, entities_count: .stats.entities_count, stages_completed: .stats.stages_completed, entity_keys: (.output.entities | keys)}'`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_article_agent.py tests/v2/test_membership_agent.py tests/v2/test_contribution_agent.py -q`
- `PYTHONPATH=. .venv/bin/ruff check src/v2/agents/__init__.py src/v2/agents/article_agent.py src/v2/agents/membership_agent.py src/v2/agents/contribution_agent.py tests/v2/test_article_agent.py tests/v2/test_membership_agent.py tests/v2/test_contribution_agent.py`
- `PYTHONPATH=. .venv/bin/mypy src/v2/agents/article_agent.py src/v2/agents/membership_agent.py src/v2/agents/contribution_agent.py`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/`
- `curl -sS -m 180 "http://localhost:1234/v2/extract/https%3A%2F%2Fwww.github.com%2Fsdsc-ordes%2Fgimie?output_format=json&force_refresh=true" | jq '{source_url, detected_type, entities_count: .stats.entities_count, stages_completed: .stats.stages_completed, warnings_count: (.warnings | length), sample_entity_keys: ((.output.entities | keys)[:8])}'`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_orchestrator_execution.py tests/v2/test_provider_interfaces.py tests/v2/test_mock_infoscience_provider.py -q`
- `PYTHONPATH=. .venv/bin/ruff check src/v2/agents/models.py src/v2/agents/__init__.py src/v2/pipeline/models.py src/v2/providers/base.py src/v2/providers/infoscience_provider.py src/v2/providers/mock_infoscience.py tests/v2/test_orchestrator_execution.py tests/v2/test_provider_interfaces.py tests/v2/test_mock_infoscience_provider.py`
- `PYTHONPATH=. .venv/bin/mypy src/v2/agents/models.py src/v2/pipeline/models.py src/v2/providers/base.py src/v2/providers/infoscience_provider.py src/v2/providers/mock_infoscience.py`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/`
- `curl -sS -m 180 "http://localhost:1234/v2/extract/https%3A%2F%2Fwww.github.com%2Fsdsc-ordes%2Fgimie?output_format=json&force_refresh=true" | jq '{source_url, detected_type, output_keys: (.output.entities | keys), entities_count: .stats.entities_count, stages_completed: .stats.stages_completed, warnings_count: (.warnings | length)}'`
- `PYTHONPATH=. .venv/bin/ruff check src/v2/dependencies.py src/v2/providers/github_provider.py src/v2/agents/person_agent.py src/v2/agents/organization_agent.py src/v2/pipeline/orchestrator.py src/cache/cached_parsers.py src/parsers/users_parser.py src/parsers/orgs_parser.py tests/v2/test_dependencies.py tests/v2/test_provider_interfaces.py tests/v2/test_person_agent.py tests/v2/test_organization_agent.py tests/v2/test_orchestrator_execution.py tests/v2/test_extract_e2e.py`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_dependencies.py tests/v2/test_provider_interfaces.py tests/v2/test_person_agent.py tests/v2/test_organization_agent.py tests/v2/test_orchestrator_execution.py tests/v2/test_extract_e2e.py -v`
- `PYTHONPATH=. .venv/bin/ruff check src/v2/dependencies.py src/v2/providers/github_provider.py tests/v2/test_dependencies.py`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_dependencies.py -v`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_api_extract_stub.py -v`
- `PYTHONPATH=. .venv/bin/ruff check src/v2/pipeline/stages/privacy.py src/v2/pipeline/stages/reconciliation.py src/v2/providers/base.py src/v2/providers/rate_limiter.py src/v2/providers/github_provider.py src/v2/providers/infoscience_provider.py src/v2/providers/orcid_provider.py src/v2/providers/ror_provider.py src/v2/providers/__init__.py tests/v2/test_roundtrip.py tests/v2/test_email_anonymization.py tests/v2/test_rate_limiter.py tests/test_v1_parity.py`
- `PYTHONPATH=. .venv/bin/mypy src/v2/pipeline/stages/privacy.py src/v2/pipeline/stages/reconciliation.py src/v2/providers/base.py src/v2/providers/rate_limiter.py src/v2/providers/github_provider.py src/v2/providers/infoscience_provider.py src/v2/providers/orcid_provider.py src/v2/providers/ror_provider.py tests/v2/test_roundtrip.py tests/v2/test_email_anonymization.py tests/v2/test_rate_limiter.py tests/test_v1_parity.py`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_roundtrip.py tests/v2/test_email_anonymization.py tests/v2/test_rate_limiter.py -q`
- `CACHE_DB_PATH=.tmp/cache.db PYTHONPATH=. .venv/bin/pytest tests/test_cache.py tests/test_orcid_validation_pipeline.py tests/test_v1_parity.py -q`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/`
- `PYTHONPATH=. .venv/bin/pytest -m v2`
- `PYTHONPATH=. .venv/bin/python scripts/v2/generate_v2_models.py`
- `just v2-models-generate`
- `just v2-models-check`
- `PYTHONPATH=. .venv/bin/ruff check scripts/v2/generate_v2_models.py tests/v2/test_generated_models.py tests/v2/test_ttl_schema_alignment.py`
- `PYTHONPATH=. .venv/bin/mypy scripts/v2/generate_v2_models.py tests/v2/test_generated_models.py tests/v2/test_ttl_schema_alignment.py`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_generated_models.py tests/v2/test_ttl_schema_alignment.py -q`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/`
- `PYTHONPATH=. .venv/bin/pytest -m v2`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_metrics.py tests/v2/test_error_events.py tests/v2/test_runid_correlation.py tests/v2/test_extract_golden.py -q`
- `PYTHONPATH=. .venv/bin/ruff check src/v2/observability/context.py src/v2/observability/log_filter.py src/v2/observability/metrics.py src/v2/observability/error_events.py src/v2/observability/middleware.py src/v2/observability/agent_instrumentation.py src/v2/observability/pipeline_spans.py src/v2/api.py src/v2/observability/__init__.py tests/v2/test_metrics.py tests/v2/test_error_events.py tests/v2/test_runid_correlation.py tests/v2/test_extract_golden.py`
- `PYTHONPATH=. .venv/bin/mypy src/v2/api.py src/v2/observability/context.py src/v2/observability/log_filter.py src/v2/observability/metrics.py src/v2/observability/error_events.py src/v2/observability/middleware.py src/v2/observability/agent_instrumentation.py src/v2/observability/pipeline_spans.py src/v2/observability/__init__.py`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_fastapi_instrumentation.py tests/v2/test_agent_instrumentation.py tests/v2/test_pipeline_spans.py -q`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_api_extract_stub.py tests/v2/test_api_graph.py tests/v2/test_api_health.py tests/v2/test_api_mount_v2_router.py tests/v2/test_extract_e2e.py tests/v2/test_extract_golden.py tests/v2/test_graph_golden.py tests/v2/test_orchestrator_execution.py -q`
- `PYTHONPATH=. .venv/bin/ruff check src/v2/agents/models.py src/v2/api.py src/v2/observability/__init__.py src/v2/observability/agent_instrumentation.py src/v2/observability/middleware.py src/v2/observability/pipeline_spans.py src/v2/pipeline/models.py src/v2/pipeline/orchestrator.py tests/v2/test_agent_instrumentation.py tests/v2/test_fastapi_instrumentation.py tests/v2/test_pipeline_spans.py`
- `PYTHONPATH=. .venv/bin/mypy src/v2/api.py src/v2/pipeline/orchestrator.py src/v2/observability/agent_instrumentation.py src/v2/observability/middleware.py src/v2/observability/pipeline_spans.py src/v2/agents/models.py src/v2/pipeline/models.py`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_provider_connectivity_preflight.py -q`
- `PYTHONPATH=. .venv/bin/ruff check scripts/v2/check_provider_connectivity.py tests/v2/test_provider_connectivity_preflight.py`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_logfire_bootstrap.py -q`
- `PYTHONPATH=. .venv/bin/ruff check src/v2/observability/__init__.py src/v2/observability/bootstrap.py tests/v2/test_logfire_bootstrap.py`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_intermediates_envelope.py tests/v2/test_stats_computation.py tests/v2/test_api_graph.py tests/v2/test_api_extract_stub.py tests/v2/test_extract_golden.py tests/v2/test_response_contracts.py -q`
- `PYTHONPATH=. .venv/bin/ruff check src/v2/api.py src/v2/models/__init__.py src/v2/models/contracts.py src/v2/pipeline/stages/__init__.py src/v2/pipeline/stages/intermediates.py src/v2/pipeline/stages/stats.py tests/v2/test_intermediates_envelope.py tests/v2/test_stats_computation.py`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/`
- `PYTHONPATH=. .venv/bin/pytest -m v2`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_jsonld_export.py tests/v2/test_context_versioning.py tests/v2/test_filtered_subgraph.py tests/v2/test_api_graph.py tests/v2/test_graph_golden.py -q`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_org_alias_canonicalization.py tests/v2/test_concurrent_writes.py -v`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/ --collect-only`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_schema_validation_strict.py -v`
- `PYTHONPATH=. .venv/bin/pytest tests/v2 -m v2 --collect-only`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_entity_crud.py tests/v2/test_edge_crud.py tests/v2/test_alias_crud.py tests/v2/test_runs_crud.py tests/v2/test_upsert_merge.py tests/v2/test_provenance.py tests/v2/test_rdf_sync.py tests/v2/test_canonical_id_organization.py tests/v2/test_org_alias_canonicalization.py tests/v2/test_concurrent_writes.py -v`
- `PYTHONPATH=. .venv/bin/ruff check src/v2/graph/concurrency.py src/v2/graph/store.py src/v2/graph/provenance.py src/v2/graph/__init__.py src/v2/canonicalization/string_utils.py src/v2/canonicalization/organization_alias_map.py src/v2/canonicalization/__init__.py tests/v2/test_org_alias_canonicalization.py tests/v2/test_concurrent_writes.py`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/ --collect-only`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_schema_validation_strict.py -v`
- `PYTHONPATH=. .venv/bin/pytest tests/v2 -m v2 --collect-only`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_runs_crud.py tests/v2/test_upsert_merge.py tests/v2/test_provenance.py tests/v2/test_rdf_sync.py -v`
- `PYTHONPATH=. .venv/bin/ruff check src/v2/graph/rdf_sync.py tests/v2/test_rdf_sync.py`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/ --collect-only`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_schema_validation_strict.py -v`
- `PYTHONPATH=. .venv/bin/pytest tests/v2 -m v2 --collect-only`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_entity_crud.py tests/v2/test_edge_crud.py tests/v2/test_alias_crud.py tests/v2/test_runs_crud.py tests/v2/test_upsert_merge.py tests/v2/test_provenance.py tests/v2/test_rdf_sync.py -v`
- `PYTHONPATH=. .venv/bin/ruff check src/v2/graph/__init__.py src/v2/graph/models.py src/v2/graph/store.py src/v2/graph/merge.py src/v2/graph/provenance.py src/v2/graph/rdf_sync.py tests/v2/test_runs_crud.py tests/v2/test_upsert_merge.py tests/v2/test_provenance.py tests/v2/test_rdf_sync.py`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/ --collect-only`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_schema_validation_strict.py -v`
- `PYTHONPATH=. .venv/bin/pytest tests/v2 -m v2 --collect-only`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_graph_schema.py tests/v2/test_entity_crud.py tests/v2/test_edge_crud.py tests/v2/test_alias_crud.py -v`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_graph_schema.py -v`
- `PYTHONPATH=. .venv/bin/ruff check src/v2/graph/migrations.py tests/v2/test_graph_schema.py`
- `PYTHONPATH=. .venv/bin/pytest tests/v2/test_canonical_id_repository.py tests/v2/test_reconciliation.py tests/v2/test_partial_failure.py tests/v2/test_enum_alignment.py -v`

### Added
- Added RDF sync regression coverage that locks `schema:identifier` URL values to typed string literals while preserving IRI coercion for predicates such as `schema:url`.
- Added reconciliation regression coverage for organization hierarchy pruning/preservation across unresolved and resolvable `org:hasUnit` / `org:unitOf` references.
- Added orchestrator regression coverage `test_execute_skips_github_organization_accounts_from_person_fanout` to lock the org-account exclusion behavior in person fanout.
- Added reconciliation regression coverage `test_reconcile_models_github_org_account_as_unit_for_repository_owner` to lock `org:hasUnit`/`org:unitOf` modeling for GitHub org accounts.
- Added regression test `test_real_infoscience_person_profile_url_is_coerced_to_string` in `tests/v2/test_provider_interfaces.py` to lock `HttpUrl` to `str` coercion in real Infoscience provider person results.
- Added regression test `test_person_agent_omits_schema_email_when_no_email_is_available` in `tests/v2/test_person_agent.py` to lock `None` email pre-filter behavior.
- Added v2 UUID helper regression coverage (`tests/v2/test_agent_uuid_generation.py`) asserting UUIDv4 generation semantics.
- Added reconciliation and extract e2e regression coverage for fallback-generation policy, including stress coverage for large unresolved Infoscience author lists to prevent fallback-entity explosions in production mode.
- Added repository-agent regression coverage for strict date normalization from date-only source values in `tests/v2/test_repository_agent.py`.
- Added phase-6 extract regression assertions for:
  - explicit clean-break JSON envelope keys and six-bucket output grouping.
  - detected-type-specific stage sequence locking in integration and golden tests.
  - JSON-LD node contract checks (`@id`/`@type`) in golden verification.
- Added phase-6 graph regression assertion that source-scoped `/v2/graph` intermediate responses exclude secondary-source intermediates.
- Added first-class GraphStore intermediate persistence/query APIs in `src/v2/graph/store.py` (`insert_intermediate`, `get_intermediates`) with deterministic ordering and optional source/run filtering.
- Added phase-5 regression coverage for GraphStore-backed intermediates and extract-driven source graph filtering:
  - `tests/v2/test_intermediates_envelope.py`
  - `tests/v2/test_api_graph.py`
  - `tests/v2/test_extract_e2e.py`
- Added a dedicated phase-4 JSON-LD build stage at `src/v2/pipeline/stages/jsonld_build.py` and updated stage exports/wiring.
- Added clean-break extract response contracts and validation tests for JSON/JSON-LD output shapes (`tests/v2/test_response_contracts.py`, `tests/v2/test_extract_e2e.py`).
- Added phase-4 regression coverage for context-term promotion and stage-span sequencing (`tests/v2/test_context_versioning.py`, `tests/v2/test_pipeline_spans.py`) plus refreshed v2 extract/graph golden fixtures.
- Added phase-2 orchestration coverage for class-stage fanout context propagation, deterministic root-type seeding, mixed-success retry behavior, and stage-span assertions (`tests/v2/test_orchestrator_execution.py`, `tests/v2/test_pipeline_spans.py`).
- Added new v2 class-agent implementations and tests:
  - `src/v2/agents/article_agent.py`
  - `src/v2/agents/membership_agent.py`
  - `src/v2/agents/contribution_agent.py`
  - `tests/v2/test_article_agent.py`
  - `tests/v2/test_membership_agent.py`
  - `tests/v2/test_contribution_agent.py`
- Added Infoscience publication fixture ranking signals (`score`) for class-agent coverage in `tests/v2/fixtures/providers/infoscience/publication_result.json`.
- Added six-class runtime typed entity bucket primitives in v2 pipeline/agent models and serialization (`repositories`, `persons`, `organizations`, `articles`, `memberships`, `contributions`) for downstream reconciliation/output stages.
- Added focused v2 dependency coverage for cache-bypass toggles in `tests/v2/test_dependencies.py`.
- Added Phase 7 CI and migration artifacts:
  - `tests/v2/test_roundtrip.py`
  - `tests/test_v1_parity.py`
  - `tests/v2/test_email_anonymization.py`
  - `tests/v2/test_rate_limiter.py`
  - `src/v2/pipeline/stages/privacy.py`
  - `src/v2/providers/rate_limiter.py`
  - `docs/migration-v1-to-v2.md`
  - `docs/v2-api-reference.md`
- Added committed generated v2 models module at `src/v2/generated/entities.py` (with datamodel-code-generator header and embedded strict-schema SHA metadata) plus package exports in `src/v2/generated/__init__.py`.
- Added `scripts/v2/generate_v2_models.py` to produce/check generated models from strict schemas and detect stale codegen output.
- Added generated-model smoke coverage in `tests/v2/test_generated_models.py` for `PersonModel` and `RepositoryModel` fixture validation.
- Added promoted TTL↔schema CI gate coverage in `tests/v2/test_ttl_schema_alignment.py` for property/enum/pattern/required/datatype consistency and drift detection.
- Added v2 observability primitives for phase-6 completion:
  - `src/v2/observability/context.py` (`RunContext`)
  - `src/v2/observability/log_filter.py` (`RunIdLogFilter`)
  - `src/v2/observability/metrics.py` (`V2Metrics`)
  - `src/v2/observability/error_events.py` (`record_error`)
- Added focused coverage for phase-6 completion:
  - `tests/v2/test_metrics.py`
  - `tests/v2/test_error_events.py`
  - `tests/v2/test_runid_correlation.py`
- Added v2 observability primitives and wiring for Phase 6 request/agent/pipeline tracing:
  - `src/v2/observability/middleware.py`
  - `src/v2/observability/agent_instrumentation.py`
  - `src/v2/observability/pipeline_spans.py`
- Added focused observability test coverage:
  - `tests/v2/test_fastapi_instrumentation.py`
  - `tests/v2/test_agent_instrumentation.py`
  - `tests/v2/test_pipeline_spans.py`
- Added a Logfire connectivity branch in `scripts/v2/check_provider_connectivity.py`:
  - validates Logfire credentials via `LOGFIRE_TOKEN` or `.logfire/logfire_credentials.json`
  - validates token + region with direct `GET /v1/info` checks against the resolved Logfire API base URL
- Added warning output when `LOGFIRE_TOKEN` and `.logfire` credentials both exist but differ.
- Added Logfire preflight coverage in `tests/v2/test_provider_connectivity_preflight.py`.
- Added v2 observability bootstrap primitives:
  - `src/v2/observability/bootstrap.py` (`initialize_logfire`)
  - `src/v2/observability/__init__.py` (bootstrap export)
- Added focused Logfire bootstrap coverage:
  - `tests/v2/test_logfire_bootstrap.py`
- Added intermediates envelope contracts and assembly stage:
  - `src/v2/models/contracts.py` (`IntermediateEnvelope`)
  - `src/v2/pipeline/stages/intermediates.py`
  - `tests/v2/test_intermediates_envelope.py`
- Added shared stats computation stage for v2 API responses:
  - `src/v2/pipeline/stages/stats.py`
  - `tests/v2/test_stats_computation.py`
- Added v2 graph export primitives and context assets:
  - `src/v2/graph/export.py`
  - `src/v2/schemas/context/v2.0.jsonld`
- Added run-scoped entity ID lookup for graph filtering in `src/v2/graph/store.py`.
- Added Phase 5 coverage for JSON-LD export, context versioning, filtered subgraphs, and the `/v2/graph` API:
  - `tests/v2/test_jsonld_export.py`
  - `tests/v2/test_context_versioning.py`
  - `tests/v2/test_filtered_subgraph.py`
  - `tests/v2/test_api_graph.py`
- Added organization alias canonicalization primitives:
  - `src/v2/canonicalization/string_utils.py`
  - `src/v2/canonicalization/organization_alias_map.py`
- Added SQLite write-concurrency utility and exports:
  - `src/v2/graph/concurrency.py`
  - `src/v2/graph/__init__.py`
- Added focused v2 coverage for organization alias resolution and concurrent-write behavior:
  - `tests/v2/test_org_alias_canonicalization.py`
  - `tests/v2/test_concurrent_writes.py`
- Added RDF sync coverage for ontology class normalization from built-in lowercase entity kinds in `tests/v2/test_rdf_sync.py`.
- **V2 Phase 4 graph-store execution metadata + merge/provenance/RDF sync (`P4-05` to `P4-08`)**:
  - Added merge-policy primitive and result contract:
    - `src/v2/graph/merge.py`
  - Added provenance tracking primitive:
    - `src/v2/graph/provenance.py`
  - Added RDF bootstrap/delta synchronization primitive:
    - `src/v2/graph/rdf_sync.py`
  - Extended graph-store data contracts:
    - `src/v2/graph/models.py` (`Run`, `ProvenanceEntry`)
  - Extended graph-store integration:
    - `src/v2/graph/store.py`
    - `src/v2/graph/__init__.py`
  - Added focused v2 coverage:
    - `tests/v2/test_runs_crud.py`
    - `tests/v2/test_upsert_merge.py`
    - `tests/v2/test_provenance.py`
    - `tests/v2/test_rdf_sync.py`
- **V2 Phase 4 graph-store foundation and CRUD (`P4-01` to `P4-04`)**:
  - Added SQLite schema constants and migration runner:
    - `src/v2/graph/schema.py`
    - `src/v2/graph/migrations.py`
    - `src/v2/graph/migrations/001_initial.sql`
  - Added typed graph-store models and package exports:
    - `src/v2/graph/models.py`
    - `src/v2/graph/__init__.py`
  - Added `GraphStore` CRUD operations for entities, edges, and aliases:
    - `src/v2/graph/store.py`
  - Added graph-store schema and CRUD coverage:
    - `tests/v2/test_graph_schema.py`
    - `tests/v2/test_entity_crud.py`
    - `tests/v2/test_edge_crud.py`
    - `tests/v2/test_alias_crud.py`
- Added a scoped Phase 3 completion checkpoint for repository canonicalization, reconciliation, partial-failure assembly, and enum-alignment validation.
- **V2 Phase 3 reconciliation and enum alignment (`P3-05` to `P3-08`)**:
  - Added repository canonical ID resolution with prioritized source selection:
    - `src/v2/canonicalization/id_resolution.py`
    - `tests/v2/test_canonical_id_repository.py`
  - Added cross-entity reconciliation for canonical link normalization plus membership/contribution generation:
    - `src/v2/pipeline/stages/reconciliation.py`
    - `src/v2/pipeline/stages/models.py`
    - `tests/v2/test_reconciliation.py`
  - Added partial-failure output assembly that excludes strict-invalid non-root entities and preserves root success semantics:
    - `src/v2/pipeline/stages/output_assembly.py`
    - `src/v2/pipeline/stages/models.py`
    - `tests/v2/test_partial_failure.py`
  - Added v2 enum alignment against the TTL ontology and extraction utility:
    - `src/v2/models/enums.py`
    - `scripts/v2/extract_enums_from_ttl.py`
    - `tests/v2/test_enum_alignment.py`
- **V2 validation gates and canonical ID resolution**:
  - Added strict JSON Schema gate module and batch result contracts:
    - `src/v2/validation/schema_validation.py`
    - `tests/v2/test_strict_validation_gate.py`
  - Added SHACL validation support with ontology loading cache:
    - `src/v2/validation/shacl_validation.py`
    - `src/v2/validation/ontology.py`
    - `tests/v2/test_shacl_validation.py`
  - Added canonical ID resolution for people and organizations:
    - `src/v2/canonicalization/id_resolution.py`
    - `src/v2/canonicalization/__init__.py`
    - `tests/v2/test_canonical_id_person.py`
    - `tests/v2/test_canonical_id_organization.py`
- **V2 Phase 0 strict schema promotion (`P0-01`)**:
  - Promoted 6 strict JSON Schemas from `dev/ontology-v2-json-response/a-001/json-schema/strict/` to `src/v2/schemas/strict/`:
    - `person.schema.json`
    - `repository.schema.json`
    - `organization.schema.json`
    - `membership.schema.json`
    - `contribution.schema.json`
    - `article.schema.json`
  - Added new v2 package markers:
    - `src/v2/__init__.py`
    - `src/v2/schemas/__init__.py`
- **V2 Phase 0 agent schema promotion (`P0-02`)**:
  - Promoted 6 agent JSON Schemas from `dev/ontology-v2-json-response/a-001/json-schema/agent/` to `src/v2/schemas/agent/`:
    - `person.schema.json`
    - `repository.schema.json`
    - `organization.schema.json`
    - `membership.schema.json`
    - `contribution.schema.json`
    - `article.schema.json`
- **V2 Phase 0 test infrastructure (`P0-03`)**:
  - Added `tests/v2/conftest.py` with shared session fixtures:
    - `v2_test_config`
    - `load_schema()`
    - `load_fixture()`
    - `load_golden()`
  - Added v2 fixture/golden scaffold directories:
    - `tests/v2/fixtures/schema/{strict,agent}/`
    - `tests/v2/fixtures/providers/{github,orcid,infoscience,ror}/`
    - `tests/v2/fixtures/scenarios/`
    - `tests/v2/golden/{extract,graph}/`
  - Added schema fixture copies in:
    - `tests/v2/fixtures/schema/strict/*.schema.json`
    - `tests/v2/fixtures/schema/agent/*.schema.json`
  - Added infrastructure smoke tests:
    - `tests/v2/test_test_infrastructure.py`
- **V2 Phase 0 strict schema valid fixtures/tests (`P0-04`)**:
  - Added valid strict fixture copies in `tests/v2/fixtures/schema/strict/`:
    - `pulse_PersonShape.json`
    - `pulse_RepositoryShape.json`
    - `pulse_OrganizationShape.json`
    - `pulse_MembershipShape.json`
    - `pulse_ContributionShape.json`
    - `pulse_ArticleShape.json`
  - Added strict schema validation tests:
    - `tests/v2/test_schema_validation_strict.py`
- **V2 Phase 0 agent schema valid fixtures/tests (`P0-05`)**:
  - Added agent schema validation tests:
    - `tests/v2/test_schema_validation_agent.py`
- **V2 Phase 0 strict schema negative fixtures/tests (`P0-06`)**:
  - Added strict-invalid schema fixtures in `tests/v2/fixtures/schema/invalid/`:
    - `person_missing_name.json`
    - `person_bad_orcid.json`
    - `person_no_identifier.json`
    - `repo_bad_github_handle.json`
    - `org_unknown_type.json`
    - `membership_extra_properties.json`
    - `contribution_negative_count.json`
    - `article_bad_doi.json`
  - Added strict negative schema validation tests:
    - `tests/v2/test_schema_validation_negative.py`
- **V2 Phase 0 mock GitHub provider fixtures/interface (`P0-07`)**:
  - Added provider package scaffolding:
    - `src/v2/providers/__init__.py`
    - `src/v2/providers/base.py`
    - `src/v2/providers/mock_github.py`
  - Added GitHub provider fixtures in `tests/v2/fixtures/providers/github/`:
    - `repo_payload.json`
    - `user_payload.json`
    - `org_payload.json`
    - `contributors_payload.json`
    - `rate_limited_response.json`
    - `not_found_response.json`
  - Added mock provider tests:
    - `tests/v2/test_mock_github_provider.py`
- **V2 Phase 0 mock Infoscience provider fixtures/interface (`P0-09`)**:
  - Added Infoscience provider interface + fixture-backed mock:
    - `src/v2/providers/base.py` (new `InfoscienceProvider`)
    - `src/v2/providers/mock_infoscience.py`
  - Added Infoscience provider fixtures in `tests/v2/fixtures/providers/infoscience/`:
    - `person_single_hit.json`
    - `person_multi_hit.json`
    - `orgunit_result.json`
    - `publication_result.json`
    - `empty_result.json`
  - Added mock provider tests:
    - `tests/v2/test_mock_infoscience_provider.py`
- **V2 Phase 0 mock ROR provider fixtures/interface (`P0-10`)**:
  - Added ROR provider interface + fixture-backed mock:
    - `src/v2/providers/base.py` (new `RORProvider`)
    - `src/v2/providers/mock_ror.py`
  - Added ROR provider fixtures in `tests/v2/fixtures/providers/ror/`:
    - `org_detail.json`
    - `search_results.json`
    - `org_with_aliases.json`
    - `parent_org.json`
    - `not_found.json`
  - Added mock provider tests:
    - `tests/v2/test_mock_ror_provider.py`
- **V2 Phase 0 seed-based mock data generator (`P0-11`)**:
  - Promoted deterministic mock data generation tooling:
    - `scripts/v2/generate_mock_data.py`
    - `src/v2/testing/__init__.py`
    - `src/v2/testing/mock_generator.py`
  - Added mock generator tests:
    - `tests/v2/test_mock_generator.py`
- **V2 Phase 0 mock ORCID provider fixtures/interface (`P0-08`)**:
  - Added ORCID provider interface and fixture-backed mock:
    - `src/v2/providers/base.py` (new `ORCIDRecord` + `ORCIDProvider`)
    - `src/v2/providers/mock_orcid.py`
    - `src/v2/providers/__init__.py` (ORCID exports)
  - Added ORCID provider fixtures in `tests/v2/fixtures/providers/orcid/`:
    - `valid_record.json`
    - `no_affiliations.json`
    - `multiple_employment.json`
    - `invalid_checksum.json`
  - Added mock provider tests:
    - `tests/v2/test_mock_orcid_provider.py`
- **V2 Phase 0 cross-reference consistency validation (`P0-12`)**:
  - Added cross-reference validation module:
    - `src/v2/validation/__init__.py`
    - `src/v2/validation/crossref.py`
  - Added cross-reference consistency tests:
    - `tests/v2/test_crossref_consistency.py`
- **V2 Phase 0 golden extract contract tests (red phase) (`P0-13`)**:
  - Added extract golden tests:
    - `tests/v2/test_extract_golden.py`
  - Added extract golden fixtures:
    - `tests/v2/golden/extract/repo_github_com_owner_repo.json`
    - `tests/v2/golden/extract/user_github_com_username.json`
    - `tests/v2/golden/extract/org_github_com_orgname.json`
- **V2 Phase 0 golden graph contract tests (red phase) (`P0-14`)**:
  - Added graph golden tests:
    - `tests/v2/test_graph_golden.py`
  - Added graph golden fixtures:
    - `tests/v2/golden/graph/full_graph.json`
    - `tests/v2/golden/graph/filtered_by_type.json`
    - `tests/v2/golden/graph/filtered_by_source.json`
- **V2 Phase 1 package skeleton (`P1-01`)**:
  - Added `src/v2/api.py` placeholder module.
  - Added package skeleton `__init__.py` files for:
    - `src/v2/agents/`
    - `src/v2/canonicalization/`
    - `src/v2/detection/`
    - `src/v2/generated/`
    - `src/v2/graph/`
    - `src/v2/models/`
    - `src/v2/observability/`
    - `src/v2/pipeline/`
    - `src/v2/pipeline/stages/`
- **V2 Phase 1 config module (`P1-02`)**:
  - Added `src/v2/config.py` with `V2Config` environment loading and defaults:
    - `V2_GRAPH_DB_PATH` (default: `data/v2_graph.db`)
    - `V2_INTERMEDIATE_HISTORY_LIMIT` (default: `5`)
    - `V2_ENABLE_LOGFIRE` (default: `true`)
    - `LOGFIRE_TOKEN` (optional)
    - `GITHUB_TOKEN` (required via `validate_preflight()`)
  - Added `tests/v2/test_config.py`.
- **V2 Phase 1 GitHub URL classifier (`P1-03`)**:
  - Added URL detection models in `src/v2/detection/models.py`:
    - `GitHubURLType`
    - `GitHubURLClassification`
    - `UnsupportedGitHubURL`
  - Added `classify_github_url()` implementation in `src/v2/detection/github_url_classifier.py`.
  - Added exports in `src/v2/detection/__init__.py`.
  - Added `tests/v2/test_url_classifier.py`.
- **V2 Phase 1 classifier edge-case handling (`P1-04`)**:
  - Extended classifier rejection logic for unsupported GitHub subresource URLs:
    - `issues`, `pull`, `blob`, `tree`, `commit`/`commits`, `actions`, `releases`, `wiki`, `settings`, `security`
  - Added support for:
    - HTTP to HTTPS normalization
    - URL-decoded owner/repo path segments
    - configurable GitHub Enterprise base URL (`V2_GITHUB_BASE_URL`)
  - Added `tests/v2/test_url_classifier_edge_cases.py`.
- **V2 Phase 1 response contracts (`P1-05`)**:
  - Added `src/v2/models/contracts.py` with:
    - `V2ExtractResponse`
    - `V2GraphResponse`
    - `V2Stats`
    - `V2GraphUpdate`
  - Added exports in `src/v2/models/__init__.py`.
  - Added `tests/v2/test_response_contracts.py`.
- **V2 Phase 1 error models (`P1-06`)**:
  - Added `src/v2/models/errors.py` with:
    - `V2ErrorType`
    - `V2FieldError`
    - `V2ErrorResponse`
  - Added exports in `src/v2/models/__init__.py`.
  - Added `tests/v2/test_error_models.py`.
- **V2 Phase 1 stub extract endpoint (`P1-07`)**:
  - Implemented `src/v2/api.py` router with `GET /v2/extract/{full_path:path}`.
  - Added query parameter handling for:
    - `output_format`
    - `force_refresh`
    - `include_intermediates`
  - Added typed `422` error payloads for unsupported URLs.
  - Added `tests/v2/test_api_extract_stub.py`.
- **V2 Phase 1 stub graph endpoint (`P1-08`)**:
  - Added `GET /v2/graph` to `src/v2/api.py`.
  - Added query parameter handling for:
    - `source_url`
    - repeated `entity_type`
    - `include_intermediates`
    - `intermediate_limit`
  - Stub response returns JSON-LD envelope with empty `@graph` and zeroed stats.
  - Added `tests/v2/test_api_graph_stub.py`.
- **V2 Phase 1 mount v2 router in main app (`P1-09`)**:
  - Mounted `v2_router` in `src/api.py` so `/v2/*` routes are served via the main API app.
  - Added main-app routing coverage:
    - `tests/v2/test_api_mount_v2_router.py`
- **V2 Phase 1 v2 health check endpoint (`P1-10`)**:
  - Added `V2HealthResponse` to `src/v2/models/contracts.py`.
  - Exported `V2HealthResponse` from `src/v2/models/__init__.py`.
  - Added `GET /v2/health` in `src/v2/api.py` with component-level statuses for:
    - `python`
    - `config`
    - `graph_store` (stubbed healthy)
    - `github_token` (healthy/degraded)
  - Added health endpoint coverage:
    - `tests/v2/test_api_health.py`
- **V2 Phase 2 provider interfaces (`P2-01`)**:
  - Added production provider implementations:
    - `src/v2/providers/github_provider.py`
    - `src/v2/providers/orcid_provider.py`
    - `src/v2/providers/infoscience_provider.py`
    - `src/v2/providers/ror_provider.py`
  - Extended provider exports and factory wiring in:
    - `src/v2/providers/__init__.py`
    - `src/v2/providers/base.py`
  - Added provider interface coverage:
    - `tests/v2/test_provider_interfaces.py`
- **V2 Phase 2 agent wrappers (`P2-02`, `P2-03`, `P2-04`)**:
  - Added shared v2 agent result/provider models:
    - `src/v2/agents/models.py`
  - Added permissive-schema wrappers:
    - `src/v2/agents/repository_agent.py`
    - `src/v2/agents/person_agent.py`
    - `src/v2/agents/organization_agent.py`
    - `src/v2/agents/__init__.py`
  - Added focused v2 agent coverage:
    - `tests/v2/test_repository_agent.py`
    - `tests/v2/test_person_agent.py`
    - `tests/v2/test_organization_agent.py`
- **V2 Phase 2 retry + soft-failure (`P2-05`)**:
  - Added reusable retry wrapper:
    - `src/v2/agents/retry.py`
  - Extended `AgentResult` with partial-failure metadata and retry stats:
    - `src/v2/agents/models.py`
  - Exported retry helper:
    - `src/v2/agents/__init__.py`
  - Added retry behavior coverage:
    - `tests/v2/test_agent_retry.py`
- **V2 Phase 2 orchestrator graph + execution (`P2-06`, `P2-07`)**:
  - Added pipeline contracts and execution runtime:
    - `src/v2/pipeline/models.py`
    - `src/v2/pipeline/orchestrator.py`
    - `src/v2/pipeline/__init__.py`
  - Added orchestration graph and execution coverage:
    - `tests/v2/test_orchestrator_graph.py`
    - `tests/v2/test_orchestrator_execution.py`
- **V2 Phase 2 context-gather stage (`P2-08`)**:
  - Added context bundle model and gather stage:
    - `src/v2/pipeline/stages/models.py`
    - `src/v2/pipeline/stages/context_gather.py`
    - `src/v2/pipeline/stages/__init__.py`
  - Added stage coverage:
    - `tests/v2/test_context_gather.py`
- **V2 Phase 2 `/v2/extract` pipeline wiring (`P2-09`)**:
  - Added provider dependency injection:
    - `src/v2/dependencies.py`
  - Replaced extract stub logic with orchestrator execution:
    - `src/v2/api.py`
  - Added end-to-end extract coverage and promoted extract golden test to green:
    - `tests/v2/test_extract_e2e.py`
    - `tests/v2/test_extract_golden.py`
    - `tests/v2/golden/extract/repo_github_com_owner_repo.json`
    - `tests/v2/golden/extract/user_github_com_username.json`
    - `tests/v2/golden/extract/org_github_com_orgname.json`

### Changed
- **V2 Infoscience canonical URI strategy**:
  - Canonical Infoscience IDs for person/orgunit/publication now normalize to the single API endpoint form:
    - `https://infoscience.epfl.ch/server/api/core/items/{uuid}`
  - Kept support for legacy/alternate inputs during normalization:
    - `https://infoscience.epfl.ch/entities/{person|orgunit|publication}/{uuid}`
    - `https://infoscience.epfl.ch/server/api/entities/{person|orgunit|publication}/{uuid}/full`
    - `https://infoscience.epfl.ch/server/api/core/items/{uuid}`
  - Extended reconciliation to canonicalize article IDs and article cross-references (`schema:author`, `schema:sourceOrganization`) against resolved person/organization IDs.
- **V2 export surfaces for reconciliation and enum alignment**:
  - Extended `src/v2/canonicalization/__init__.py` with repository ID resolution export.
  - Extended `src/v2/pipeline/stages/__init__.py` with reconciliation/output assembly exports.
  - Extended `src/v2/models/__init__.py` with v2 enum exports.
- **Dependency management**:
  - Pinned `pyshacl` to `==0.22.2` in `pyproject.toml` for deterministic SHACL validation environments.
- **SHACL validation behavior**:
  - Updated `src/v2/validation/shacl_validation.py` to validate against a merged data+ontology graph so `sh:class` checks resolve ontology enum instances consistently with installed `pyshacl`.
- **Agent workflow documentation**:
  - Advanced the phase entry task to `P4-01-sqlite-schema-migrations.md` after completing `P3-05` through `P3-08`.
- **Validation and canonicalization package exports**:
  - Extended `src/v2/validation/__init__.py` exports with strict/SHACL validators and ontology loader helpers.
- **Agent workflow documentation**:
  - Advanced the phase entry task to `P3-05-canonical-id-repository.md` after completing `P3-01` through `P3-04`.
- **Agent workflow documentation**:
  - Advanced the phase entry task to `P3-01-strict-json-schema-gate.md` after completing `P2-05` through `P2-09`.
- **Agent workflow documentation**:
  - Advanced the phase entry task to `P2-01-provider-interfaces.md` after completing `P1-09` and `P1-10`.
- **Agent workflow documentation**:
  - Advanced the phase entry task to `P2-05-agent-retry-soft-failure.md` after completing `P2-01`, `P2-02`, `P2-03`, and `P2-04`.
- **Agent workflow documentation**:
  - Advanced the phase entry task to `P1-09-mount-v2-router.md` after completing `P1-05`, `P1-06`, `P1-07`, and `P1-08`.
- **Agent workflow documentation**:
  - Updated `AGENTS.md` with a dedicated `V2 Phase 0 TDD Track` section.
  - Advanced the phase entry task to `P0-03-test-infrastructure.md` after completing `P0-02`.
  - Added explicit validation commands for promoted schemas:
    - `python -m json.tool src/v2/schemas/strict/*.json`
    - `python -m json.tool src/v2/schemas/agent/*.json`
    - `just test-file tests/v2/test_promoted_strict_schemas.py`
    - `just test-file tests/v2/test_promoted_agent_schemas.py`
- **Pytest configuration**:
  - Registered the `v2` marker in `pyproject.toml` under `[tool.pytest.ini_options]`.
- **Agent workflow documentation**:
  - Advanced the phase entry task to `P0-04-strict-schema-valid-tests.md` after completing `P0-03`.
  - Added explicit v2 infrastructure check commands:
    - `pytest tests/v2/ --collect-only`
    - `pytest tests/v2 -m v2 --collect-only`
    - `just test-file tests/v2/test_test_infrastructure.py`
- **Agent workflow documentation**:
  - Advanced the phase entry task to `P0-05-agent-schema-valid-tests.md` after completing `P0-04`.
- **Agent workflow documentation**:
  - Advanced the phase entry task to `P0-06-negative-schema-tests.md` after completing `P0-05`.
- **Agent workflow documentation**:
  - Advanced the phase entry task to `P0-07-mock-github-provider.md` after completing `P0-06`.
- **Agent workflow documentation**:
  - Advanced the phase entry task to `P0-08-mock-orcid-provider.md` after completing `P0-07`.
- **Agent workflow documentation**:
  - Kept the phase entry task at `P0-08-mock-orcid-provider.md` as the earliest remaining dependency before `P0-12`.
- **Agent workflow documentation**:
  - Advanced the phase entry task to `P1-01-package-skeleton.md` after completing Phase 0 tasks `P0-08`, `P0-12`, `P0-13`, and `P0-14`.
- **Agent workflow documentation**:
  - Advanced the phase entry task to `P1-02-config-module.md` after completing `P1-01`.
- **Environment example configuration**:
  - Extended `.env.example` with v2-related keys:
    - `GITHUB_TOKEN`
    - `V2_GRAPH_DB_PATH`
    - `V2_INTERMEDIATE_HISTORY_LIMIT`
    - `V2_ENABLE_LOGFIRE`
    - `LOGFIRE_TOKEN`
- **Agent workflow documentation**:
  - Advanced the phase entry task to `P1-05-response-contracts.md` after completing `P1-02`, `P1-03`, and `P1-04`.

### Testing
- Ran task-focused canonicalization/reconciliation checks with repo venv:
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/test_canonical_id_person.py tests/v2/test_canonical_id_organization.py tests/v2/test_canonical_id_article.py tests/v2/test_reconciliation.py -v`
    - Result: `26 passed`
- Ran scoped reliability checks with repo venv:
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/ --collect-only`
    - Result: `321 collected`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/test_schema_validation_strict.py -v`
    - Result: `39 passed`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2 -m v2 --collect-only`
    - Result: `321 collected`
- Ran task-focused checks for `P3-05` to `P3-08` with repo venv:
  - `PYTHONPATH=. .venv/bin/ruff check src/v2/canonicalization/__init__.py src/v2/canonicalization/id_resolution.py src/v2/pipeline/stages/__init__.py src/v2/pipeline/stages/models.py src/v2/pipeline/stages/reconciliation.py src/v2/pipeline/stages/output_assembly.py src/v2/models/__init__.py src/v2/models/enums.py scripts/v2/extract_enums_from_ttl.py tests/v2/test_canonical_id_repository.py tests/v2/test_reconciliation.py tests/v2/test_partial_failure.py tests/v2/test_enum_alignment.py`
  - `PYTHONPATH=. .venv/bin/mypy --follow-imports=skip src/v2/canonicalization src/v2/pipeline/stages src/v2/models/enums.py`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/test_canonical_id_repository.py tests/v2/test_reconciliation.py tests/v2/test_partial_failure.py tests/v2/test_enum_alignment.py -v`
- Ran scoped reliability checks with repo venv:
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/ --collect-only`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/test_schema_validation_strict.py -v`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2 -m v2 --collect-only`
  - `PYTHONPATH=. .venv/bin/pytest -m v2 --collect-only`
- Ran SHACL dependency validation checks with repo venv:
  - `PYTHONPATH=. .venv/bin/ruff check src/v2/validation/shacl_validation.py`
  - `PYTHONPATH=. .venv/bin/mypy --follow-imports=skip src/v2/validation/shacl_validation.py`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/test_shacl_validation.py -v`
- Ran task-focused checks for strict validation gates and canonical ID resolution with repo venv:
  - `PYTHONPATH=. .venv/bin/ruff check src/v2/validation/__init__.py src/v2/validation/schema_validation.py src/v2/validation/ontology.py src/v2/validation/shacl_validation.py src/v2/canonicalization/__init__.py src/v2/canonicalization/id_resolution.py tests/v2/test_strict_validation_gate.py tests/v2/test_shacl_validation.py tests/v2/test_canonical_id_person.py tests/v2/test_canonical_id_organization.py`
  - `PYTHONPATH=. .venv/bin/mypy --follow-imports=skip src/v2/validation src/v2/canonicalization`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/test_strict_validation_gate.py tests/v2/test_canonical_id_person.py tests/v2/test_canonical_id_organization.py tests/v2/test_shacl_validation.py -v`
- Ran scoped reliability checks with repo venv:
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/ --collect-only`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/test_schema_validation_strict.py -v`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2 -m v2 --collect-only`
  - `PYTHONPATH=. .venv/bin/pytest -m v2 --collect-only`
- Ran task-focused checks for `P2-05` to `P2-09` with repo venv:
  - `PYTHONPATH=. .venv/bin/ruff check src/v2/agents/models.py src/v2/agents/retry.py src/v2/agents/__init__.py src/v2/pipeline/__init__.py src/v2/pipeline/models.py src/v2/pipeline/orchestrator.py src/v2/pipeline/stages/__init__.py src/v2/pipeline/stages/context_gather.py src/v2/pipeline/stages/models.py src/v2/dependencies.py src/v2/api.py tests/v2/test_agent_retry.py tests/v2/test_orchestrator_graph.py tests/v2/test_orchestrator_execution.py tests/v2/test_context_gather.py tests/v2/test_extract_e2e.py tests/v2/test_extract_golden.py`
  - `PYTHONPATH=. .venv/bin/mypy --follow-imports=skip src/v2/agents/retry.py src/v2/pipeline src/v2/dependencies.py src/v2/api.py`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/test_agent_retry.py tests/v2/test_orchestrator_graph.py tests/v2/test_orchestrator_execution.py tests/v2/test_context_gather.py tests/v2/test_extract_e2e.py tests/v2/test_extract_golden.py -q`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/test_api_extract_stub.py::test_extract_repository_url_returns_detected_repository tests/v2/test_extract_e2e.py::test_extract_endpoint_runs_pipeline_with_mock_providers -v`
- Ran scoped reliability checks with repo venv:
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/ --collect-only`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/test_schema_validation_strict.py -v`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2 -m v2 --collect-only`
  - `PYTHONPATH=. .venv/bin/pytest -m v2 --collect-only`
- Ran task-focused checks for `P1-09` and `P1-10` with repo venv:
  - `PYTHONPATH=. .venv/bin/ruff check src/v2/api.py src/v2/models/contracts.py src/v2/models/__init__.py tests/v2/test_api_mount_v2_router.py tests/v2/test_api_health.py`
  - `PYTHONPATH=. .venv/bin/ruff check --select I src/api.py`
  - `PYTHONPATH=. .venv/bin/mypy --follow-imports=skip src/v2/models/contracts.py src/v2/api.py`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/ --collect-only`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/test_schema_validation_strict.py -v`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2 -m v2 --collect-only`
  - `PYTHONPATH=. .venv/bin/pytest -m v2 --collect-only`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/test_api_mount_v2_router.py tests/v2/test_api_health.py -v`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/test_api_mount_v2_router.py tests/v2/test_api_health.py tests/v2/test_api_extract_stub.py tests/v2/test_api_graph_stub.py -v`
- Ran task-focused checks for `P1-05` to `P1-08` with repo venv:
  - `PYTHONPATH=. .venv/bin/ruff check src/v2/api.py src/v2/models/contracts.py src/v2/models/errors.py src/v2/models/__init__.py tests/v2/test_response_contracts.py tests/v2/test_error_models.py tests/v2/test_api_extract_stub.py tests/v2/test_api_graph_stub.py`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/test_response_contracts.py tests/v2/test_error_models.py tests/v2/test_api_extract_stub.py tests/v2/test_api_graph_stub.py -v`
- Ran scoped reliability checks with repo venv:
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/ --collect-only`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/test_schema_validation_strict.py -v`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2 -m v2 --collect-only`
- Ran task-focused checks for `P2-01` to `P2-04` with repo venv:
  - `PYTHONPATH=. .venv/bin/ruff check src/v2/providers/base.py src/v2/providers/__init__.py src/v2/providers/github_provider.py src/v2/providers/infoscience_provider.py src/v2/providers/orcid_provider.py src/v2/providers/ror_provider.py src/v2/agents/__init__.py src/v2/agents/models.py src/v2/agents/repository_agent.py src/v2/agents/person_agent.py src/v2/agents/organization_agent.py tests/v2/test_provider_interfaces.py tests/v2/test_repository_agent.py tests/v2/test_person_agent.py tests/v2/test_organization_agent.py`
  - `PYTHONPATH=. .venv/bin/mypy --follow-imports=skip src/v2/agents src/v2/providers`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/ --collect-only`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/test_schema_validation_strict.py -v`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2 -m v2 --collect-only`
  - `PYTHONPATH=. .venv/bin/pytest -m v2 --collect-only`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/test_provider_interfaces.py tests/v2/test_repository_agent.py tests/v2/test_person_agent.py tests/v2/test_organization_agent.py -v`
  - `PYTHONPATH=. .venv/bin/pytest -m v2 --collect-only`
- Added `tests/v2/test_promoted_strict_schemas.py` to verify:
  - promoted schema files exist and parse as JSON,
  - promoted files are byte-identical to source artifacts in `dev/`,
  - each promoted schema passes `jsonschema` meta-schema validation.
- Added `tests/v2/test_promoted_agent_schemas.py` to verify:
  - promoted agent schema files exist and parse as JSON,
  - promoted files are byte-identical to source artifacts in `dev/`,
  - each promoted schema passes `jsonschema` meta-schema validation,
  - each agent schema preserves all property names present in its strict counterpart.
- Added `tests/v2/test_test_infrastructure.py` to verify:
  - `load_schema("strict", "person")` returns a parsed JSON object,
  - `load_fixture("schema/strict", "person.schema")` resolves nested fixture groups,
  - `v2_test_config` points to expected test fixture/golden roots.
- Added `tests/v2/test_schema_validation_strict.py` to verify:
  - strict fixtures meet minimum instance coverage per entity:
    - Person (>=5), Repository (>=4), Organization (>=5), Membership (>=6), Contribution (>=9), Article (>=4),
  - every valid fixture instance passes `jsonschema.validate()` against promoted strict schemas.
- Added `tests/v2/test_schema_validation_agent.py` to verify:
  - each strict valid fixture group has at least one instance for all six entity types,
  - every valid strict fixture instance passes `jsonschema.validate()` against promoted agent schemas.
- Added `tests/v2/test_schema_validation_negative.py` to verify:
  - each invalid fixture is rejected by its corresponding strict schema with `jsonschema.ValidationError`,
  - coverage includes at least 8 distinct invalid fixtures spanning required fields, patterns, enums, `anyOf`, `additionalProperties`, and numeric minimum constraints.
- Added `tests/v2/test_mock_github_provider.py` to verify:
  - `MockGitHubProvider` implements the abstract `GitHubProvider` interface methods,
  - repository lookup returns a GitHub REST-shaped payload for `octocat/Hello-World`,
  - provider error paths raise typed exceptions for not found, rate limit, and private repository access,
  - GitHub provider fixture files exist with valid JSON and include both REST and GraphQL mock response shapes.
- Added `tests/v2/test_mock_infoscience_provider.py` to verify:
  - `MockInfoscienceProvider` implements the abstract `InfoscienceProvider` interface methods,
  - person search returns single-hit and multi-hit fixtures (ambiguous candidate scenario),
  - empty-result queries return an empty list without raising provider errors,
  - fixture catalog/JSON validity checks for Infoscience payloads.
- Added `tests/v2/test_mock_ror_provider.py` to verify:
  - `MockRORProvider` implements the abstract `RORProvider` interface methods,
  - organization detail fixtures include names, aliases, types, country data, and parent/child relationships,
  - search fixtures return ranked candidate organizations,
  - alias fixtures include alternate names, acronyms, and multilingual labels.
- Added `tests/v2/test_mock_generator.py` to verify:
  - `generate_dataset(seed=42)` is deterministic and `seed=99` yields different output,
  - generated entities validate against promoted strict schemas,
  - cross-reference integrity across persons, repositories, organizations, memberships, contributions, and articles,
  - `--edge-cases` generation includes UUID-only identities, zero-count contributions, and forked repositories,
  - CLI `scripts/v2/generate_mock_data.py` writes expected `pulse_*` JSON files to disk.
- Added `tests/v2/test_mock_orcid_provider.py` to verify:
  - `MockORCIDProvider` implements abstract `ORCIDProvider`,
  - fixture-backed valid/no-affiliation/multi-employment ORCID responses are returned with normalized structure,
  - invalid checksum and malformed ORCID formats are rejected at provider level.
- Added `tests/v2/test_crossref_consistency.py` to verify:
  - seed-generated dataset (`seed=42`) passes cross-reference checks with zero invalid references,
  - orphaned contribution author references are detected,
  - ownership symmetry (`pulse:owns` ↔ `pulse:ownedBy`) mismatches are detected,
  - membership composite IDs resolve to valid person+organization pairs.
- Added `tests/v2/test_extract_golden.py` (`xfail`, red phase) to define `/v2/extract` contract expectations for:
  - repository URL input,
  - user URL input,
  - organization URL input.
- Added `tests/v2/test_graph_golden.py` (`xfail`, red phase) to define `/v2/graph` contract expectations for:
  - full graph envelope,
  - filtering by entity type,
  - filtering by source URL.
- Ran scoped reliability checks with repo venv:
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/ --collect-only`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/test_schema_validation_strict.py -v`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2 -m v2 --collect-only`
- Ran additional phase-scoped v2 checks with repo venv:
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/test_promoted_agent_schemas.py -v`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/test_test_infrastructure.py -v`
  - `PYTHONPATH=. .venv/bin/pytest -m v2 --collect-only`
- Added `tests/v2/test_config.py` to verify:
  - valid config creation with required environment variables,
  - descriptive `ValueError` on missing `GITHUB_TOKEN`,
  - default `V2_GRAPH_DB_PATH`,
  - `V2_ENABLE_LOGFIRE=false` behavior,
  - optional `LOGFIRE_TOKEN` behavior when Logfire is enabled.
- Added `tests/v2/test_url_classifier.py` to verify:
  - repository/user/organization detection,
  - `.git` stripping,
  - default user classification for ambiguous `https://github.com/<name>`,
  - normalization of trailing slashes, query params, fragments,
  - non-GitHub URL rejection via `ValueError`.
- Added `tests/v2/test_url_classifier_edge_cases.py` to verify:
  - at least 10 unsupported subresource rejection cases with specific reasons,
  - HTTP to HTTPS upgrade,
  - URL-encoded path segment handling,
  - configurable GitHub Enterprise base URL behavior.
- Ran task-focused checks with repo venv:
  - `PYTHONPATH=. .venv/bin/ruff check src/v2/config.py src/v2/detection/__init__.py src/v2/detection/models.py src/v2/detection/github_url_classifier.py tests/v2/test_config.py tests/v2/test_url_classifier.py tests/v2/test_url_classifier_edge_cases.py`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/test_config.py tests/v2/test_url_classifier.py tests/v2/test_url_classifier_edge_cases.py -q`
- Ran scoped reliability checks with repo venv:
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/ --collect-only`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2/test_schema_validation_strict.py -v`
  - `PYTHONPATH=. .venv/bin/pytest tests/v2 -m v2 --collect-only`
  - `PYTHONPATH=. .venv/bin/pytest -m v2 --collect-only`


## [2.0.1] - 2026-02-16

### Added
- Documentation and CI for github-pages

### Changed
- Bumped project version to `2.0.1`.
- Updated API version metadata and root welcome message to `v2.0.1`.



## [2.0.0] - 2025-10-07

### Added
- **Project restructuring** for improved maintainability and modularity:
  - Reorganized `src/core/` monolithic directory into categorized subdirectories under `src/`:
    - `src/agents/` - PydanticAI agents for organization and user enrichment
    - `src/cache/` - Caching infrastructure and SQLite cache manager
    - `src/data_models/` - Pydantic models and schemas (Person, Organization, SoftwareSourceCode, etc.)
    - `src/gimie/` - GIMIE integration methods for repository metadata extraction
    - `src/llm/` - LLM processing and GenAI model wrapper
    - `src/parsers/` - Organization and user parsers for structured data extraction
    - `src/validation/` - Verification and validation logic
  - Created proper `__init__.py` files with explicit exports for all modules
  - Improved import paths throughout the codebase (e.g., `from src.agents import...` instead of `from src.core.organization_enrichment import...`)
  - Enhanced code organization and discoverability
- **SQLite-based caching system** for external API calls (GitHub, ORCID, GIMIE, LLM)
  - Automatic TTL (Time To Live) expiration with configurable settings per API type
  - Default TTL: 30 days (LLM), 7 days (GitHub users/orgs), 14 days (ORCID), 1 day (GIMIE)
  - Thread-safe operations for concurrent access
  - JSON storage for complex API responses
- **Force refresh capability** via `force_refresh` query parameter on all data endpoints
- **Cache management endpoints**:
  - `GET /v1/cache/stats` - View comprehensive cache statistics
  - `POST /v1/cache/cleanup` - Remove expired cache entries
  - `POST /v1/cache/clear` - Clear all cache entries
  - `POST /v1/cache/enable` - Enable caching system
  - `POST /v1/cache/disable` - Disable caching system
  - `DELETE /v1/cache/invalidate/{api_type}` - Invalidate specific cache entries
- **Environment-based cache configuration**:
  - `CACHE_ENABLED` - Enable/disable caching
  - `CACHE_DEFAULT_TTL_DAYS` - Default TTL in days
  - `CACHE_DB_PATH` - Custom database location
  - API-specific TTL overrides (e.g., `CACHE_GITHUB_USER_TTL_DAYS`)
  - Cache size and cleanup settings
- **Enhanced FastAPI documentation**:
  - Comprehensive API metadata (title, description, version, contact, license)
  - Detailed endpoint docstrings with parameter and return descriptions
  - Organized API endpoints with tags (Repository, User, Organization, Cache Management, System)
  - OpenAPI schema improvements for better interactive documentation
- **Cache statistics and monitoring**:
  - Total entries and active/expired counts
  - Entries breakdown by API type
  - Hit counts for cache effectiveness analysis
  - Database size reporting
- **Performance benefits**:
  - Up to 90% reduction in external API requests
  - Faster response times with instant cache retrieval
  - Rate limit protection for GitHub/ORCID APIs
  - Cost savings on LLM API calls
- **ORCID affiliation enrichment**:
  - Automatic extraction of ORCID IDs from author metadata
  - Selenium-based scraping of ORCID profiles for employment and education history
  - Smart affiliation merging that preserves existing affiliations and adds ORCID data
  - Support for both Zod format (`schema:author`, `md4i:orcidId`) and plain format (`author`, `orcidId`)
  - Integration with both main extraction and LLM JSON endpoints
- **Enhanced logging system**:
  - Comprehensive logging for ORCID enrichment process
  - Detailed error handling and debugging information
  - Cache operation logging for monitoring and troubleshooting
  - Selenium operation logging for ORCID scraping
- **GPT-5 model support** - Full support for GPT-5 and reasoning models
  - Support for GPT-5, GPT-5 variants (gpt-5-mini, gpt-5-nano), o3-mini, and o4-mini models
  - Proper model detection logic to handle GPT-5 and reasoning models
  - Uses `beta.chat.completions.parse()` with structured outputs for all models
  - Lazy initialization for async OpenAI client to prevent API key issues at module load
  - Comprehensive error logging with error type and detailed debugging information
  - Retry logic with exponential backoff for handling connection errors
  - Unified response parsing for all OpenAI models using `.parsed` attribute
- **Organization Enrichment System** using PydanticAI for agentic analysis
  - Second-pass analysis to refine and enrich organization information
  - PydanticAI agent with intelligent tool usage for:
    - ROR (Research Organization Registry) API queries for standardized org data
    - Web search integration (DuckDuckGo) for additional context
    - Email domain analysis for institutional affiliation detection
  - Enhanced `Organization` model with new fields:
    - `alternateNames` - Other names the organization is known by
    - `organizationType` - Type classification (university, lab, company, etc.)
    - `parentOrganization` - Parent organization for hierarchical relationships
    - `country` - Country location
    - `website` - Official website URL
  - Optional `enrich_orgs=true` parameter on existing `/v1/repository/llm/json` endpoint
    - Non-breaking change - enrichment only runs when explicitly requested
    - Analyzes git author emails, ORCID affiliations, and existing metadata
    - Provides detailed EPFL relationship analysis with evidence
    - Graceful error handling - errors don't break the main request
  - Comprehensive documentation in `docs/ORGANIZATION_ENRICHMENT.md`
  - Example script: `examples/example_organization_enrichment.py`
  - Test suite: `tests/test_organization_enrichment.py`
- **Organization enrichment for User and Organization endpoints**
  - Added `enrich_orgs=true` query parameter to `/v1/user/llm/json/{full_path:path}` endpoint
  - Added `enrich_orgs=true` query parameter to `/v1/org/llm/json/{full_path:path}` endpoint
  - Both endpoints now support ROR (Research Organization Registry) enrichment
  - Consistent enrichment functionality across repository, user, and organization endpoints
  - Enhanced organization metadata with ROR IDs, types, countries, websites, and hierarchical relationships
  - Detailed EPFL relationship analysis for user and organization profiles
- **Git commit temporal tracking**:
  - Added `Commits` model with `firstCommitDate` and `lastCommitDate` fields per author
  - Enhanced `extract_git_authors()` to extract first and last commit dates using git log
  - Dates stored in ISO format (YYYY-MM-DD) for consistency
  - JSON-LD context mappings added for `imag:firstCommitDate` and `imag:lastCommitDate`
- **Organization confidence scoring system**:
  - Added `confidenceOfAttribution` field to `Organization` model (0.0-1.0 scale)
  - Added `relatedToEPFLConfidence` field to `OrganizationEnrichmentResult` model
  - Enhanced PydanticAI agent with detailed confidence scoring guidelines:
    - 0.9-1.0: Strong evidence (verified affiliations, official emails, ORCID data)
    - 0.7-0.89: Good evidence (domain match, indirect affiliation)
    - 0.5-0.69: Moderate evidence (collaborations, shared projects)
    - 0.3-0.49: Weak evidence (geographical proximity, field similarity)
    - 0.0-0.29: Minimal or no evidence
  - Confidence assessment considers temporal alignment between commit dates and affiliation dates
  - JSON-LD context mapping for `imag:confidenceOfAttribution`
- **ORCID parser overhaul** - Complete rewrite for reliability and data completeness:
  - Fixed employment extraction to parse line-by-line text content instead of unreliable HTML containers
  - Fixed education extraction with same line-by-line parsing approach
  - Enhanced date extraction to support multiple formats:
    - Full dates: `YYYY-MM-DD to YYYY-MM-DD`
    - Year ranges: `YYYY to YYYY`
    - Ongoing: `YYYY-MM-DD to present`
  - Fixed role extraction to recognize ORCID's `|` separator format (e.g., "Institut Pasteur | PhD Student")
  - Fixed degree extraction for education entries (MSc, BSc, PhD, etc.)
  - Enhanced duration calculation to handle full date formats with decimal precision (e.g., 3.2 years)
  - Fixed location parsing to eliminate double commas and clean formatting
  - All fields now reliably extracted: dates, roles, degrees, locations, durations
  - Validated with real ORCID profiles (e.g., 0000-0002-1126-1535)
- **Dependencies**: Added `httpx` for async HTTP requests in organization enrichment
- **Docker volume mounting** for persistent cache storage:
  - Support for mounting `./data` directory to `/app/data` in container
  - Environment variable `CACHE_DB_PATH` for custom cache database location
  - Enables cache persistence across container restarts
- **Environment-based log level configuration**:
  - Added `LOG_LEVEL` environment variable support (DEBUG, INFO, WARNING, ERROR)
  - Allows dynamic logging configuration without code changes
  - New `serve-dev-debug` justfile recipe for easy debug mode startup
  - Enhanced subprocess logging with full stderr/stdout output (no truncation)
- **Enhanced debugging capabilities** for repository processing:
  - Comprehensive debug logging for git clone operations with directory contents
  - Full error output from repo-to-text subprocess (complete tracebacks)
  - Directory existence checks and file listing for troubleshooting
  - Detailed diagnostics when no .txt files are found after repo-to-text
- **ORCID validation and normalization**:
  - Added `normalize_orcid_to_url()` function to convert ORCID IDs to standard URL format
  - ORCID validation now accepts both ID format (0000-0002-1234-5678) and URL format (https://orcid.org/0000-0002-1234-5678)
  - Automatic normalization to URL format before enrichment and scraping
  - Enhanced validation in both scraping flow and enrichment flow
- **Auto-enrichment flag** for conditional ORCID enrichment:
  - Added `auto_enrich_orcid` query parameter (default: `true`) to repository endpoints
  - Allows users to disable automatic ORCID enrichment when not needed
  - Reduces API calls and processing time for use cases that don't require affiliation data
- **GitHub API authentication** to avoid rate limits:
  - Added GitHub token authentication to `is_github_repo_public()` function
  - Uses `GITHUB_TOKEN` environment variable for authenticated requests
  - Increased rate limit from 60/hour (unauthenticated) to 5000/hour (authenticated)
  - Detailed rate limit logging for monitoring
- **Google Search integration** via Selenium for organization enrichment:
  - Replaced DuckDuckGo Instant Answer API with Selenium-based Google search
  - Extracts top 5 search results with title, link, and snippet
  - Reuses existing Selenium infrastructure (shared with ORCID scraping)
  - Comprehensive error handling with multiple CSS selector fallbacks
  - Improved search result quality and coverage for organization queries
  - Documentation in `docs/GOOGLE_SEARCH_IMPLEMENTATION.md`
- **Comprehensive logging** for organization enrichment:
  - Added detailed logging to all PydanticAI agent tools (search_ror, search_web, extract_domain_from_email)
  - Emoji indicators for visual scanning (🔍 calls, ✓ success, ✗ errors, 🤖 agent, 📍 results)
  - Logging for main enrichment functions (enrich_organizations, enrich_organizations_from_dict)
  - Enhanced observability into agent operations and decision-making
- **Unknown domain detection** with automatic search suggestions:
  - Enhanced `extract_domain_from_email` tool to detect unknown email domains
  - Automatically suggests ROR and web searches for organizations not in known domains dictionary
  - Updated system prompt to instruct agent to follow search suggestions
  - Improved organization discovery coverage beyond pre-configured domains
  - Known domains include: EPFL, ETH Zürich, Institut Pasteur, UNIL, Swiss Data Science Center
- **Enhanced colored logging with request tracking**:
  - ANSI color-coded logs with emojis for different log levels (🔵 DEBUG, ✅ INFO, ⚠️ WARNING, ❌ ERROR)
  - Request ID tracking across all async operations using AsyncRequestContext
  - Automatic request context via FastAPI middleware for all endpoints
  - Request IDs formatted with endpoint prefix (org-, user-, repo-, cache-) + worker PID + unique ID
  - Incoming request logging with 📥 emoji showing method, path, and query parameters
  - Response logging with 📤 emoji showing status code
  - All logs include request ID in brackets for easy correlation (e.g., [repo-8-6479])
- **User enrichment system** using PydanticAI for comprehensive author analysis:
  - Second-pass analysis to refine and enrich author/contributor information
  - PydanticAI agent with intelligent analysis of:
    - Git commit author data (names, emails, commit history)
    - ORCID profile data (affiliations, publications)
    - Email domain analysis for institutional connections
  - Enhanced author metadata with enriched affiliations and profile data
  - Available via `enrich_users=true` parameter on user and repository endpoints
  - Graceful error handling - errors don't break the main request
- **Complete enrichment coverage for all repository endpoints**:
  - `/v1/extract/json/{full_path:path}` now supports:
    - ✅ ORCID enrichment with `auto_enrich_orcid` parameter
    - ✅ Organization enrichment with `enrich_orgs` parameter
    - ✅ User enrichment with `enrich_users` parameter
  - `/v1/extract/json-ld/{full_path:path}` now supports:
    - ✅ ORCID enrichment with `auto_enrich_orcid` parameter
    - ✅ Organization enrichment with `enrich_orgs` parameter
    - ✅ User enrichment with `enrich_users` parameter
  - `/v1/repository/llm/json/{full_path:path}` now supports:
    - ✅ ORCID enrichment (existing)
    - ✅ Organization enrichment with `enrich_orgs` parameter (existing)
    - ✅ User enrichment with `enrich_users` parameter (new)
  - All three main repository endpoints now have consistent, comprehensive enrichment capabilities

### Changed
- **Project structure modernization**:
  - Removed monolithic `src/core/` directory in favor of feature-based modules
  - All imports updated from `.core.*` pattern to direct module imports (`.agents`, `.cache`, `.data_models`, etc.)
  - Improved separation of concerns with dedicated modules for each functional area
- API version updated to 2.0.0 across all endpoints
- **Upgraded pydantic-ai to version 1.0.15**:
  - Migrated from deprecated `result_type` parameter to new `output_type` parameter
  - Updated both organization enrichment and user enrichment agents
  - Changed all `result.data` references to `result.output` for compatibility with new API
  - Ensures compatibility with latest pydantic-ai features and improvements
- **Improved repo-to-text error handling** for more resilient repository processing:
  - Changed from strict failure on non-zero exit codes to lenient handling
  - Now continues processing if .txt files are created despite exit code 1
  - Handles cases where repo-to-text writes warnings to stderr but still succeeds
  - Prevents data loss from repositories that process successfully but return error codes
  - Added warning logs instead of immediate failure for better observability
- **Fixed token parameter handling** for OpenAI reasoning models:
  - o3-mini and o4-mini now correctly use `max_completion_tokens` instead of `max_tokens`
  - Standard models (gpt-4o-mini, gpt-5) continue to use `max_tokens`
  - Prevents token limit errors with reasoning models
- **Replaced DuckDuckGo with Google Search** for organization enrichment:
  - DuckDuckGo Instant Answer API was returning empty results for many queries
  - Google search via Selenium provides comprehensive, reliable results
  - No additional infrastructure needed (reuses existing Selenium instance)
- All data endpoints now support caching with `force_refresh` parameter
- Response format includes `cached` status indicator
- Author metadata now automatically enriched with ORCID affiliations
- Both `/v1/extract/json/` and `/v1/repository/llm/json/` endpoints include ORCID enrichment
- Selenium configuration now uses environment variable `SELENIUM_REMOTE_URL`
- Updated OpenAI Python SDK dependency to version 2.1.0 for better GPT-5 support
- Refactored `genai_model.py` to use consistent structured output handling across all models
- Enhanced logging to show model configuration and API call progress
- Fixed logger initialization order to prevent undefined variable errors
- Removed `cached` field from user and organization endpoint responses
- Updated response structure to match repository endpoint: `{"link": ..., "output": ...}`
- User and organization endpoints now return `relatedToOrganizationsROR` with full ROR metadata when `enrich_orgs=true`
- Improved consistency across all LLM-based endpoints

### Fixed

- **Removed duplicate validation summary printing** in verification module:
  - Validation issues now appear only once in logs (as ERROR/WARNING with request IDs)
  - Removed redundant formatted print statements from `summary()` method
  - Cleaner log output without duplicate validation summaries
- **Fixed 400 Bad Request error** for cached LLM results in `/v1/repository/llm/json`:
  - Added JSON parsing for cached responses that may be stored as JSON strings
  - Handles both dict and JSON string responses from cache
  - Prevents parsing errors when retrieving cached LLM data

### Documentation

- Added comprehensive cache documentation in `docs/CACHE_README.md`
- Updated API endpoint documentation with caching information
- Added cache configuration examples and environment variables reference
- Added ORCID affiliations documentation in `docs/ORCID_AFFILIATIONS.md`
- Created ORCID implementation summary with technical details


## [1.0.0] - 2025-08-06

### Added

- Users and Organization compatibility
- Endpoints refactoring
- Parallel calling
- Multiworkers entrypoint

## [0.1.0] - 2025-06-25

### Added

- Initial project setup.
- Dockerfile for containerization.
- GitHub Actions workflow for automated publishing and releases.
