from __future__ import annotations

# Boundary tests intentionally inspect private cache keys and use local imports.
# ruff: noqa: PLC0415, SLF001
from copy import deepcopy

import pytest
from pydantic import ValidationError

from git_metadata_extractor.mapping import load_mapping, map_fields, mapping_manifest
from git_metadata_extractor.mapping.engine import parse_mapping
from git_metadata_extractor.pipeline.stages.raw_projection import project_raw


def test_yaml_contract_rejects_unknown_transform_and_target():
    template = """
schema_version: 1
name: example.mapping
version: '1'
source_contract: github.rest.repository
target_contract: agent.repository
fields:
  %s: {sources: [name], transform: %s}
"""
    with pytest.raises(ValidationError):
        parse_mapping(template % ("schema:name", "eval"))
    with pytest.raises(ValueError, match="undeclared"):
        parse_mapping(template % ("schema:notAProperty", "identity"))


def test_duplicate_yaml_keys_are_rejected():
    with pytest.raises(ValueError, match="Duplicate"):
        parse_mapping("name: first\nname: second\n")


def test_github_mapping_preserves_zero_false_and_missing_policy():
    source = {
        "stargazers_count": 0,
        "archived": False,
        "topics": ["rdf", "", 3],
        "owner": {"avatar_url": "https://example.org/avatar.png"},
    }
    before = deepcopy(source)
    mapped = map_fields("github.repository", source)
    assert mapped["pulse:githubRepoStars"] == 0
    assert mapped["_archived"] is False
    assert mapped["pulse:githubRepoForks"] is None
    assert mapped["_keywords"] == ["rdf"]
    assert mapped["_avatar_url"] == source["owner"]["avatar_url"]
    mapped["_keywords"].append("changed")
    assert source == before


def test_raw_profile_fallback_and_iri_mapping():
    mapped = map_fields(
        "raw.person_profile",
        {
            "_bio": "GitHub bio",
            "_orcid_biography": "ORCID bio",
            "_followers_count": 0,
            "_avatar_url": "https://example.org/avatar.png",
        },
    )
    assert mapped["pulse:biography"] == "ORCID bio"
    assert mapped["pulse:followerCount"] == 0
    assert mapped["schema:image"] == {"@id": "https://example.org/avatar.png"}
    assert "schema:url" not in mapped


def test_raw_repository_does_not_claim_size_is_a_file_count():
    node = {
        "@id": "https://github.com/example/repo",
        "@type": "schema:SoftwareSourceCode",
        "pulse:githubRepositoryHandle": "https://github.com/example/repo",
        "_size_kb": 1234,
        "_subscribers_count": 5,
        "pulse:githubRepoStars": 0,
    }
    result = project_raw([node], run_id="mapping-test")
    repository = next(n for n in result["@graph"] if n.get("@id") == node["@id"])
    assert "pulse:fileCount" not in repository
    assert repository["pulse:watcherCount"] == node["_subscribers_count"]
    assert repository["pulse:repositoryStars"] == 0


def test_mapping_inventory_has_versioned_contracts_and_rejects_paths():
    manifest = mapping_manifest()
    assert len(bytes.fromhex(manifest["fingerprint"])) == 32  # noqa: PLR2004 - SHA-256 bytes
    assert {entry["name"] for entry in manifest["mappings"]} >= {
        "github.repository",
        "raw.person_profile",
        "raw.organization_profile",
        "raw.repository",
    }
    with pytest.raises(ValueError, match=r"namespace\.name"):
        load_mapping("../secrets")


def test_mapping_records_version_and_contract_in_provenance():
    from git_metadata_extractor.observation.query_log import QueryLog, query_log_var

    log = QueryLog(
        run_id="mapping-test",
        extract_full_path="https://github.com/example/repo",
    )
    token = query_log_var.set(log)
    try:
        map_fields("github.repository", {"stargazers_count": 3})
    finally:
        query_log_var.reset(token)
    node = next(
        n for n in log.evidence.document([])["@graph"] if n.get("gme:kind") == "mapping"
    )
    assert node["gme:mapping_version"] == "1"
    assert node["gme:source_contract"] == "github.rest.repository"
    assert node["gme:mapping_fingerprint"] == mapping_manifest()["fingerprint"]


def test_mapping_changes_invalidate_agent_verdict_cache(monkeypatch):
    from git_metadata_extractor.agents.llm import _verdict_cache

    monkeypatch.setattr(
        _verdict_cache,
        "mapping_manifest",
        lambda: {"fingerprint": "before"},
        raising=False,
    )
    before = _verdict_cache._verdict_key("repository", {"id": "example/repo"})
    monkeypatch.setattr(
        _verdict_cache,
        "mapping_manifest",
        lambda: {"fingerprint": "after"},
    )
    assert before != _verdict_cache._verdict_key("repository", {"id": "example/repo"})


def test_loaded_mapping_is_not_mutable_shared_configuration():
    spec = load_mapping("github.repository")
    spec.fields.clear()
    assert load_mapping("github.repository").fields


def test_yaml_edit_changes_actual_fingerprint(tmp_path, monkeypatch):
    from git_metadata_extractor.mapping import engine

    source = engine.CONFIG_ROOT / "github/repository.yaml"
    path = tmp_path / "github/repository.yaml"
    path.parent.mkdir()
    path.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setattr(engine, "CONFIG_ROOT", tmp_path)
    engine._load.cache_clear()
    engine._manifest.cache_clear()
    try:
        before = mapping_manifest()["fingerprint"]
        path.write_text(
            path.read_text(encoding="utf-8").replace("version: '1'", "version: '2'"),
            encoding="utf-8",
        )
        engine._load.cache_clear()
        engine._manifest.cache_clear()
        assert load_mapping("github.repository").version == "2"
        assert mapping_manifest()["fingerprint"] != before
    finally:
        engine._load.cache_clear()
        engine._manifest.cache_clear()
