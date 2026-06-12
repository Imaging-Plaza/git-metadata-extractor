# Bugs 09–11 — GitLab index fixes (embed card, require_rcp, public users)
**Severity:** medium (blocks GitLab indexing) · **Status:** Investigated — plan ready (no code changed) · **Area:** GitLab index

## Overview & dependencies

Three bugs sit in the shared GitLab index engine under `src/index/_gitlab_base/`, which
backs all nine leaf stores (`gitlab_{epfl,ethz,datascience}_{projects,groups,users}` — see
`src/v2/indices/gitlab.py:31-35`). All nine leaves alias the same `GitLabIndexConfig`
(`src/index/_gitlab_base/config_base.py:56`) and call the same embed/ingest helpers, so a
single fix in `_gitlab_base` propagates to every store.

**None of the three hot-patches are present in the working tree.** All three bugs are still
live in `_gitlab_base`. (The task framing said #9 and #10 were "hot-patched by operators",
but the patches are not in this checkout — they must be re-applied here as the proper fix.)

Dependency ordering:

1. **#10 is a hard prerequisite for #9 and for any embed work.** `RCPEmbeddingClient.__init__`
   unconditionally calls `config.require_rcp()` (`src/index/_rcp/embed_client.py:83`). Every
   GitLab embed path (`project_embed.py:99`, `group_embed.py:80`, `user_embed.py:87`)
   constructs `RCPEmbeddingClient(config)` with a `GitLabIndexConfig` that has no
   `require_rcp()` method → `AttributeError` on the **first line of every embed run**, before
   any card is ever built. So #9's effect (thin cards skipped) cannot even be observed until
   #10 is fixed. Fix #10 first.
2. **#9 and #11 are independent** of each other; both can land after #10.
3. **#11 (`iter_public_users`) blocks only the three `*_users` leaves** at *ingest* time (the
   admin `/users` endpoint 403s anonymously). The `*_projects` and `*_groups` leaves are
   unaffected by #11.

A single shared test plan covers all three because they share the engine (see Combined
verification).

---

## Bug 09 — thin embed card

### Current behaviour (file:line)

`src/index/_gitlab_base/project_embed.py:57-73`, `_row_to_chunks`:

```python
parts: list[str] = [str(row["project_id"])]          # line 64 — project_id only
if row.get("description"):
    parts.append(str(row["description"]))             # 65-66 — description (often empty)
topics = _row_topics(row)
if topics:
    parts.append("topics: " + ", ".join(topics))      # 67-69 — topics (often empty)
text = "\n\n".join(parts)
if len(text) < min_card_chars:                         # 71 — skip if too short
    return []
```

The card is `project_id + description + topics` only. `project_id` is the canonical web_url
(e.g. `https://gitlab.epfl.ch/group/sub/myproj`), which is usually 40–80 chars on its own,
but `min_card_chars` is **64** (`src/index/_gitlab_base/config_base.py:52`,
`GitLabFetchConfig.min_card_chars: int = 64`). A public project with **no description and no
topics** (very common for public mirrors / thin repos) and a short namespace+slug can fall
under 64 chars → `_row_to_chunks` returns `[]` → the row is counted in `rows_skipped`
(`project_embed.py:181`) and silently never embedded. It is then invisible to retrieval.

Note the `name` and `full_path` columns already exist on the row: the store schema defines
both (`src/index/_gitlab_base/project_schema.sql:4-5`), `stream_rows_for_embedding` does
`SELECT t.*` (`project_store.py:158`), and `_row_to_payload` already reads
`row.get("full_path")` / `row.get("name")` (`project_embed.py:82,86`) — they are simply not
fed into the **card text**.

### Fix

Add `name` and `full_path` to the card parts in `_row_to_chunks`
(`src/index/_gitlab_base/project_embed.py:64`), placed before `description`:

```python
def _row_to_chunks(row, *, chunk_tokens, overlap, min_card_chars):
    parts: list[str] = [str(row["project_id"])]
    if row.get("name"):
        parts.append(str(row["name"]))
    if row.get("full_path"):
        parts.append(str(row["full_path"]))
    if row.get("description"):
        parts.append(str(row["description"]))
    topics = _row_topics(row)
    if topics:
        parts.append("topics: " + ", ".join(topics))
    text = "\n\n".join(parts)
    if len(text) < min_card_chars:
        return []
    return chunk_text(text, chunk_tokens=chunk_tokens, overlap=overlap)
```

Both fields are non-empty for every ingested project (`full_path` is `NOT NULL` in the schema;
`name` is set from the GitLab payload). Adding `name` (repo display name) + `full_path`
(`path_with_namespace`, joined with `"\n\n"`) reliably adds dozens of chars, pushing
description-less public projects over the 64-char threshold so they are no longer skipped.
This makes the card semantically richer too (name/path are good retrieval signal).

Only `project_embed.py` needs editing. `group_embed.py` already includes name/full_path-style
fields, and `user_embed.py:51-63` already includes name/username/bio/etc., so they don't have
the same starvation.

**Re-skip / re-embed note:** This changes which rows produce chunks. Projects already skipped
on a prior run will only get embedded on a *re-run* of `run_embed`, because
`stream_rows_for_embedding` (`project_store.py:147-172`) only yields rows with **no existing
chunks**. Previously-skipped projects have no chunks, so they will be picked up automatically
on the next embed pass — no backfill/migration needed, just re-run embed for the affected
stores. (Rows that were embedded before with the thin card keep their old chunks and will NOT
be re-embedded unless their chunks are cleared — acceptable, since the thin-card rows are
exactly the ones that were skipped, not the ones already embedded.)

### Test

- Unit: call `_row_to_chunks` with a row `{"project_id": "https://gitlab.epfl.ch/g/p",
  "name": "p", "full_path": "g/p", "description": None, "topics": None}` and
  `min_card_chars=64`. Assert it returns non-empty chunks (today it returns `[]`). Assert the
  emitted chunk text contains the name and full_path.
- Regression: a row whose `project_id` alone already exceeds 64 still embeds (unchanged).

---

## Bug 10 — missing require_rcp()

### Current behaviour (file:line)

`GitLabIndexConfig` (`src/index/_gitlab_base/config_base.py:56-63`) defines `rcp`, `qdrant`,
`chunking`, `gitlab`, `paths` — but **no `require_rcp()` method**. There is no `require_rcp`
anywhere in `src/index/_gitlab_base/` (grep returns zero defs and zero calls).

The shared embedding client requires it: `RCPEmbeddingClient.__init__` calls
`config.require_rcp()` unconditionally (`src/index/_rcp/embed_client.py:83`), and its
`RCPConfigProtocol` documents `require_rcp(self) -> None` as part of the mandatory contract
(`embed_client.py:35-44`). Every GitLab embed entrypoint constructs this client with a
`GitLabIndexConfig`:

- `project_embed.py:99` — `client = RCPEmbeddingClient(config)`
- `group_embed.py:80` — same
- `user_embed.py:87` — same

So the very first thing every `run_embed` does is hit `config.require_rcp()` on an object that
lacks the method → `AttributeError: 'GitLabIndexConfig' object has no attribute 'require_rcp'`
→ all GitLab embeds fail. (In the v2 ingest job, `run_embed` is awaited at
`src/v2/indices/gitlab.py:78`, so the job is marked FAILED.)

Sibling configs all provide the method with an identical contract:

- `src/index/github_repos/config.py:81-83`
- `src/index/dockerhub/config.py:70-72`
- also `epfl_graph`, `huggingface_papers`, `oamonitor`, `openalex`, `orcid`

All share the body:

```python
def require_rcp(self) -> None:
    if not self.rcp.token:
        raise ValueError(MISSING_RCP_TOKEN_ERROR)
```

with `MISSING_RCP_TOKEN_ERROR = "Missing required environment variable: RCP_TOKEN"`
(`github_repos/config.py:29`, `dockerhub/config.py:26`).

### Fix

Mirror the sibling contract in `src/index/_gitlab_base/config_base.py`. Add the error constant
near the top of the module (after the imports, ~line 20):

```python
MISSING_RCP_TOKEN_ERROR = "Missing required environment variable: RCP_TOKEN"
```

and add the method to `GitLabIndexConfig` (after `model_config`, ~line 63):

```python
class GitLabIndexConfig(BaseModel):
    rcp: RcpConfig
    qdrant: QdrantConfig
    chunking: ChunkingConfig
    gitlab: GitLabFetchConfig
    paths: GitLabIndexPathsBase

    model_config = {"arbitrary_types_allowed": True}

    def require_rcp(self) -> None:
        if not self.rcp.token:
            raise ValueError(MISSING_RCP_TOKEN_ERROR)
```

This is the minimal proper fix: it resolves the `AttributeError` for all nine leaves at once
(they all alias `GitLabIndexConfig`), and it correctly turns a missing `RCP_TOKEN` into a clear
`ValueError` instead of a confusing attribute error. No changes to the embed modules are needed
— they already (correctly) rely on `RCPEmbeddingClient` to enforce the token.

Optional hardening (not required for the fix): the v2 GitLab ingest job could catch the
resulting `ValueError` and surface a 503/clear job error the way `github_repos/api.py:63-67`
does for its search route — but that is a separate quality improvement, not part of unblocking.

### Test

- Unit: `cfg.require_rcp()` raises `ValueError` containing `RCP_TOKEN` when `rcp.token` is
  falsy; returns `None` when set. (Mirror `tests/index/openalex/test_config.py`.)
- Integration: constructing `RCPEmbeddingClient(gitlab_config)` no longer raises
  `AttributeError`; with a token set it builds, with no token it raises the `ValueError`.
- Smoke: `run_embed` for one `*_projects` leaf gets past client construction (today it
  `AttributeError`s immediately).

---

## Bug 11 — iter_public_users admin-only

### Current behaviour (file:line)

`src/index/_gitlab_base/client.py:70-71`:

```python
def iter_public_users(self) -> Iterator[dict[str, Any]]:
    yield from self._paginate("/users", {})
```

The GitLab `GET /users` listing endpoint returns the full user directory only to
administrators; for an anonymous or non-admin token it returns **403** (or a heavily filtered
set). The client is built per-host with an optional token (`client.py:22-41`) and the GitLab
indices crawl public data, so in practice this 403s and **`iter_public_users` yields nothing /
errors** → `ingest_users` (`src/index/_gitlab_base/user_ingest.py:38-46`) seeds zero users for
all three `*_users` leaves.

Users must instead be derived from **public projects' owners and members**, which anonymous
callers *can* read.

### Fix

Replace the admin `/users` crawl with a derivation from public projects. Public, anonymous-safe
endpoints:

- `GET /projects?visibility=public` — already used by `iter_public_projects`
  (`client.py:64-65`); each project payload carries an `owner` object (for user-namespace
  projects) and a `namespace` object.
- `GET /projects/:id/members/all?per_page=…` — the project members listing, readable for public
  projects, returns the user objects (id, username, name, web_url, …) for direct + inherited
  members. (Plain `/members` for direct-only is also acceptable; `/members/all` gives better
  coverage.)

Rewrite `iter_public_users` to fan out over public projects and de-duplicate users by id /
web_url. Add a small members helper to the client:

```python
def iter_project_members(self, project_id: int | str) -> Iterator[dict[str, Any]]:
    # public for public projects; anonymous-safe (no admin scope needed)
    yield from self._paginate(f"/projects/{project_id}/members/all", {})

def iter_public_users(self) -> Iterator[dict[str, Any]]:
    seen: set[str] = set()
    for project in self.iter_public_projects():
        # 1) project owner (present for user-namespace projects)
        owner = project.get("owner")
        if owner:
            key = str(owner.get("id") or owner.get("username") or "")
            if key and key not in seen:
                seen.add(key)
                yield owner
        # 2) project members (direct + inherited)
        pid = project.get("id")
        if pid is None:
            continue
        for member in self.iter_project_members(pid):
            key = str(member.get("id") or member.get("username") or "")
            if key and key not in seen:
                seen.add(key)
                yield member
```

Notes:
- This keeps the existing `ingest_users`/`_user_record_from_payload` contract intact — the
  yielded dicts still expose `username`, `name`, `web_url`, etc.
  (`user_ingest.py:14-35`). `bio`/`organization`/`job_title`/`public_email` may be absent in
  member payloads (member objects are lighter than full user objects); `_user_record_from_payload`
  already uses `.get(...)` with `None` fallbacks, so missing fields degrade gracefully.
- De-dup is by `id` (falling back to `username`) to avoid re-emitting the same user across many
  projects.
- The `limit` parameter in `ingest_users` (`user_ingest.py:44-45`) still applies and now bounds
  the number of *derived* users — useful because the project fan-out can be large.

If richer per-user fields are needed later, a second pass could fetch `GET /users/:id` (the
single-user read, which **is** public) for each discovered id — but that is an enhancement, not
required to unblock ingest.

### Test

- Unit with an injected `transport` (the client already supports `transport=` for tests,
  `client.py:31`): stub `/projects?visibility=public` to return two projects (one with an
  `owner`, one without) and stub `/projects/:id/members/all` per project. Assert
  `iter_public_users` yields the union of owners + members with no duplicates, and never
  requests `/users` (admin endpoint must not be called).
- Regression: assert a 403 from `/users` can no longer break ingest (the endpoint is no longer
  hit).
- End-to-end: `ingest_users` over the stubbed client seeds the expected user rows.

---

## Combined risks & verification

Risks:
- **#9 re-embed scope:** description-less projects skipped on earlier runs only get embedded on
  the next `run_embed` (chunk-existence gate, `project_store.py:158-162`). No backfill needed,
  but operators should re-run embed for the affected `*_projects` stores to pick them up.
- **#11 payload shape:** member objects are lighter than full `/users` objects; some user
  metadata fields will be `None`. Acceptable (the record builder tolerates it), but cards for
  derived users may be thinner — watch the user `min_card_chars=64` skip rate.
- **#11 cost:** project fan-out + per-project members listing is more HTTP traffic than one
  `/users` crawl; the client's bounded retry/pagination (`client.py:43-62`) handles it, and
  `limit` bounds it.
- **Shared engine blast radius:** all edits are in `_gitlab_base`, affecting all nine leaves
  uniformly — a regression hits everything, so the test suite below must pass for at least one
  projects + one users leaf.

Verification sequence (after applying #10, then #9 and #11):
1. `uv run pytest tests/v2/test_indices_gitlab_endpoints.py tests/v2/test_gitlab_canonicalization.py`
   — route/search/auth/canonicalization regression for the family.
2. New unit tests above (`_row_to_chunks`, `require_rcp`, `iter_public_users`).
3. Targeted smoke: `run_ingest(limit=…)` + `run_embed()` against one `*_projects` leaf and one
   `*_users` leaf with `RCP_TOKEN` set and an injected/mock transport — confirm no
   `AttributeError` (#10), description-less projects embed (#9), users are seeded from project
   members (#11).
4. `uv run ruff check src/index/_gitlab_base/` for lint parity.

## Effort estimate

- #10: ~10 min (constant + 3-line method, copy from sibling) + ~10 min test. **Smallest, do first.**
- #9: ~10 min (two `if`/append lines) + ~15 min test. Plus operator re-run of embed.
- #11: ~30–45 min (new `iter_project_members` + rewritten `iter_public_users` + de-dup) +
  ~30 min transport-stub test (largest of the three).
- Combined: **~2–2.5 hours** including tests, single shared engine, single PR.

## Open questions

- **Why does the bug report say #9/#10 were already hot-patched?** The patches are **not** in
  this checkout. Confirm whether operators patched a deployed image / different branch and these
  need re-landing on `main`, or whether the hot-patch was lost. (Surprise finding — flag to
  reporter.)
- **#10 contract source:** should the error constant live in `_gitlab_base/config_base.py`
  (proposed) or be imported from a shared module? Siblings each define their own copy, so a
  local constant matches house style — confirm no central `MISSING_RCP_TOKEN_ERROR` is expected.
- **#11 `/members/all` vs `/members`:** `/members/all` includes inherited (group-level) members
  and gives better coverage but more results; `/members` is direct-only. Confirm desired scope.
  Also confirm whether group-namespace projects (no `owner` field) should additionally derive
  users from group members (`GET /groups/:id/members`) — out of scope here but a natural follow-up.
- **#11 token:** does the GitLab token used for these crawls have any elevated scope that made
  `/users` partially work in some envs? If so, document that the new path is strictly better
  (works anonymously) and the token requirement drops.
