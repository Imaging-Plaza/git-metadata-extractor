"""Adapter wrapping `src.index.gitlab_datascience_users` for federated search/lookup."""

from __future__ import annotations

from typing import Any

from src.index._federated.registry import EntityRecord, Hit, register


class GitLabDatascienceUsersAdapter:
    name = "gitlab_datascience_users"
    entity_types: list[str] = ["user"]  # noqa: RUF012

    # Manifest hints (see IndexAdapter docstring).
    backend = "vector"
    surface_as_source = True
    id_shape = "url"  # user_id is already the canonical https://gitlab.datascience.ch/<username> URL

    def search(
        self,
        *,
        query: str,
        entity_type: str | None,  # noqa: ARG002 — single-type index
        top_k: int,
        filters: dict[str, Any] | None,  # noqa: ARG002
    ) -> list[Hit]:
        try:
            from src.index.gitlab_datascience_users.retrieval import search  # noqa: PLC0415, I001
        except Exception:  # noqa: BLE001
            return []
        try:
            results = search(query, top_k=top_k)
        except Exception:  # noqa: BLE001
            return []
        out: list[Hit] = []
        for r in results:
            payload = r.get("payload") or {}
            user_id = payload.get("user_id") or payload.get("entity_id")
            if not user_id:
                continue
            entity = r.get("entity") or {}
            out.append(Hit(
                index=self.name,
                entity_type="user",
                id=str(user_id),
                title=(entity.get("name") if entity else None) or str(user_id),
                score=float(r.get("rerank_score") or r.get("vector_score") or 0.0),
                summary=_summary(entity),
                url=str(user_id),
                payload=payload,
            ))
        return out

    def lookup(self, identifier: str) -> list[EntityRecord]:  # noqa: PLR0911
        if not isinstance(identifier, str) or not identifier.strip():
            return []
        s = identifier.strip()
        # Validate that it's a GitLab user URL using the canonicalization helper.
        try:
            from src.v2.canonicalization.gitlab import parse_gitlab_iri  # noqa: PLC0415, I001
        except Exception:  # noqa: BLE001
            return []
        parsed = parse_gitlab_iri(s)
        if parsed is None:
            return []
        host, kind, _path = parsed
        if kind != "user" or host != "gitlab.datascience.ch":
            return []
        try:
            from src.index.gitlab_datascience_users.store import open_store  # noqa: PLC0415, I001
        except Exception:  # noqa: BLE001
            return []
        store = None
        try:
            store = open_store()
            row = store.fetch_user(s)
            if row is None:
                return []
            return [EntityRecord(
                index=self.name,
                entity_type="user",
                id=s,
                data=row,
                url=s,
            )]
        except Exception:  # noqa: BLE001
            return []
        finally:
            if store is not None:
                try:  # noqa: SIM105
                    store.close()
                except Exception:  # noqa: BLE001, S110
                    pass


def _summary(entity: dict[str, Any]) -> str | None:
    parts = [
        str(entity.get("bio") or ""),
        str(entity.get("organization") or ""),
        str(entity.get("location") or ""),
    ]
    parts = [p for p in parts if p]
    return " — ".join(parts) if parts else None


register(GitLabDatascienceUsersAdapter())
