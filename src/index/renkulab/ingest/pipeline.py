"""Top-level RenkuLab ingest pipeline.

Drives the four entity streams (projects, groups, users, data_connectors)
plus the two member streams. State is checkpointed per-entity to
`<paths.state_dir>/ingest_<scope>.json` so partial runs are resumable.
"""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.index.renkulab.ingest.projector import (
    project_data_connector,
    project_group,
    project_member,
    project_project,
    project_user,
)
from src.index.renkulab.ingest.renku_client import RenkulabClient
from src.index.renkulab.ingest.scope import Scope, matches

if TYPE_CHECKING:
    from src.index.renkulab.config import RenkulabIndexConfig
    from src.index.renkulab.storage.duckdb_store import RenkulabStore

LOGGER = logging.getLogger(__name__)


def _state_path(config: RenkulabIndexConfig, scope_name: str) -> Path:
    return config.paths.state_dir / f"ingest_{scope_name}.json"


def _load_state(config: RenkulabIndexConfig, scope_name: str) -> dict[str, Any]:
    path = _state_path(config, scope_name)
    if not path.exists():
        return {"completed": []}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        LOGGER.warning("could not parse %s; restarting state", path)
        return {"completed": []}


def _save_state(
    config: RenkulabIndexConfig,
    scope_name: str,
    state: dict[str, Any],
) -> None:
    _state_path(config, scope_name).write_text(
        json.dumps(state, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


async def _ingest_projects(
    *,
    client: RenkulabClient,
    store: RenkulabStore,
    scope: Scope,
    limit: int | None,
) -> int:
    n = 0
    async for raw in client.iter_projects(limit=limit):
        if not matches(scope, raw):
            continue
        row = project_project(raw)
        if row is None:
            continue
        store.upsert_project(row, raw=raw)
        n += 1
        if n % 200 == 0:
            LOGGER.info("projects ingested: %d", n)
    LOGGER.info("projects done: %d persisted", n)
    return n


async def _ingest_groups(
    *,
    client: RenkulabClient,
    store: RenkulabStore,
    scope: Scope,
    limit: int | None,
) -> int:
    n = 0
    async for raw in client.iter_groups(limit=limit):
        if not matches(scope, raw):
            continue
        row = project_group(raw)
        if row is None:
            continue
        store.upsert_group(row, raw=raw)
        n += 1
        if n % 200 == 0:
            LOGGER.info("groups ingested: %d", n)
    LOGGER.info("groups done: %d persisted", n)
    return n


async def _ingest_data_connectors(
    *,
    client: RenkulabClient,
    store: RenkulabStore,
    scope: Scope,
    limit: int | None,
) -> int:
    n = 0
    async for raw in client.iter_data_connectors(limit=limit):
        if not matches(scope, raw):
            continue
        row = project_data_connector(raw)
        if row is None:
            continue
        store.upsert_data_connector(row, raw=raw)
        n += 1
        if n % 200 == 0:
            LOGGER.info("data_connectors ingested: %d", n)
    LOGGER.info("data_connectors done: %d persisted", n)
    return n


async def _ingest_users_via_search(
    *,
    client: RenkulabClient,
    store: RenkulabStore,
    scope: Scope,
    limit: int | None,
) -> int:
    """Harvest public user records via `/search/query?q=type:User`.

    The dedicated `/users` endpoint requires auth; the search index is open
    and exposes the same surface (path, slug, first/last name).
    """
    n = 0
    async for raw in client.iter_search("type:User", limit=limit):
        if not matches(scope, raw):
            continue
        row = project_user(raw)
        if row is None:
            continue
        store.upsert_user(row, raw=raw)
        n += 1
        if n % 1000 == 0:
            LOGGER.info("users ingested: %d", n)
    LOGGER.info("users done: %d persisted", n)
    return n


async def _ingest_group_members(
    *,
    client: RenkulabClient,
    store: RenkulabStore,
) -> int:
    """Pull (group, user, role) for every persisted group.

    Group members are also persisted as user rows so that membership rows
    always reference an existing user even if the user wasn't seen in the
    `type:User` search pass.
    """
    n = 0
    for group_id in store.list_group_ids():
        # We need the slug to call /groups/{slug}/members.
        info = store.fetch_entity("groups", group_id)
        if info is None:
            continue
        slug = info.get("slug")
        if not slug:
            continue
        members = await client.fetch_group_members(slug)
        for raw in members:
            mrow = project_member(raw)
            if mrow is None:
                continue
            # Persist a stub user row so referential joins work.
            store.upsert_user(
                {
                    "user_id": mrow["user_id"],
                    "slug": mrow.get("slug"),
                    "path": mrow.get("path"),
                    "first_name": mrow.get("first_name"),
                    "last_name": mrow.get("last_name"),
                },
                raw=raw,
            )
        store.upsert_group_members(
            group_id,
            [{"user_id": project_member(m)["user_id"], "role": m.get("role")}
             for m in members
             if project_member(m) is not None],
        )
        n += len(members)
    LOGGER.info("group_members done: %d edges persisted", n)
    return n


async def _ingest_project_members(
    *,
    client: RenkulabClient,
    store: RenkulabStore,
) -> int:
    n = 0
    for project_id in store.list_project_ids():
        members = await client.fetch_project_members(project_id)
        for raw in members:
            mrow = project_member(raw)
            if mrow is None:
                continue
            store.upsert_user(
                {
                    "user_id": mrow["user_id"],
                    "slug": mrow.get("slug"),
                    "path": mrow.get("path"),
                    "first_name": mrow.get("first_name"),
                    "last_name": mrow.get("last_name"),
                },
                raw=raw,
            )
        store.upsert_project_members(
            project_id,
            [{"user_id": project_member(m)["user_id"], "role": m.get("role")}
             for m in members
             if project_member(m) is not None],
        )
        n += len(members)
        if n and n % 500 == 0:
            LOGGER.info("project_members ingested: %d", n)
    LOGGER.info("project_members done: %d edges persisted", n)
    return n


async def _ingest_async(
    *,
    config: RenkulabIndexConfig,
    store: RenkulabStore,
    scope: Scope,
    limit: int | None,
    refresh: bool,
    only: set[str] | None,
) -> dict[str, int]:
    state = _load_state(config, scope.name) if not refresh else {"completed": []}
    completed = set(state.get("completed") or [])
    client = RenkulabClient(config)
    summary: dict[str, int] = {}

    flags = config.entities

    plan: list[tuple[str, bool, Any]] = [
        ("projects", flags.projects, _ingest_projects),
        ("groups", flags.groups, _ingest_groups),
        ("data_connectors", flags.data_connectors, _ingest_data_connectors),
        ("users", flags.users, _ingest_users_via_search),
        ("group_members", flags.group_members, _ingest_group_members),
        ("project_members", flags.project_members, _ingest_project_members),
    ]

    for name, enabled, fn in plan:
        if not enabled:
            continue
        if only and name not in only:
            continue
        if name in completed and not refresh:
            LOGGER.info("scope=%s entity=%s already completed; skipping", scope.name, name)
            continue
        if name in {"group_members", "project_members"}:
            count = await fn(client=client, store=store)
        else:
            count = await fn(
                client=client,
                store=store,
                scope=scope,
                limit=limit,
            )
        summary[name] = count
        completed.add(name)
        state["completed"] = sorted(completed)
        _save_state(config, scope.name, state)

    return summary


def ingest_all(
    *,
    config: RenkulabIndexConfig,
    store: RenkulabStore,
    scope: Scope,
    limit: int | None = None,
    refresh: bool = False,
    only: set[str] | None = None,
) -> dict[str, int]:
    """Synchronous CLI entrypoint."""
    return asyncio.run(
        _ingest_async(
            config=config,
            store=store,
            scope=scope,
            limit=limit,
            refresh=refresh,
            only=only,
        ),
    )
