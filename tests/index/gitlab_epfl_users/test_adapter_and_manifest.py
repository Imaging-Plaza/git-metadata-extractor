from src.index._federated.manifest import build_manifest


def test_store_in_manifest_as_vector_source_tile():
    by = {e["name"]: e for e in build_manifest()}
    e = by["gitlab_epfl_users"]
    assert e["backend"] == "vector"
    assert e["surface_as_source"] is True
    assert e["duckdb"] == "gitlab_epfl_users.duckdb"
    assert e["entity_types"] == ["user"]
