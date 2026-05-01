"""Scope filter shapes."""

from __future__ import annotations

import pytest

from src.index.openalex.config import OpenAlexIndexConfig
from src.index.openalex.ingest.scope import (
    epfl_scope,
    resolve_scope,
    switzerland_scope,
)


@pytest.mark.openalex()
def test_epfl_works_filter_uses_ror(base_config: OpenAlexIndexConfig):
    scope = epfl_scope(base_config)
    assert scope.works == {
        "authorships": {
            "institutions": {"ror": "https://ror.org/02s376052"},
        },
    }
    assert scope.institutions == {"ror": "https://ror.org/02s376052"}
    # Sources cannot be filtered by ROR — pulled unfiltered.
    assert scope.sources == {}


@pytest.mark.openalex()
def test_switzerland_works_filter_uses_country_code(
    base_config: OpenAlexIndexConfig,
):
    scope = switzerland_scope(base_config)
    assert scope.works == {
        "authorships": {"institutions": {"country_code": "ch"}},
    }
    assert scope.institutions == {"country_code": "ch"}
    assert scope.sources == {}


@pytest.mark.openalex()
def test_resolve_scope_dispatches(base_config: OpenAlexIndexConfig):
    assert resolve_scope("epfl", base_config) == epfl_scope(base_config)
    assert resolve_scope("switzerland", base_config) == switzerland_scope(base_config)


@pytest.mark.openalex()
def test_resolve_scope_unknown(base_config: OpenAlexIndexConfig):
    with pytest.raises(ValueError, match="Unknown scope"):
        resolve_scope("germany", base_config)  # type: ignore[arg-type]
