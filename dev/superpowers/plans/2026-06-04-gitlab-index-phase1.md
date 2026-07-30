# GitLab Index — Phase 1 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the GitLab-index foundation end-to-end: a canonical-URL helper, a shared `_gitlab_base` engine (REST client + parameterized project store/ingest/embed/retrieval), and the first concrete vector-backed store `gitlab_epfl_projects` — proving the pattern the remaining 11 stores will reuse.

**Architecture:** Mirror `github_repos` (a vector-backed single-entity DuckDB+Qdrant store). The per-instance/per-type logic lives once in `src/index/_gitlab_base/` (parameterized by host + collection + duckdb path), and each `gitlab_<instance>_<type>` package is a thin leaf supplying config + a federated adapter. IDs are the GitLab API's `web_url` (canonical landing page). Reuse the openalex RCP embed/rerank + Qdrant clients exactly as `github_repos` does.

**Tech Stack:** Python 3.12, DuckDB, Qdrant, httpx, pydantic v2, pytest. Spec: `docs/superpowers/specs/2026-06-04-gitlab-index-design.md`.

---

## File Structure (Phase 1)

Create:
- `git_metadata_extractor/canonicalization/gitlab.py` — `gitlab_iri()` / `parse_gitlab_iri()`.
- `src/index/_gitlab_base/__init__.py`
- `src/index/_gitlab_base/client.py` — `GitLabClient` (per-host REST, token, pagination, rate-limit).
- `src/index/_gitlab_base/models.py` — `GitLabProjectRecord`.
- `src/index/_gitlab_base/project_schema.sql` — `projects` + `chunks` tables.
- `src/index/_gitlab_base/project_store.py` — `GitLabProjectStore` (parameterized by duckdb path).
- `src/index/_gitlab_base/project_ingest.py` — `_project_record_from_payload()`, `ingest_projects()`.
- `src/index/_gitlab_base/project_embed.py` — `embed_projects()` (chunk→RCP→Qdrant).
- `src/index/_gitlab_base/project_retrieval.py` — `project_semantic_search()`.
- `src/index/_gitlab_base/config_base.py` — `GitLabIndexConfig` + `load_gitlab_config()`.
- `src/index/_gitlab_base/paths_base.py` — `resolve_gitlab_paths()`.
- `src/index/gitlab_epfl_projects/{__init__,paths,config,store,ingest,embed,retrieval}.py`
- `src/index/gitlab_epfl_projects/storage/schema.sql` (copy of project_schema.sql for bootstrap)
- `config/index/gitlab_epfl_projects.yaml`
- `src/index/_federated/adapters/gitlab_epfl_projects.py`
- Tests under `tests/v2/test_gitlab_canonicalization.py`, `tests/index/_gitlab_base/`, `tests/index/gitlab_epfl_projects/`.

Modify:
- `src/index/_federated/registry.py:87-94` — add `"gitlab_epfl_projects"` to the `candidates` list.

---

## Task 1: Canonical-URL helper

**Files:**
- Create: `git_metadata_extractor/canonicalization/gitlab.py`
- Test: `tests/v2/test_gitlab_canonicalization.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/v2/test_gitlab_canonicalization.py
"""Tests for the GitLab canonical-URL helpers."""
from __future__ import annotations

import pytest

from git_metadata_extractor.canonicalization.gitlab import gitlab_iri, parse_gitlab_iri

HOST = "gitlab.epfl.ch"


def test_iri_project_from_path():
    assert gitlab_iri(HOST, "project", "group/sub/proj") == "https://gitlab.epfl.ch/group/sub/proj"


def test_iri_group_uses_groups_segment():
    assert gitlab_iri(HOST, "group", "group/sub") == "https://gitlab.epfl.ch/groups/group/sub"


def test_iri_user():
    assert gitlab_iri(HOST, "user", "alice") == "https://gitlab.epfl.ch/alice"


def test_iri_idempotent_on_url_and_trailing_slash():
    url = "https://gitlab.epfl.ch/group/proj"
    assert gitlab_iri(HOST, "project", url) == url
    assert gitlab_iri(HOST, "project", url + "/") == url
    assert gitlab_iri(HOST, "project", "  group/proj  ") == url


@pytest.mark.parametrize("bad", [None, "", "   ", 42, "https://example.com/x"])
def test_iri_rejects_garbage(bad):
    assert gitlab_iri(HOST, "project", bad) is None


def test_iri_rejects_unknown_kind():
    with pytest.raises(ValueError, match="kind"):
        gitlab_iri(HOST, "spaceship", "x")


def test_parse_round_trips():
    assert parse_gitlab_iri("https://gitlab.epfl.ch/group/proj") == ("gitlab.epfl.ch", "project", "group/proj")
    assert parse_gitlab_iri("https://gitlab.epfl.ch/groups/group/sub") == ("gitlab.epfl.ch", "group", "group/sub")
    assert parse_gitlab_iri("https://gitlab.epfl.ch/alice") == ("gitlab.epfl.ch", "user", "alice")


def test_parse_returns_none_on_non_gitlab():
    assert parse_gitlab_iri(None) is None
    assert parse_gitlab_iri("group/proj") is None  # bare path is not a URL
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/v2/test_gitlab_canonicalization.py -q`
Expected: FAIL with `ModuleNotFoundError: git_metadata_extractor.canonicalization.gitlab`.

- [ ] **Step 3: Write the implementation**

```python
# git_metadata_extractor/canonicalization/gitlab.py
"""Canonical GitLab URL helpers for index ids.

GitLab's canonical landing-page URL is the id (v3.0.0), with the instance host
encoded in it so ids are globally unique across per-instance stores. The
GitLab API's ``web_url`` is authoritative; ``gitlab_iri`` is the builder used
when only a host + path is available.

  project -> https://<host>/<full_path>
  group   -> https://<host>/groups/<full_path>
  user    -> https://<host>/<username>
"""
from __future__ import annotations

# kind -> path prefix after the host (groups live under /groups/, others don't)
_PREFIX = {"project": "", "group": "groups/", "user": ""}


def gitlab_iri(host: str, kind: str, value: str | None) -> str | None:
    """Canonical GitLab URL for a bare path (or an already-canonical URL).

    Returns None on empty/invalid input. Raises ValueError on unknown ``kind``.
    Idempotent: a value already under ``https://<host>/`` is returned unchanged
    (trailing slash trimmed); an ``http://`` form is upgraded to ``https``.
    """
    if kind not in _PREFIX:
        msg = f"Unknown gitlab kind: {kind!r}"
        raise ValueError(msg)
    if not isinstance(value, str) or not value.strip():
        return None
    base = f"https://{host}/"
    s = value.strip()
    if s.lower().startswith(base):
        return s.rstrip("/")
    if s.lower().startswith(f"http://{host}/"):
        return ("https://" + s.split("://", 1)[1]).rstrip("/")
    if "://" in s:
        return None  # a URL on some other host
    return f"{base}{_PREFIX[kind]}{s.strip('/')}"


def parse_gitlab_iri(iri: str | None) -> tuple[str, str, str] | None:
    """Inverse: a canonical GitLab URL -> (host, kind, path). None otherwise."""
    if not isinstance(iri, str) or "://" not in iri:
        return None
    rest = iri.strip().rstrip("/").split("://", 1)[1]
    if "/" not in rest:
        return None
    host, path = rest.split("/", 1)
    if path.startswith("groups/"):
        return host, "group", path[len("groups/"):]
    kind = "user" if "/" not in path else "project"
    return host, kind, path


__all__ = ["gitlab_iri", "parse_gitlab_iri"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/v2/test_gitlab_canonicalization.py -q`
Expected: PASS (10 tests).

- [ ] **Step 5: Commit**

```bash
git add git_metadata_extractor/canonicalization/gitlab.py tests/v2/test_gitlab_canonicalization.py
git commit -m "feat(canon): gitlab_iri/parse_gitlab_iri canonical-URL helpers"
```

---

## Task 2: GitLab REST client

**Files:**
- Create: `src/index/_gitlab_base/__init__.py` (empty), `src/index/_gitlab_base/client.py`
- Test: `tests/index/_gitlab_base/test_client.py` (+ `tests/index/_gitlab_base/__init__.py`)

GitLab REST v4: list public projects at `GET https://<host>/api/v4/projects?visibility=public&per_page=100&page=N` (keyset via `X-Next-Page` header). Each project payload already carries `web_url`, `path_with_namespace`, `description`, `topics`, `star_count`, `forks_count`, `visibility`, `forked_from_project`, `namespace`, `last_activity_at`, `created_at`, `default_branch`.

- [ ] **Step 1: Write the failing test** (fake transport, no network)

```python
# tests/index/_gitlab_base/test_client.py
from __future__ import annotations

import httpx

from src.index._gitlab_base.client import GitLabClient


def _transport(pages: dict[int, list[dict]]):
    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        body = pages.get(page, [])
        nxt = str(page + 1) if (page + 1) in pages else ""
        return httpx.Response(200, json=body, headers={"X-Next-Page": nxt})
    return httpx.MockTransport(handler)


def test_iter_public_projects_paginates():
    pages = {1: [{"id": 1, "web_url": "https://gl/a"}], 2: [{"id": 2, "web_url": "https://gl/b"}]}
    client = GitLabClient(host="gitlab.epfl.ch", token=None, transport=_transport(pages))
    got = list(client.iter_public_projects())
    assert [p["id"] for p in got] == [1, 2]


def test_sends_token_header_when_present():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("PRIVATE-TOKEN")
        return httpx.Response(200, json=[], headers={"X-Next-Page": ""})

    client = GitLabClient(host="gitlab.epfl.ch", token="abc", transport=httpx.MockTransport(handler))
    list(client.iter_public_projects())
    assert seen["auth"] == "abc"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/index/_gitlab_base/test_client.py -q`
Expected: FAIL (`ModuleNotFoundError`).

- [ ] **Step 3: Write the implementation**

```python
# src/index/_gitlab_base/client.py
"""Thin GitLab REST v4 client: per-host base URL, optional token, page
pagination via the X-Next-Page header, and bounded retry on 429/5xx.

`transport` is injectable for tests. One instance per GitLab host.
"""
from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from typing import Any

import httpx

LOGGER = logging.getLogger(__name__)
_RETRY_STATUS = {429, 500, 502, 503, 504}


class GitLabClient:
    def __init__(
        self,
        *,
        host: str,
        token: str | None = None,
        per_page: int = 100,
        timeout: float = 30.0,
        max_retries: int = 4,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._base = f"https://{host}/api/v4"
        self._per_page = per_page
        self._max_retries = max_retries
        headers = {"Accept": "application/json"}
        if token:
            headers["PRIVATE-TOKEN"] = token
        self._client = httpx.Client(
            headers=headers, timeout=timeout, transport=transport,
        )

    def _get(self, path: str, params: dict[str, Any]) -> httpx.Response:
        url = f"{self._base}{path}"
        for attempt in range(self._max_retries + 1):
            resp = self._client.get(url, params=params)
            if resp.status_code in _RETRY_STATUS and attempt < self._max_retries:
                wait = float(resp.headers.get("Retry-After") or (2 ** attempt))
                LOGGER.warning("gitlab %s -> %s; retry in %ss", url, resp.status_code, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp
        return resp  # pragma: no cover

    def _paginate(self, path: str, params: dict[str, Any]) -> Iterator[dict[str, Any]]:
        page = 1
        while page:
            resp = self._get(path, {**params, "per_page": self._per_page, "page": page})
            for item in resp.json():
                yield item
            nxt = resp.headers.get("X-Next-Page", "").strip()
            page = int(nxt) if nxt else 0

    def iter_public_projects(self) -> Iterator[dict[str, Any]]:
        yield from self._paginate("/projects", {"visibility": "public", "archived": "false"})

    def close(self) -> None:
        self._client.close()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/index/_gitlab_base/test_client.py -q`
Expected: PASS (2 tests).

- [ ] **Step 5: Commit**

```bash
git add src/index/_gitlab_base/__init__.py src/index/_gitlab_base/client.py tests/index/_gitlab_base/
git commit -m "feat(gitlab): _gitlab_base REST client (paginated, token, retry)"
```

---

## Task 3: Project record + DuckDB store + schema

**Files:**
- Create: `src/index/_gitlab_base/models.py`, `src/index/_gitlab_base/project_schema.sql`, `src/index/_gitlab_base/project_store.py`
- Test: `tests/index/_gitlab_base/test_project_store.py`

`project_schema.sql` — mirror `github_repos/storage/schema.sql` (digest §4) with GitLab fields; PK `project_id` (the `web_url`):

```sql
CREATE TABLE IF NOT EXISTS projects (
    project_id        TEXT PRIMARY KEY,   -- canonical web_url
    host              TEXT NOT NULL,      -- e.g. gitlab.epfl.ch
    full_path         TEXT NOT NULL,      -- path_with_namespace
    name              TEXT,
    description       TEXT,
    visibility        TEXT,
    is_fork           BOOLEAN,
    forked_from       TEXT,               -- parent project web_url or NULL
    namespace         TEXT,
    topics            JSON,
    star_count        BIGINT,
    forks_count       BIGINT,
    default_branch    TEXT,
    last_activity_at  TIMESTAMP,
    created_at        TIMESTAMP,
    raw               JSON,
    ingested_at       TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE TABLE IF NOT EXISTS chunks (
    chunk_id     TEXT PRIMARY KEY,
    entity_type  TEXT NOT NULL,
    entity_id    TEXT NOT NULL,
    chunk_index  INTEGER NOT NULL,
    text         TEXT NOT NULL,
    token_count  INTEGER NOT NULL,
    vector_id    TEXT NOT NULL,
    embedded_at  TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_gl_projects_host ON projects (host);
CREATE INDEX IF NOT EXISTS idx_gl_chunks_entity ON chunks (entity_type, entity_id);
```

`models.py`:

```python
# src/index/_gitlab_base/models.py
from __future__ import annotations
from datetime import datetime
from typing import Any
from pydantic import BaseModel


class GitLabProjectRecord(BaseModel):
    project_id: str            # canonical web_url
    host: str
    full_path: str
    name: str | None = None
    description: str | None = None
    visibility: str | None = None
    is_fork: bool = False
    forked_from: str | None = None
    namespace: str | None = None
    topics: list[str] = []
    star_count: int = 0
    forks_count: int = 0
    default_branch: str | None = None
    last_activity_at: datetime | None = None
    created_at: datetime | None = None
    raw: dict[str, Any] = {}
```

`project_store.py` — mirror `GitHubReposStore` (digest §5), but constructed with an explicit `db_path` (each instance leaf passes its own). `ENTITY_TYPE = "projects"`, `ID_COLUMN = "project_id"`. Methods: `open(db_path)`, `bootstrap()` (loads `project_schema.sql`), `connect()`, `close()`, `upsert_project(record)` (INSERT … ON CONFLICT (project_id) DO UPDATE, json.dumps topics/raw), `upsert_chunk(...)` (verbatim from digest §5), `count(table)`, `fetch_project(project_id)` (SELECT * WHERE project_id = ?), `stream_rows_for_embedding("projects", limit)` (NOT EXISTS chunks join on `project_id`). The PK is already the canonical URL, so `upsert_project` stores `record.project_id` as-is (it is the `web_url`).

- [ ] **Step 1: Write the failing test**

```python
# tests/index/_gitlab_base/test_project_store.py
from __future__ import annotations
from pathlib import Path
import pytest
from src.index._gitlab_base.models import GitLabProjectRecord
from src.index._gitlab_base.project_store import GitLabProjectStore

_URL = "https://gitlab.epfl.ch/group/proj"


@pytest.fixture()
def store(tmp_path: Path):
    s = GitLabProjectStore.open(tmp_path / "gitlab_epfl_projects.duckdb")
    yield s
    s.close()


def test_upsert_round_trip_keeps_url_id(store):
    store.upsert_project(GitLabProjectRecord(
        project_id=_URL, host="gitlab.epfl.ch", full_path="group/proj",
        name="proj", topics=["ml"], star_count=3,
    ))
    row = store.fetch_project(_URL)
    assert row["project_id"] == _URL
    assert row["full_path"] == "group/proj"
    assert store.count("projects") == 1


def test_stream_skips_embedded(store):
    store.upsert_project(GitLabProjectRecord(project_id=_URL, host="h", full_path="group/proj"))
    assert len(list(store.stream_rows_for_embedding("projects"))) == 1
    store.upsert_chunk(chunk_id="c1", entity_type="projects", entity_id=_URL,
                       chunk_index=0, text="x", token_count=1, vector_id="c1")
    assert list(store.stream_rows_for_embedding("projects")) == []
```

- [ ] **Step 2: Run** `python -m pytest tests/index/_gitlab_base/test_project_store.py -q` → FAIL.
- [ ] **Step 3: Implement** `models.py`, `project_schema.sql`, `project_store.py` per the shapes above (copy `GitHubReposStore` method bodies from `src/index/github_repos/storage/duckdb_store.py`, renaming repo→project, repo_id→project_id, and reading `project_schema.sql`).
- [ ] **Step 4: Run** the test → PASS (2 tests).
- [ ] **Step 5: Commit** `feat(gitlab): _gitlab_base project record + DuckDB store + schema`.

---

## Task 4: Project ingest (API payload → record)

**Files:**
- Create: `src/index/_gitlab_base/project_ingest.py`
- Test: `tests/index/_gitlab_base/test_project_ingest.py`

`_project_record_from_payload(host, payload)` maps a GitLab `/projects` item to `GitLabProjectRecord`, using `web_url` as `project_id` (fallback to `gitlab_iri(host, "project", path_with_namespace)`).

- [ ] **Step 1: Write the failing test**

```python
# tests/index/_gitlab_base/test_project_ingest.py
from __future__ import annotations
from src.index._gitlab_base.project_ingest import _project_record_from_payload

_PAYLOAD = {
    "web_url": "https://gitlab.epfl.ch/grp/proj",
    "path_with_namespace": "grp/proj",
    "name": "proj",
    "description": "a project",
    "visibility": "public",
    "topics": ["ml", "rust"],
    "star_count": 5,
    "forks_count": 1,
    "default_branch": "main",
    "namespace": {"full_path": "grp"},
    "forked_from_project": {"web_url": "https://gitlab.epfl.ch/up/stream"},
}


def test_maps_payload_with_url_id():
    rec = _project_record_from_payload("gitlab.epfl.ch", _PAYLOAD)
    assert rec.project_id == "https://gitlab.epfl.ch/grp/proj"
    assert rec.full_path == "grp/proj"
    assert rec.topics == ["ml", "rust"]
    assert rec.is_fork is True
    assert rec.forked_from == "https://gitlab.epfl.ch/up/stream"
    assert rec.namespace == "grp"


def test_falls_back_to_iri_when_web_url_missing():
    rec = _project_record_from_payload("gitlab.epfl.ch", {"path_with_namespace": "a/b"})
    assert rec.project_id == "https://gitlab.epfl.ch/a/b"
```

- [ ] **Step 2: Run** → FAIL.
- [ ] **Step 3: Implement**

```python
# src/index/_gitlab_base/project_ingest.py
from __future__ import annotations
from typing import Any, Iterator
from git_metadata_extractor.canonicalization.gitlab import gitlab_iri
from src.index._gitlab_base.client import GitLabClient
from src.index._gitlab_base.models import GitLabProjectRecord
from src.index._gitlab_base.project_store import GitLabProjectStore


def _project_record_from_payload(host: str, payload: dict[str, Any]) -> GitLabProjectRecord:
    full_path = payload.get("path_with_namespace") or ""
    web_url = payload.get("web_url") or gitlab_iri(host, "project", full_path)
    fork_parent = payload.get("forked_from_project") or None
    ns = payload.get("namespace") or {}
    return GitLabProjectRecord(
        project_id=web_url,
        host=host,
        full_path=full_path,
        name=payload.get("name"),
        description=payload.get("description"),
        visibility=payload.get("visibility"),
        is_fork=fork_parent is not None,
        forked_from=(fork_parent or {}).get("web_url") if isinstance(fork_parent, dict) else None,
        namespace=ns.get("full_path") if isinstance(ns, dict) else None,
        topics=list(payload.get("topics") or []),
        star_count=int(payload.get("star_count") or 0),
        forks_count=int(payload.get("forks_count") or 0),
        default_branch=payload.get("default_branch"),
        last_activity_at=payload.get("last_activity_at"),
        created_at=payload.get("created_at"),
        raw=payload,
    )


def ingest_projects(*, host: str, client: GitLabClient, store: GitLabProjectStore,
                    limit: int | None = None) -> dict[str, int]:
    seen = 0
    for payload in client.iter_public_projects():
        store.upsert_project(_project_record_from_payload(host, payload))
        seen += 1
        if limit and seen >= limit:
            break
    return {"seen": seen}
```

- [ ] **Step 4: Run** → PASS (2 tests).
- [ ] **Step 5: Commit** `feat(gitlab): project ingest payload→record + ingest_projects`.

---

## Task 5: Embed + retrieval (reuse openalex RCP/Qdrant clients)

**Files:**
- Create: `src/index/_gitlab_base/project_embed.py`, `src/index/_gitlab_base/project_retrieval.py`

Mirror `github_repos/embed/pipeline.py` (digest §8) and `retrieval/semantic.py` (digest §9) **verbatim**, with these exact deltas:
- Parameterize `embed_projects(*, config, store, collection, limit=None)` and `project_semantic_search(*, config, collection, store, query, top_k, candidate_k, filter_payload)` — `collection` is passed in by the leaf (e.g. `"gitlab_epfl_projects"`), not a module constant.
- `_chunk_id("projects", project_id, idx)` (same `uuid5(NAMESPACE_URL, "projects|{id}|{idx}")`).
- `_row_to_chunks`: compose text from `project_id`, `description`, `"topics: " + ", ".join(topics)` (no README fetch in Phase 1 — projects list has no readme; `min_card_chars` from config).
- `_row_to_payload`: `{entity_type:"projects", entity_id:project_id, project_id, host, full_path, visibility, star_count, is_fork}`.
- Hydrate via `store.fetch_project`.
- Reuse `RCPEmbeddingClient`, `RCPRerankerClient`, `QdrantStore`, `chunk_text` imported from the same openalex/github modules github_repos uses (see digest §8/§9 imports).

- [ ] **Step 1–5:** Write `project_embed.py` + `project_retrieval.py` adapting the digest §8/§9 code with the deltas above. Test is deferred to the leaf integration test (Task 7) since these need RCP/Qdrant; keep unit coverage on `_row_to_chunks`/`_row_to_payload` (pure):

```python
# tests/index/_gitlab_base/test_project_embed_text.py
from src.index._gitlab_base.project_embed import _row_to_chunks, _row_to_payload

def test_payload_shape():
    row = {"project_id": "https://gitlab.epfl.ch/g/p", "host": "gitlab.epfl.ch",
           "full_path": "g/p", "visibility": "public", "star_count": 2, "is_fork": False}
    p = _row_to_payload(row)
    assert p["entity_type"] == "projects" and p["entity_id"] == row["project_id"]

def test_chunks_skip_when_too_short():
    row = {"project_id": "x", "description": None, "topics": None}
    assert _row_to_chunks(row, chunk_tokens=400, overlap=40, min_card_chars=64) == []
```

Commit: `feat(gitlab): project embed + retrieval (parameterized collection)`.

---

## Task 6: `gitlab_epfl_projects` leaf + config + paths

**Files:**
- Create: `src/index/_gitlab_base/paths_base.py`, `src/index/_gitlab_base/config_base.py`
- Create: `src/index/gitlab_epfl_projects/{__init__,paths,config,store,ingest,embed,retrieval}.py`, `src/index/gitlab_epfl_projects/storage/schema.sql`
- Create: `config/index/gitlab_epfl_projects.yaml`
- Test: `tests/index/gitlab_epfl_projects/test_paths.py`

`paths_base.py::resolve_gitlab_paths(store_name)` returns a paths object whose `duckdb_path == data/index/<store_name>/duckdb/<store_name>.duckdb` (mirror `_github_accounts_base/paths_base.py`).

`config_base.py::GitLabIndexConfig` mirrors `github_repos/config.py` (digest §3) with a `gitlab` block: `host: str`, `token: str | None` (env `GITLAB_<INSTANCE>_TOKEN`), `per_page`, `min_card_chars`, `collection: str`, plus `rcp`, `qdrant`, `chunking`, `paths`. `load_gitlab_config(yaml_path, store_name, token_env)` merges env.

The leaf modules are thin: `paths.py` → `resolve_gitlab_paths("gitlab_epfl_projects")`; `config.py` → `load_gitlab_config(DEFAULT_YAML, "gitlab_epfl_projects", "GITLAB_EPFL_TOKEN")`; `store.py` → `GitLabProjectStore.open(get_paths().duckdb_path)`; `ingest.py`/`embed.py`/`retrieval.py` → call the `_gitlab_base` functions passing `host="gitlab.epfl.ch"`, `collection="gitlab_epfl_projects"`. `storage/schema.sql` is a copy of `_gitlab_base/project_schema.sql`.

`config/index/gitlab_epfl_projects.yaml`:
```yaml
gitlab:
  host: gitlab.epfl.ch
  per_page: 100
  min_card_chars: 64
  collection: gitlab_epfl_projects
rcp: { base_url: "${RCP_BASE_URL}", embedding_model: "...", embedding_dim: 4096, query_instruction: "...", reranker_model: "..." }
qdrant: { }
chunking: { size_tokens: 400, overlap_tokens: 40 }
```
(Copy the rcp/qdrant/chunking blocks verbatim from `config/index/github_repos.yaml`.)

- [ ] **Step 1:** test that `get_gitlab_epfl_projects_paths().duckdb_path` ends with `gitlab_epfl_projects/duckdb/gitlab_epfl_projects.duckdb`.
- [ ] **Step 2:** Run → FAIL.
- [ ] **Step 3:** Implement the bases + leaf + yaml.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(gitlab): gitlab_epfl_projects leaf + config + paths bases`.

---

## Task 7: Federated adapter + register + manifest hints

**Files:**
- Create: `src/index/_federated/adapters/gitlab_epfl_projects.py`
- Modify: `src/index/_federated/registry.py:87-94` (add `"gitlab_epfl_projects"` to `candidates`)
- Test: `tests/index/gitlab_epfl_projects/test_adapter_and_manifest.py`

Adapter mirrors `github_repos` adapter (digest §10): `name = "gitlab_epfl_projects"`, `entity_types = ["project"]`, manifest hints `backend = "vector"`, `surface_as_source = True`, `id_shape = "url"`. `search()` calls `project_semantic_search(...)`; `Hit.id`/`url` = the project_id (already a URL). `lookup(identifier)` resolves a gitlab.epfl.ch URL via `store.fetch_project`.

- [ ] **Step 1: Write the failing test**

```python
# tests/index/gitlab_epfl_projects/test_adapter_and_manifest.py
from src.index._federated.manifest import build_manifest

def test_store_in_manifest_as_vector_source_tile():
    by = {e["name"]: e for e in build_manifest()}
    e = by["gitlab_epfl_projects"]
    assert e["backend"] == "vector"
    assert e["surface_as_source"] is True
    assert e["duckdb"] == "gitlab_epfl_projects.duckdb"
    assert e["entity_types"] == ["project"]
```

- [ ] **Step 2:** Run → FAIL (`KeyError: gitlab_epfl_projects`).
- [ ] **Step 3:** Implement the adapter (mirror digest §10) + add to `registry.py` candidates.
- [ ] **Step 4:** Run → PASS.
- [ ] **Step 5:** Commit `feat(gitlab): gitlab_epfl_projects federated adapter + manifest`.

---

## Task 8: Full gate + PR

- [ ] **Step 1:** `python -m pytest tests/index/_gitlab_base/ tests/index/gitlab_epfl_projects/ tests/v2/test_gitlab_canonicalization.py -q` → all PASS.
- [ ] **Step 2:** Full gate: `python -m pytest tests/v2/ -q` → green (no regressions).
- [ ] **Step 3:** `python -m src.index._federated.manifest --sources` → confirm `gitlab_epfl_projects` appears.
- [ ] **Step 4:** Commit any test fixups; push `feat/gitlab-index`; open PR to `develop`.

---

## Self-Review

**Spec coverage:** stores (Task 6 leaf, layout `gitlab_<instance>_<type>`), native ingest (Tasks 2/4), shared `_gitlab_base` (Tasks 2-6), canonical web_url ids (Tasks 1/3/4), vector-backed (Task 5), per-instance token env (Task 6 config), manifest/maintenance free (Task 7 + by-disk), Phase-1 = epfl_projects template (all tasks). Phases 2 (fan-out self-hosted groups/users + ethz/datascience) and 3 (gitlab.com seed traversal) are separate plans.

**Deferred to Phase 2/3 (intentional):** group/user record+store (needs `_gitlab_accounts_base` mirroring `_github_accounts_base`); README enrichment for projects; gitlab.com seed-traversal client methods (`iter_subgroups`/`iter_group_projects`).

**Type consistency:** `project_id` (the web_url) is the PK/id everywhere; `GitLabProjectRecord` fields match the schema columns; `collection` is a passed parameter (not a constant) so the 4 project leaves reuse the base.
