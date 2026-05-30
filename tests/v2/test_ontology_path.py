"""Guards for the ontology-TTL resolution (the prod packaging fix).

The SHACL gate loads the open-pulse ontology at runtime. The TTL used to
live under `dev/`, which is NOT copied into the Docker image, so the gate
died with FileNotFoundError in the container. The file now ships inside
the package (`src/v2/validation/`); these tests lock that in.
"""

from __future__ import annotations

import importlib
from pathlib import Path

import pytest

from src.v2.validation import ontology as ont


def test_packaged_ttl_is_shipped_inside_the_package() -> None:
    """The canonical TTL must live under src/ (so `COPY src` ships it),
    never only under dev/."""
    assert ont._PACKAGED_PATH.exists(), ont._PACKAGED_PATH
    # It must be under the installed package tree, not the dev bundle.
    assert "dev" not in ont._PACKAGED_PATH.parts
    assert ont._PACKAGED_PATH.parent.name == "validation"


def test_resolver_returns_existing_packaged_path_by_default() -> None:
    resolved = ont.ontology_ttl_path()
    assert resolved == ont._PACKAGED_PATH
    assert resolved.exists()


def test_load_ontology_shapes_graph_parses() -> None:
    graph = ont.load_ontology_shapes_graph()
    assert len(graph) > 0


def test_env_override_takes_precedence(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    override = tmp_path / "custom-ontology.ttl"
    # Reuse the real ontology content so a parse would succeed too.
    override.write_text(ont._PACKAGED_PATH.read_text(encoding="utf-8"), encoding="utf-8")
    monkeypatch.setenv("GME_ONTOLOGY_TTL", str(override))
    # Re-evaluate candidate resolution under the patched env.
    assert ont.ontology_ttl_path() == override


def test_env_override_ignored_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A bad env path falls through to the packaged copy rather than blowing up."""
    monkeypatch.setenv("GME_ONTOLOGY_TTL", "/nonexistent/nope.ttl")
    assert ont.ontology_ttl_path() == ont._PACKAGED_PATH


def test_missing_everything_raises_informative_error(monkeypatch: pytest.MonkeyPatch) -> None:
    """When no candidate exists, the error names what was tried + the env var."""
    monkeypatch.setattr(ont, "_PACKAGED_PATH", Path("/nope/packaged.ttl"))
    monkeypatch.setattr(ont, "_DEV_FALLBACK_PATH", Path("/nope/dev.ttl"))
    monkeypatch.delenv("GME_ONTOLOGY_TTL", raising=False)
    # Bust the lru_cache so the patched paths are honoured.
    importlib.reload(ont)
    monkeypatch.setattr(ont, "_PACKAGED_PATH", Path("/nope/packaged.ttl"))
    monkeypatch.setattr(ont, "_DEV_FALLBACK_PATH", Path("/nope/dev.ttl"))
    with pytest.raises(FileNotFoundError, match="GME_ONTOLOGY_TTL"):
        ont.load_ontology_shapes_graph()
    importlib.reload(ont)  # restore real module state for other tests
