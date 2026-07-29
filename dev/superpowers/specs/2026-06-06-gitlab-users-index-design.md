# GitLab Users Index — Design Spec

**Date:** 2026-06-06
**Branch:** `feat/gitlab-users-index`
**Status:** in build

## Goal

Add a `gitlab_<instance>_users` index family — a semantic "people" index mirroring
the existing `gitlab_<instance>_projects` / `gitlab_<instance>_groups` stores, for the
three instances already wired: `epfl` (gitlab.epfl.ch), `ethz` (gitlab.ethz.ch),
`datascience` (renkulab.io / gitlab-datascience host as configured for groups).

This was held pending a go because **GitLab exposes no verified-ORCID property** the way
GitHub's GraphQL `socialAccounts` does. It is now greenlit as a people index *without*
ORCID — it carries the identity/contact fields GitLab does expose.

## Non-goals

- No ORCID resolution (GitLab has none). The orcid-index fallback is out of scope here.
- No new ontology terms; user records surface through the federated layer as `entity_type="user"`.
- No gitlab.com curated-seed traversal (separate, still parked).

## Architecture (mirror the groups pipeline exactly)

### `src/index/_gitlab_base/` engine additions
- `client.py`: add `iter_public_users() -> Iterator[dict]` → `self._paginate("/users", {})`.
  GitLab `/users` returns the public user listing. No visibility param (unlike projects).
- `models.py`: add `GitLabUserRecord` (BaseModel), fields:
  - `user_id: str` (canonical web_url = `https://<host>/<username>`), `host: str`,
    `username: str`, `name | None`, `bio | None`, `location | None`,
    `organization | None`, `job_title | None`, `public_email | None`,
    `website_url | None`, `linkedin | None`, `twitter | None`,
    `avatar_url | None`, `web_url | None`, `raw: dict = {}`.
- `user_schema.sql`: `users` table (PK `user_id`) + shared `chunks` table
  (`entity_type='users'`, `entity_id=user_id`), mirroring `group_schema.sql` indexes.
- `user_store.py`: `GitLabUserStore` mirroring `GitLabGroupStore` — `open(db_path)`,
  `bootstrap()`, `upsert_user(record)`, `upsert_chunk(...)`, `fetch_user(user_id)`,
  `stream_rows_for_embedding(entity_type, *, limit)`, `count(table)`, `close()`.
- `user_ingest.py`: `_user_record_from_payload(host, payload)` (canonical id via
  `payload["web_url"]` or `gitlab_iri(host, "user", payload["username"])`),
  `ingest_users(*, host, client, store, limit=None) -> {"seen": N}`.
- `user_embed.py`: `embed_users(*, config, store, collection, limit=None)` — compose card
  text from `name`, `username`, `bio`, `organization`, `job_title`, `location`; skip cards
  under `min_card_chars`; same async RCP-embed → Qdrant upsert (with retry) → chunk records.
- `user_retrieval.py`: `user_semantic_search(*, config, collection, store, query,
  top_k=10, candidate_k=50, filter_payload=None)` — hydrate via `store.fetch_user()`.

### Leaf packages `src/index/gitlab_{epfl,ethz,datascience}_users/`
Thin, copied from the matching `_groups` leaf, swapping group→user and the store name /
config path / token_env. Files: `__init__.py`, `config.py`, `paths.py`, `store.py`,
`ingest.py`, `embed.py`, `retrieval.py`. Token envs reuse the groups' ones
(`GITLAB_EPFL_TOKEN`, `GITLAB_ETHZ_TOKEN`, `GITLAB_DATASCIENCE_TOKEN` — match what the
existing `_groups` leaves use).

### Config `config/index/gitlab_{epfl,ethz,datascience}_users.yaml`
Copy the matching `_groups.yaml`; set `collection: gitlab_<instance>_users`,
`min_card_chars` small (users have short cards), query_instruction mentioning GitLab users.

### Federated adapters `src/index/_federated/adapters/gitlab_{epfl,ethz,datascience}_users.py`
Copy the matching `_groups.py` adapter: `name`, `entity_types=["user"]`, `backend="vector"`,
`surface_as_source=True`, `id_shape="url"`; `search()` maps hits (title=name, url=user_id);
`lookup()` validates via `parse_gitlab_iri()` (kind must be `"user"` and host must match),
then `store.fetch_user()`. Register at module import.

### Registry `src/index/_federated/registry.py`
Append the 3 new adapter names to the `candidates` list.

### Bootstrap `src/index/_federated/bootstrap.py`
- **Bug fix:** the gitlab `_groups` stores use the `open_store()` leaf convention but are
  missing from `_LEAF_STORES`, so they silently bootstrap as "skipped: no duckdb store".
  Add the 3 groups stores AND the 3 new users stores to `_LEAF_STORES`.

## Tests `tests/index/gitlab_{epfl,ethz,datascience}_users/`
Mirror the `_groups` tests: `test_adapter_and_manifest.py` (manifest has the store,
`backend="vector"`, `entity_types=["user"]`, `surface_as_source=True`) and `test_paths.py`
(duckdb path layout + config host/collection). At least one ingest+embed round-trip test
with a faked `GitLabClient` (mirror the existing groups ingest/round-trip test if present).
Also add a bootstrap test asserting the gitlab groups+users leaves are no longer skipped.

## Gate
Full `tests/v2/` plus `tests/index/` must be green. No real network/LLM in tests
(inject `transport` / monkeypatch RCP+Qdrant as the existing gitlab tests do).
