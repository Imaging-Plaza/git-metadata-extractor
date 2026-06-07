# GitLab Index

A family of **9 standalone RAG indices** over three self-hosted GitLab
instances — EPFL, ETH Zurich, and the Swiss Data Science Center — covering
their **projects**, **groups**, and **users**. All 9 share one engine at
[`src/index/_gitlab_base/`](https://github.com/Imaging-Plaza/git-metadata-extractor/tree/main/src/index/_gitlab_base);
the thin per-instance leaves live at `src/index/gitlab_<instance>_<type>/`.

> **Two complementary views.** The federated layer
> ([`federated-search.md`](federated-search.md)) is the agent- and
> cross-index-facing surface. *This* page documents the per-store
> direct-access surfaces — the CLI entry points and the HTTP
> ingest + search endpoints — for analysts and scripts that target a single
> store.

## What it indexes

Each of the 9 stores is vector-backed (its own Qdrant collection),
`surface_as_source=True`, and registered in the federated layer, so all 9
appear in `GET /v2/manifest` and in federated search / lookup.

| Store | Host | Entity type | Qdrant collection |
|---|---|---|---|
| `gitlab_epfl_projects` | `gitlab.epfl.ch` | project | `gitlab_epfl_projects` |
| `gitlab_epfl_groups` | `gitlab.epfl.ch` | group | `gitlab_epfl_groups` |
| `gitlab_epfl_users` | `gitlab.epfl.ch` | user | `gitlab_epfl_users` |
| `gitlab_ethz_projects` | `gitlab.ethz.ch` | project | `gitlab_ethz_projects` |
| `gitlab_ethz_groups` | `gitlab.ethz.ch` | group | `gitlab_ethz_groups` |
| `gitlab_ethz_users` | `gitlab.ethz.ch` | user | `gitlab_ethz_users` |
| `gitlab_datascience_projects` | `gitlab.datascience.ch` | project | `gitlab_datascience_projects` |
| `gitlab_datascience_groups` | `gitlab.datascience.ch` | group | `gitlab_datascience_groups` |
| `gitlab_datascience_users` | `gitlab.datascience.ch` | user | `gitlab_datascience_users` |

## The shared `_gitlab_base` engine

All three instances speak the same GitLab REST v4 API, so the crawl logic
lives once in `src/index/_gitlab_base/`:

- One `GitLabClient` — wraps the REST v4 endpoints (`/projects`, `/groups`,
  `/users`), follows `X-Next-Page` pagination, and retries on `429` / `5xx`.
- Three parallel pipelines — `project_*`, `group_*`, `user_*` — each with its
  own `schema.sql`, store, ingest, embed, and retrieval modules.

A per-instance leaf (`src/index/gitlab_<instance>_<type>/`) is a thin wrapper
that binds the shared pipeline to one host + one entity type. Each leaf
exposes the standard programmatic surface:

```python
run_ingest(limit=None)   # full-instance crawl (omit limit to crawl everything)
run_embed(limit=None)    # embed un-embedded rows
search(query, top_k, candidate_k, filter_payload)
```

### Authentication for the source

Public listings on all three instances work **unauthenticated**, so no token
is required to bootstrap or crawl. Supplying a source token raises the
rate limit and can widen visibility:

| Env | Instance |
|---|---|
| `GITLAB_EPFL_TOKEN` | `gitlab.epfl.ch` |
| `GITLAB_ETHZ_TOKEN` | `gitlab.ethz.ch` |
| `GITLAB_DATASCIENCE_TOKEN` | `gitlab.datascience.ch` |

(As with every index, `RCP_TOKEN` is required for the embed + search steps.)

## Canonical id shapes

Every record's `id` is a canonical URL (`id_shape="url"`):

| Entity type | Canonical id |
|---|---|
| project | `https://<host>/<full_path>` |
| group | `https://<host>/groups/<full_path>` |
| user | `https://<host>/<username>` |

## User records — no ORCID (important caveat)

The user stores carry these fields: `username`, `name`, `bio`, `location`,
`organization`, `job_title`, `public_email`, `website_url`, `linkedin`,
`twitter`, `avatar_url`, `web_url`, `raw`.

**Unlike the GitHub users index, GitLab exposes no verified ORCID
property**, so the GitLab user stores carry **no ORCID**. Treat the GitLab
users index as a **people / contact** index, and resolve ORCID separately via
the `orcid` / `github_users` indices.

## Bootstrap, ingest, embed

### Deploy-time bootstrap (automatic)

The 9 GitLab leaf stores are auto-discovered by the federated bootstrap, so
their DuckDB files exist (with schema applied) **before the first request** —
the Gunicorn `on_starting` hook in `tools/config/gunicorn_conf.py` runs the
bootstrap once in the master process before workers fork. It is idempotent
(existing stores untouched) and best-effort (a failure is logged, never blocks
server start). Toggle with `INDEX_BOOTSTRAP_ON_START` (default `true`; set
`false`/`0`/`no`/`off` to skip — e.g. when an init-container provisions the
data dir). See the
[Bootstrap on deploy](rag-indices.md#bootstrap-on-deploy) note and the
[operations runbook](OPERATIONS_RUNBOOK.md#7-deploy-time-index-bootstrap).

Manual equivalents:

```bash
make bootstrap-index                          # all stores, idempotent, empty
python -m src.index._federated.bootstrap      # same thing
```

### CLI / programmatic ingest + embed

Each leaf exposes `run_ingest` / `run_embed` / `search`:

```python
from src.index.gitlab_epfl_users.ingest import run_ingest
from src.index.gitlab_epfl_users.embed import run_embed
from src.index.gitlab_epfl_users.retrieval import search

run_ingest(limit=50)     # smoke test: cap the crawl
run_ingest()             # full-instance crawl (no cap)
run_embed()              # embed new rows (idempotent)

hits = search("computer vision researcher", top_k=5,
              candidate_k=None, filter_payload=None)
```

## HTTP API

Every one of the 9 stores exposes the standard index ingest + search routes
under `/v2/indices/<name>/…`. All index endpoints require the same bearer
token as the rest of `/v2` (see the
[Authentication](v2-api-reference.md#authentication) section of the v2 API
reference).

### `POST /v2/indices/<name>/ingest`

Body — optional cap; omit (or send `null`) to crawl the whole public instance:

```json
{ "limit": 100 }
```

Returns `202 Accepted` with an `IndexIngestJobAccepted`:

```json
{
  "job_id": "…",
  "index_name": "gitlab_epfl_users",
  "status": "pending",
  "status_url": "/v2/indices/jobs/…",
  "submitted_at": "…"
}
```

The job runs a full-instance crawl and then embeds. Poll
`GET /v2/indices/jobs/{job_id}` for status.

### `POST /v2/indices/<name>/search`

Body is an `IndexSearchRequest`:

```json
{
  "query": "computer vision researcher",
  "top_k": 10,
  "candidate_k": null,
  "filter_payload": null,
  "target": null
}
```

Returns an `IndexSearchResponse` `{index_name, target, query, hits[]}`. These
stores are single-entity, so `target` is ignored.

### Worked example — `gitlab_epfl_users`

```bash
# Ingest (cap at 100 for a smoke test; omit "limit" for a full crawl)
curl -s -X POST https://<host>/v2/indices/gitlab_epfl_users/ingest \
     -H "Authorization: Bearer $API_TOKEN" \
     -H 'content-type: application/json' \
     -d '{"limit": 100}'
# → 202 {"job_id":"…","index_name":"gitlab_epfl_users","status":"pending",
#        "status_url":"/v2/indices/jobs/…","submitted_at":"…"}

# Poll the job
curl -s https://<host>/v2/indices/jobs/<job_id> \
     -H "Authorization: Bearer $API_TOKEN"

# Search
curl -s -X POST https://<host>/v2/indices/gitlab_epfl_users/search \
     -H "Authorization: Bearer $API_TOKEN" \
     -H 'content-type: application/json' \
     -d '{"query":"computer vision researcher","top_k":5}'
```

Swap the store name in the path for any of the other 8 (e.g.
`gitlab_ethz_projects`, `gitlab_datascience_groups`) — the contract is
identical.

## Federated layer + manifest

All 9 stores are registered in the federated layer, so they:

- appear in `GET /v2/manifest`, and
- participate in federated search / lookup — `gme-search` fans a query out
  across them alongside every other index, and `gme-entity` recognises a
  canonical GitLab URL (project / group / user) and routes it to the right
  store.

```bash
just gme-search "Swiss data science project" --top-k 10
just gme-entity https://gitlab.epfl.ch/<username>
```

See [`federated-search.md`](federated-search.md) for the federated design.
