"""One-shot dump of Infoscience articles whose Solr-indexed full-text
contains a link to GitHub / GitLab / HuggingFace / ORCID / Zenodo.

For each phrase, paginate `/discover/search/objects?query=fulltext:"<phrase>"`,
union UUIDs across phrases, persist each indexable item to
`raw/items/{uuid}.json`, extract person + org authority UUIDs, fetch any
missing person / org records, and write a single JSON dump.
"""

from __future__ import annotations

import asyncio
import json
import sys
import time
from typing import Any, Dict, List, Set

from src.index.infoscience.config import load_config
from src.index.infoscience.dspace import DSpaceClient
from src.index.infoscience.extract_relations import (
    _ORG_ONLY_FIELDS,
    _PERSON_FIELDS,
    _authorities,
)
from src.index.infoscience.fetch_related import _fetch_set
from src.index.infoscience.paths import (
    infoscience_data_dir,
    raw_items_dir,
    raw_organizations_dir,
    raw_persons_dir,
)

# (label, Solr phrase). Phrase queries with a trailing slash filter to URL
# usages; bare-host phrases also work but capture more incidental mentions.
PHRASES: List[tuple[str, str]] = [
    # Code hosts
    ("github", '"github.com/"'),
    ("gitlab", '"gitlab.com/"'),
    ("gitlab_epfl", '"gitlab.epfl.ch"'),
    ("c4science", '"c4science.ch/"'),
    ("bitbucket", '"bitbucket.org/"'),
    # Data + model archives
    ("huggingface", '"huggingface.co/"'),
    ("hf_co", '"hf.co/"'),
    ("zenodo", '"zenodo.org/"'),
    ("figshare", '"figshare.com/"'),
    ("osf", '"osf.io/"'),
    ("datadryad", '"datadryad.org/"'),
    ("materialscloud", '"materialscloud.org/"'),
    ("kaggle", '"kaggle.com/"'),
    ("paperswithcode", '"paperswithcode.com/"'),
    # Notebook / runtime hosts
    ("colab", '"colab.research.google.com/"'),
    ("mybinder", '"mybinder.org/"'),
    # Preprints
    ("arxiv", '"arxiv.org/"'),
    # Workflow platforms
    ("renkulab", '"renkulab.io/"'),
    # Identifier graph (kept; high recall, low precision)
    ("orcid", '"orcid.org/"'),
]


async def collect_uuids(
    client: DSpaceClient,
    phrase: str,
    *,
    size: int = 100,
) -> Dict[str, Dict[str, Any]]:
    """Paginate one phrase; return {uuid: indexable_item}."""
    out: Dict[str, Dict[str, Any]] = {}
    async for indexable in client.iter_discover_fulltext(phrase, size=size):
        u = indexable.get("uuid")
        if u:
            out[u] = indexable
    return out


async def main() -> None:
    cfg = load_config()
    # Bump fetch concurrency for the related-entity step. Discovery itself
    # iterates pages serially per phrase.
    cfg.infoscience.max_concurrency = 8

    items_dir = raw_items_dir()
    persons_dir = raw_persons_dir()
    orgs_dir = raw_organizations_dir()

    # 1) Discover per phrase + union
    per_phrase_counts: Dict[str, int] = {}
    matches_per_uuid: Dict[str, List[str]] = {}
    items: Dict[str, Dict[str, Any]] = {}

    async with DSpaceClient(cfg.infoscience) as c:
        for label, phrase in PHRASES:
            t0 = time.monotonic()
            hits = await collect_uuids(c, phrase)
            per_phrase_counts[label] = len(hits)
            for u, it in hits.items():
                items.setdefault(u, it)
                matches_per_uuid.setdefault(u, []).append(label)
            dt = time.monotonic() - t0
            print(
                f"[discover] {label:13s} phrase={phrase:25s} hits={len(hits):5d} "
                f"({dt:5.1f}s)  union={len(items)}",
                flush=True,
            )

    print(f"[discover] total unique articles: {len(items)}", flush=True)

    # 2) Persist raw items + extract authority sets
    person_uuids: Set[str] = set()
    org_uuids: Set[str] = set()
    articles: List[Dict[str, Any]] = []

    for uuid, item in items.items():
        (items_dir / f"{uuid}.json").write_text(
            json.dumps(item, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        md = item.get("metadata", {}) or {}
        p = list(dict.fromkeys(_authorities(md, _PERSON_FIELDS)))
        o = list(dict.fromkeys(_authorities(md, _ORG_ONLY_FIELDS)))
        person_uuids.update(p)
        org_uuids.update(o)
        articles.append(
            {
                "uuid": uuid,
                "matched_phrases": sorted(matches_per_uuid[uuid]),
                "person_uuids": p,
                "org_uuids": o,
            }
        )

    print(
        f"[extract] articles={len(articles)} "
        f"persons={len(person_uuids)} orgs={len(org_uuids)}",
        flush=True,
    )

    # 3) Fetch missing persons + orgs
    have_p = {p.stem for p in persons_dir.glob("*.json")}
    have_o = {p.stem for p in orgs_dir.glob("*.json")}
    missing_p = sorted(u for u in person_uuids if u not in have_p)
    missing_o = sorted(u for u in org_uuids if u not in have_o)
    print(
        f"[fetch] missing persons={len(missing_p)}/{len(person_uuids)} "
        f"orgs={len(missing_o)}/{len(org_uuids)}",
        flush=True,
    )

    if missing_p:
        t0 = time.monotonic()
        rp = await _fetch_set(cfg, missing_p, persons_dir, refresh=False)
        print(f"[fetch] persons: {rp}  ({time.monotonic()-t0:.1f}s)", flush=True)
    if missing_o:
        t0 = time.monotonic()
        ro = await _fetch_set(cfg, missing_o, orgs_dir, refresh=False)
        print(f"[fetch] organizations: {ro}  ({time.monotonic()-t0:.1f}s)", flush=True)

    # 4) Resolve full records
    def _load(d, u):
        p = d / f"{u}.json"
        return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

    persons_resolved = {u: _load(persons_dir, u) for u in sorted(person_uuids)}
    orgs_resolved = {u: _load(orgs_dir, u) for u in sorted(org_uuids)}

    # Inline article items (full DSpace JSON, not just authority sets)
    for a in articles:
        a["item"] = items[a["uuid"]]

    dump = {
        "queries": {label: f'fulltext:{phrase}' for label, phrase in PHRASES},
        "per_phrase_counts": per_phrase_counts,
        "totals": {
            "articles": len(articles),
            "persons": sum(1 for v in persons_resolved.values() if v),
            "organizations": sum(1 for v in orgs_resolved.values() if v),
            "persons_unresolved": sum(1 for v in persons_resolved.values() if not v),
            "orgs_unresolved": sum(1 for v in orgs_resolved.values() if not v),
        },
        "articles": articles,
        "persons": persons_resolved,
        "organizations": orgs_resolved,
    }

    out_dir = infoscience_data_dir() / "dumps"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "infoscience_links_dump.json"
    out_path.write_text(json.dumps(dump, indent=2, ensure_ascii=False), encoding="utf-8")
    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"[write] {out_path}  ({size_mb:.1f} MB)", flush=True)
    print(f"[summary] {json.dumps(dump['totals'])}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
