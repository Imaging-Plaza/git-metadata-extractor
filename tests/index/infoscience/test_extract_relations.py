"""Article → Person/Org authority-UUID extraction."""

from __future__ import annotations

import json
from pathlib import Path

from src.index.infoscience.config import (
    ChunkingConfig,
    FilterConfig,
    InfoscienceConfig,
    InfoscienceIndexConfig,
    QdrantConfig,
    RcpConfig,
)
from src.index.infoscience.extract_relations import extract_relations
from src.index.infoscience.models import MatchRecord
from src.index.infoscience.paths import (
    matches_path,
    organizations_set_path,
    persons_set_path,
    raw_items_dir,
    relations_path,
)


def _stub_config() -> InfoscienceIndexConfig:
    return InfoscienceIndexConfig(
        rcp=RcpConfig(base_url="https://stub/v1", embedding_model="m",
                      embedding_dim=4096, query_instruction="x", reranker_model="r"),
        infoscience=InfoscienceConfig(base_url="https://stub/api"),
        filter=FilterConfig(terms=["github.com"]),
        chunking=ChunkingConfig(),
        qdrant=QdrantConfig(),
        data_dir=Path("/tmp"),
    )


def test_extract_relations_pulls_authority_uuids(article_json: dict) -> None:
    uuid = article_json["uuid"]
    (raw_items_dir() / f"{uuid}.json").write_text(json.dumps(article_json), encoding="utf-8")
    matches_path().write_text(
        MatchRecord(uuid=uuid, matched_urls=["https://github.com/x/y"],
                    counts_by_host={"github.com": 1}).model_dump_json() + "\n",
        encoding="utf-8",
    )

    summary = extract_relations(_stub_config())
    assert summary["articles"] == 1
    assert summary["persons"] >= 1
    assert summary["organizations"] >= 1

    relation = json.loads(relations_path().read_text(encoding="utf-8").strip())
    # v3.0.0: relations.jsonl carries the canonical entity URLs (so the
    # reverse maps key on the same ids as the Qdrant records)...
    assert relation["article_uuid"] == f"https://infoscience.epfl.ch/entities/publication/{uuid}"
    assert all(
        p.startswith("https://infoscience.epfl.ch/entities/person/")
        for p in relation["person_uuids"]
    )
    assert all(
        o.startswith("https://infoscience.epfl.ch/entities/orgunit/")
        for o in relation["org_uuids"]
    )
    # ...but the .txt sets stay bare UUIDs (the fetch-by-UUID worklist).
    persons_listing = persons_set_path().read_text(encoding="utf-8").splitlines()
    orgs_listing = organizations_set_path().read_text(encoding="utf-8").splitlines()
    assert all(len(p) == 36 for p in persons_listing)
    assert all(len(o) == 36 for o in orgs_listing)
    assert relation["person_uuids"][0].rsplit("/", 1)[-1] in persons_listing
    assert relation["org_uuids"][0].rsplit("/", 1)[-1] in orgs_listing
