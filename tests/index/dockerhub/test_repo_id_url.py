"""dockerhub repo_id is the canonical Docker Hub URL (v3.0.0 ids)."""
from __future__ import annotations
from src.index.dockerhub.ingest.repos import _record_from_payload
from src.v2.canonicalization.dockerhub import dockerhub_iri


def test_official_image_url() -> None:
    rec = _record_from_payload(namespace="library", name="python", payload={}, tags=[])
    assert rec.repo_id == "https://hub.docker.com/_/python"
    assert rec.namespace == "library" and rec.name == "python"


def test_user_image_url() -> None:
    rec = _record_from_payload(namespace="grafana", name="grafana", payload={}, tags=[])
    assert rec.repo_id == "https://hub.docker.com/r/grafana/grafana"


def test_canonicalizer() -> None:
    assert dockerhub_iri("library", "python") == "https://hub.docker.com/_/python"
    assert dockerhub_iri("grafana", "grafana") == "https://hub.docker.com/r/grafana/grafana"
    assert dockerhub_iri(None, None) is None
