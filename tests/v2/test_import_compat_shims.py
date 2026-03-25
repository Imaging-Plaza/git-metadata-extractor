from __future__ import annotations

import importlib
import sys
import warnings

import pytest

LEGACY_TO_CANONICAL = [
    ("src.v2.providers", "src.v2.ingest.providers"),
    ("src.v2.providers.base", "src.v2.ingest.providers.base"),
    ("src.v2.detection", "src.v2.ingest.detection"),
    ("src.v2.detection.models", "src.v2.ingest.detection.models"),
    ("src.v2.canonicalization", "src.v2.normalizers"),
    ("src.v2.canonicalization.string_utils", "src.v2.normalizers.string_utils"),
    ("src.v2.validation", "src.v2.quality"),
    ("src.v2.validation.schema_validation", "src.v2.quality.schema_validation"),
    ("src.v2.models", "src.v2.api_models"),
    ("src.v2.models.contracts", "src.v2.api_models.contracts"),
    ("src.v2.generated", "src.v2.schema.models"),
    ("src.v2.generated.entities", "src.v2.schema.models.strict"),
    ("src.v2.llm", "src.v2.agents.llm"),
    ("src.v2.llm.runtime", "src.v2.agents.llm.runtime"),
    ("src.v2.agents.article_agent", "src.v2.agents.rule_based.article_agent"),
    ("src.v2.agents.person_agent", "src.v2.agents.rule_based.person_agent"),
]


def _purge_module(module_name: str) -> None:
    parts = module_name.split(".")
    for size in range(len(parts), 1, -1):
        sys.modules.pop(".".join(parts[:size]), None)


@pytest.mark.parametrize(("legacy_module", "canonical_module"), LEGACY_TO_CANONICAL)
def test_legacy_import_shims_warn_and_resolve(
    legacy_module: str,
    canonical_module: str,
) -> None:
    _purge_module(legacy_module)

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always", DeprecationWarning)
        legacy = importlib.import_module(legacy_module)
        canonical = importlib.import_module(canonical_module)

    assert legacy is not None
    assert canonical is not None
    assert any(
        isinstance(item.message, DeprecationWarning) for item in caught
    ), f"Expected DeprecationWarning when importing {legacy_module}"

