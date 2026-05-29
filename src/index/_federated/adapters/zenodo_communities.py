"""Federated adapter for the `zenodo_communities` index.

DuckDB-backed (no vector store) — `search` and `lookup` both run direct
SQL against `data/index/zenodo_communities/duckdb/zenodo_communities.duckdb`.
Keyword matching uses case-insensitive `LIKE` on title/source_slug/
parent_org; exact-acronym/slug match is used as a strong-signal
fallback when no LIKE hits land.
"""

from __future__ import annotations

import logging
from typing import Any

from src.index._federated.registry import EntityRecord, Hit, register

logger = logging.getLogger(__name__)


def _open_read_only():
    """Lazy open — keeps `from src.index._federated.adapters.zenodo_communities`
    cheap and tolerates a missing DB file gracefully."""
    from src.index.zenodo_communities.paths import duckdb_path  # noqa: PLC0415

    path = duckdb_path()
    if not path.exists():
        return None
    import duckdb  # noqa: PLC0415

    return duckdb.connect(str(path), read_only=True)


def _row_to_hit(row: dict[str, Any], score: float) -> Hit:
    return Hit(
        index="zenodo_communities",
        entity_type="community",
        id=row["community_id"],
        title=row.get("title") or row.get("source_slug"),
        score=score,
        summary=(row.get("description") or "")[:300] or None,
        url=row.get("url"),
        payload=dict(row),
    )


class ZenodoCommunitiesAdapter:
    name = "zenodo_communities"
    entity_types = ["community"]

    def search(
        self,
        *,
        query: str,
        entity_type: str | None,  # noqa: ARG002
        top_k: int,
        filters: dict[str, Any] | None,
    ) -> list[Hit]:
        con = _open_read_only()
        if con is None or not isinstance(query, str) or not query.strip():
            return []
        try:
            parent_org = (filters or {}).get("parent_org") if isinstance(filters, dict) else None
            q = query.strip()
            params: list[Any] = [q, f"%{q}%", f"%{q}%"]
            where = "(source_slug = ? OR title ILIKE ? OR description ILIKE ?)"
            if parent_org:
                where += " AND parent_org = ?"
                params.append(parent_org)
            sql = f"""
                SELECT community_id, source, source_slug, parent_org, title,
                       description, url
                FROM communities
                WHERE {where}
                ORDER BY
                    CASE WHEN source_slug = ? THEN 0 ELSE 1 END,
                    LENGTH(COALESCE(title, '')) ASC
                LIMIT ?
            """
            params.extend([q, max(1, int(top_k))])
            rows = con.execute(sql, params).fetchall()
            cols = [d[0] for d in con.description]
            out: list[Hit] = []
            for i, row in enumerate(rows):
                record = dict(zip(cols, row, strict=False))
                # Score: exact slug match = 1.0, otherwise decay with rank.
                score = 1.0 if record.get("source_slug") == q else max(
                    0.1, 0.9 - (i * 0.1),
                )
                out.append(_row_to_hit(record, score))
            return out
        except Exception:  # noqa: BLE001
            logger.exception("zenodo_communities.search failed for %r", query)
            return []
        finally:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass

    def lookup(self, identifier: str) -> list[EntityRecord]:
        con = _open_read_only()
        if con is None or not isinstance(identifier, str) or not identifier.strip():
            return []
        try:
            s = identifier.strip()
            # Three flavours of identifier we recognise:
            #   1. Full Zenodo URL   (`https://zenodo.org/communities/<slug>`,
            #      the canonical PK post-migration)
            #   2. bare `<slug>`     (matches source_slug)
            #   3. Legacy `zenodo:<slug>` (pre-migration PK; kept for
            #      backwards compatibility while consumers update)
            slug = s
            if s.startswith("https://zenodo.org/communities/"):
                slug = s.rsplit("/", 1)[-1].rstrip("/")
            elif s.startswith("zenodo:"):
                slug = s.split(":", 1)[1]
            rows = con.execute(
                """
                SELECT community_id, source, source_slug, parent_org, title,
                       description, url
                FROM communities
                WHERE community_id = ? OR source_slug = ?
                LIMIT 5
                """,
                [s, slug],
            ).fetchall()
            cols = [d[0] for d in con.description]
            out: list[EntityRecord] = []
            for row in rows:
                record = dict(zip(cols, row, strict=False))
                out.append(EntityRecord(
                    index="zenodo_communities",
                    entity_type="community",
                    id=record["community_id"],
                    data=record,
                    url=record.get("url"),
                ))
            return out
        except Exception:  # noqa: BLE001
            logger.exception("zenodo_communities.lookup failed for %r", identifier)
            return []
        finally:
            try:
                con.close()
            except Exception:  # noqa: BLE001
                pass


register(ZenodoCommunitiesAdapter())
