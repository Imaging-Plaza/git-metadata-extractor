# GitLab index — design spec

**Status:** approved design, pending implementation plan.
**Date:** 2026-06-04.

## Purpose

Extend the index to cover GitLab the way it already covers GitHub. The
high-value targets are the Swiss-research self-hosted instances
(`gitlab.epfl.ch`, `gitlab.ethz.ch`, `gitlab.datascience.ch`) — consistent with
the index's EPFL/ETHZ focus (infoscience, ethz_research_collection, snsf) —
plus an EPFL-scoped slice of `gitlab.com`. Each GitLab entity becomes a
first-class, semantically-searchable store analogous to `github_repos` /
`github_organizations` / `github_users`.

## Scope

Four instances × three entity types = **12 stores**:

| instance key | host | entity types |
|---|---|---|
| `com` | gitlab.com | projects, groups, users |
| `epfl` | gitlab.epfl.ch | projects, groups, users |
| `ethz` | gitlab.ethz.ch | projects, groups, users |
| `datascience` | gitlab.datascience.ch | projects, groups, users |

Store names: `gitlab_<instance>_<type>` — e.g. `gitlab_epfl_projects`,
`gitlab_com_groups`. One DuckDB + one Qdrant collection + one federated adapter
each, exactly mirroring the github account stores (maximum isolation: an
instance can be rebuilt/dropped independently).

Out of scope (v1): issues/MRs/pipelines (the crawler can fetch them, but the
index models projects/groups/users only); private projects beyond what a
configured token can see; non-EPFL `gitlab.com` content.

## Decisions

1. **Store layout — 12 stores, instance × type** (not a shared `platform`
   column). Chosen for maximum per-instance isolation, even though the crawler
   models carry a `platform` field and ids are host-qualified.
2. **Native reimplementation** (not a dependency on `open-pulse-crawler`). We
   learn the entity shapes/categories from its `GitLabProjectModel` /
   `GitLabGroupModel` / `GitLabUserModel`, but own the ingest code, matching
   every existing store and avoiding coupling to that package's API/cadence.
3. **Scope per instance**:
   - Self-hosted (`epfl`, `ethz`, `datascience`): enumerate **all public**
     projects/groups/users via the instance REST API (datascience.ch's
     `/explore/projects/active` is exactly the public-project listing).
   - `gitlab.com`: **curated EPFL-relevant seed namespaces** in config →
     graph-traverse (group → subgroups/projects/members), mirroring
     `zenodo_records`' curated-community-list approach. Bounded and relevant.
4. **✦ Vector-backed — all three types** (embed + Qdrant + chunks), analogous
   to the github stores. Instances are bounded, so cost is acceptable.
   *(Dial-back option if needed: projects-only embedding, groups/users
   DuckDB-only.)*
5. **✦ Auth** — optional per-instance token env `GITLAB_<INSTANCE>_TOKEN`
   (e.g. `GITLAB_EPFL_TOKEN`). Unauthenticated works for public data but is
   rate-limited; a token raises limits and (institutional) internal visibility.

Items marked ✦ are defaults to confirm at spec review.

## Canonical URL ids (v3.0.0)

The id of every entity is its canonical landing-page URL on its host — the
instance is encoded in the id, so ids are globally unique across the 12 stores.
**The GitLab API's `web_url` is authoritative** (it already encodes GitLab's own
URL conventions — e.g. groups live under `/groups/<full_path>`, projects and
users do not); we store `web_url` directly as the id rather than reconstructing
it. Typical shapes:

- project: `https://<host>/<full_path>` (e.g. `https://gitlab.epfl.ch/group/sub/proj`)
- group:   `https://<host>/groups/<full_path>`
- user:    `https://<host>/<username>`

New module `src/v2/canonicalization/gitlab.py`:
`gitlab_iri(host, kind, path)` (builder used only when we have a bare path and
no `web_url`; idempotent) + `parse_gitlab_iri(url)` (inverse →
`(host, kind, path)`). Mirrors the infoscience/ethz canonicalizers. The DuckDB
PK and the Qdrant payload id are this URL; Qdrant point ids derive via `uuid5`
of the URL (the snsf/infoscience scheme).

## Architecture

### Shared base (the DRY core)

New `src/index/_gitlab_base/`, modelled on `_github_accounts_base`:

- `client.py` — thin GitLab REST client (httpx): per-host base URL, optional
  token, keyset/page pagination, rate-limit handling (`RateLimit-Remaining`/
  `Retry-After`), and iterators: `iter_public_projects()`, `iter_public_groups()`,
  `iter_public_users()`, `iter_group_projects()`, `iter_subgroups()`,
  `iter_group_members()` (the last three for `gitlab.com` seed traversal).
- `models.py` — `GitLabProjectRecord` / `GitLabGroupRecord` / `GitLabUserRecord`
  (fields below).
- `paths_base.py`, `config_base.py`, `storage_base.py`, `embed_base.py`,
  `retrieval_base.py` — parameterized by `(instance, type)`, reusing the
  github base helpers' shapes (`bootstrap_schema`, `stream_unembedded`,
  `upsert_chunk`, RcpConfig/QdrantConfig/ChunkingConfig).

### Thin store packages (×12)

`src/index/gitlab_<instance>_<type>/` each wire: `paths.py` (duckdb at the
standard `data/index/<store>/duckdb/<store>.duckdb`), `config.py`, `storage/`
(schema.sql + a thin store binding to the base), `ingest/`, `embed/`,
`retrieval/`, and a federated adapter under
`src/index/_federated/adapters/gitlab_<instance>_<type>.py` registered in
`registry.py`. Each adapter sets manifest hints (`backend="vector"`,
`surface_as_source` per taste, `id_shape="url"`).

### Entity fields

Mapped from the crawler models, enriched to github depth:

- **project**: `project_id` (URL PK), full_path, name, description, visibility,
  is_fork, forked_from, namespace, topics (JSON), star_count, fork_count,
  last_activity_at, web_url, created_at, raw, ingested_at, + `chunks`.
- **group**: `group_id` (URL PK), full_path, name, description, visibility,
  parent (URL), web_url, raw, ingested_at, + `chunks`.
- **user**: `user_id` (URL PK), username, name, state, public_email, web_url,
  raw, ingested_at, + `chunks`.

## Data flow

```
config (per instance)  ─▶  GitLabClient (REST, paginated, rate-limited)
   ▼ enumerate/traverse
record mappers (→ *Record, id = canonical URL)
   ▼ upsert (URL PK, store boundary)
DuckDB <store>.duckdb  ─▶  embed (chunks → RCP)  ─▶  Qdrant <store> collection
   ▼ publish_snapshot                                   ▼
<store>.ro.duckdb (Hub)                          federated adapter → search/lookup
```

## Manifest & maintenance — free

The 12 stores land at the standard DuckDB path and register federated adapters,
so the maintenance driver (`python -m src.index._federated.maintenance`) covers
them by disk and they appear in `GET /v2/manifest` automatically.

## Incremental delivery (PR per phase, full `tests/v2/` gate each)

- **Phase 1 — template**: `src/v2/canonicalization/gitlab.py`, `_gitlab_base`
  (client + bases), and **one** store end-to-end: `gitlab_epfl_projects`
  (high-value, bounded). Proves the pattern.
- **Phase 2 — fan out self-hosted**: the remaining epfl/ethz/datascience types
  (groups, users; ethz/datascience projects). Mostly thin-package wiring.
- **Phase 3 — gitlab.com**: the curated-seed traversal + `gitlab_com_*` stores.

## Testing

Per the github store tests, with the GitLab client stubbed (no network):

- canonicalization: `gitlab_iri`/`parse_gitlab_iri` round-trip + idempotency.
- client: pagination + rate-limit handling against a fake transport.
- mappers: API JSON → `*Record` with URL ids.
- store: upsert round-trip asserting the PK is the canonical URL; embed
  `stream_unembedded` join; `--check`/manifest visibility.

## Risks / open items

- `gitlab.com` seed list curation is manual (config) — acceptable, matches
  zenodo_records.
- Rate limits on unauthenticated crawls — mitigated by per-instance tokens.
- Embedding volume across all three types — revisit the ✦ vector default if the
  self-hosted instances turn out larger than expected.
