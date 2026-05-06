"""Post-process the heavy `infoscience_links_dump.json` into a slim
lookup table for fast re-entry.

Output: `data/index/infoscience/dumps/infoscience_links_index.json`

One row per article with just enough metadata + per-host URL lists to
answer "which papers cite host X?" without loading the full DSpace JSON
or hitting Infoscience again.

URL extraction:
- regex over `text/{uuid}.txt` if a TEXT bundle is on disk;
- empty per-host list otherwise (the article still appears, with
  `matched_phrases` recording which Solr filter found it).
"""

from __future__ import annotations

import datetime as dt
import json
import re
from pathlib import Path
from typing import Any, Dict

from src.index.infoscience.parsers import first_value
from src.index.infoscience.paths import (
    dumps_dir,
    text_dir,
)

HOST_REGEXES: Dict[str, re.Pattern[str]] = {
    "github":         re.compile(r"https?://(?:www\.)?github\.com/[^\s)>\]\"'`,]+"),
    "gitlab":         re.compile(r"https?://(?:www\.)?gitlab\.com/[^\s)>\]\"'`,]+"),
    "gitlab_epfl":    re.compile(r"https?://(?:www\.)?gitlab\.epfl\.ch/[^\s)>\]\"'`,]+"),
    "c4science":      re.compile(r"https?://(?:www\.)?c4science\.ch/[^\s)>\]\"'`,]+"),
    "bitbucket":      re.compile(r"https?://(?:www\.)?bitbucket\.org/[^\s)>\]\"'`,]+"),
    "huggingface":    re.compile(r"https?://(?:www\.)?huggingface\.co/[^\s)>\]\"'`,]+"),
    "hf_co":          re.compile(r"https?://(?:www\.)?hf\.co/[^\s)>\]\"'`,]+"),
    "zenodo":         re.compile(r"https?://(?:www\.)?zenodo\.org/[^\s)>\]\"'`,]+"),
    "figshare":       re.compile(r"https?://(?:www\.)?figshare\.com/[^\s)>\]\"'`,]+"),
    "osf":            re.compile(r"https?://(?:www\.)?osf\.io/[^\s)>\]\"'`,]+"),
    "datadryad":      re.compile(r"https?://(?:www\.)?datadryad\.org/[^\s)>\]\"'`,]+"),
    "materialscloud": re.compile(r"https?://(?:www\.)?materialscloud\.org/[^\s)>\]\"'`,]+"),
    "kaggle":         re.compile(r"https?://(?:www\.)?kaggle\.com/[^\s)>\]\"'`,]+"),
    "paperswithcode": re.compile(r"https?://(?:www\.)?paperswithcode\.com/[^\s)>\]\"'`,]+"),
    "colab":          re.compile(r"https?://colab\.research\.google\.com/[^\s)>\]\"'`,]+"),
    "mybinder":       re.compile(r"https?://(?:www\.)?mybinder\.org/[^\s)>\]\"'`,]+"),
    "arxiv":          re.compile(r"https?://(?:www\.)?arxiv\.org/[^\s)>\]\"'`,]+"),
    "renkulab":       re.compile(r"https?://(?:www\.)?renkulab\.io/[^\s)>\]\"'`,]+"),
    "orcid":          re.compile(r"https?://(?:www\.)?orcid\.org/\d{4}-\d{4}-\d{4}-\d{3}[\dX]"),
}


def _extract_body_urls(uuid: str) -> Dict[str, list[str]]:
    p = text_dir() / f"{uuid}.txt"
    if not p.exists():
        return {}
    body = p.read_text(encoding="utf-8", errors="replace")
    out: Dict[str, list[str]] = {}
    for label, rx in HOST_REGEXES.items():
        urls = sorted({m.rstrip(".,;)") for m in rx.findall(body)})
        if urls:
            out[label] = urls
    return out


def main() -> None:
    dump_path = dumps_dir() / "infoscience_links_dump.json"
    if not dump_path.exists():
        msg = f"Heavy dump not found: {dump_path}. Run scripts/dump_link_articles.py first."
        raise SystemExit(msg)

    dump = json.loads(dump_path.read_text(encoding="utf-8"))
    items: list[dict[str, Any]] = []
    have_text = 0
    for art in dump["articles"]:
        item = art["item"]
        md = item.get("metadata", {}) or {}
        uuid = art["uuid"]
        body_urls = _extract_body_urls(uuid)
        if body_urls:
            have_text += 1
        items.append({
            "uuid": uuid,
            "title": first_value(md, "dc.title"),
            "year": (first_value(md, "dc.date.issued") or "")[:4] or None,
            "doi": first_value(md, "dc.identifier.doi"),
            "publication_type": first_value(md, "dc.type"),
            "infoscience_url": f"https://infoscience.epfl.ch/entities/publication/{uuid}",
            "matched_phrases": art["matched_phrases"],
            "person_uuids": art.get("person_uuids", []),
            "org_uuids": art.get("org_uuids", []),
            "body_urls": body_urls,
        })

    out = {
        "generated_at": dt.datetime.now(dt.timezone.utc).isoformat(),
        "queries": dump.get("queries", {}),
        "per_phrase_counts": dump.get("per_phrase_counts", {}),
        "totals": {
            "items": len(items),
            "items_with_local_text": have_text,
            "items_no_local_text": len(items) - have_text,
        },
        "items": items,
    }
    out_path = dumps_dir() / "infoscience_links_index.json"
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    size_mb = out_path.stat().st_size / (1024 * 1024)
    print(f"wrote {out_path} ({size_mb:.1f} MB)")
    print(json.dumps(out["totals"], indent=2))


if __name__ == "__main__":
    main()
