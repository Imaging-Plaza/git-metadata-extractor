"""Contracts shared by the internal Pydantic records."""

import ast
from pathlib import Path

import pytest
from pydantic import BaseModel, ValidationError

from git_metadata_extractor.agents.models import (
    AgentResult,
    ProviderSet,
    TypedEntityBuckets,
)
from git_metadata_extractor.config import V2Config
from git_metadata_extractor.providers.runtime import ProviderResources
from git_metadata_extractor.services.extraction import ExtractionOptions


def test_records_use_pydantic_validation():
    assert issubclass(AgentResult, BaseModel)
    with pytest.raises(ValidationError):
        AgentResult(data={}, tokens_prompt="invalid")
    with pytest.raises(ValidationError):
        AgentResult(data={}, unexpected=True)
    with pytest.raises(ValidationError):
        ExtractionOptions(source_url="https://github.com/a/b", agent_runtime="invalid")


def test_frozen_options_and_independent_defaults():
    options = ExtractionOptions(source_url="https://github.com/a/b")
    with pytest.raises(ValidationError, match="frozen"):
        options.source_url = "https://github.com/c/d"
    first, second = TypedEntityBuckets(), TypedEntityBuckets()
    first.repositories.append({"id": "repo"})
    assert second.repositories == []


def test_injected_clients_keep_identity_and_cleanup_is_private():
    client = object()
    providers = ProviderSet(github=client)
    assert providers.github is client
    resources = ProviderResources(bundle=providers, owned=[client])
    assert resources.bundle is providers
    assert resources.owned[0] is client
    assert "_close_task" not in resources.model_dump()
    replacement = object()
    copied = providers.model_copy(update={"github": replacement})
    assert copied.github is replacement
    assert providers.github is client


def test_production_records_do_not_declare_dataclasses():
    root = Path(__file__).resolve().parents[2] / "git_metadata_extractor"
    declarations = []
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text(encoding="utf-8-sig"))):
            if isinstance(node, ast.ClassDef) and any(
                "dataclass" in ast.unparse(decorator) for decorator in node.decorator_list
            ):
                declarations.append(f"{path.relative_to(root)}:{node.name}")
    assert not declarations, declarations


def test_config_credential_is_excluded_from_serialization_and_repr():
    config = V2Config(GME_GITHUB_TOKEN="test-only-token")  # noqa: S106
    assert config.GME_GITHUB_TOKEN == "test-only-token"  # noqa: S105
    assert "GME_GITHUB_TOKEN" not in config.model_dump()
    assert "test-only-token" not in repr(config)
