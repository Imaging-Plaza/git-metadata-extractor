from __future__ import annotations

import uuid
from typing import Any, Callable

from src.v2.canonicalization import resolve_article_id
from src.v2.validation.schema_validation import StrictSchemaValidator

UUID_V5_VERSION = 5


def test_resolve_article_id_prefers_doi() -> None:
    article = {
        "identifiers": {
            "schema:identifier": "10.1038/s41586-024-07856-z",
            "pulse:infoscienceArticleIdentifier": "dbce93b0-4ad7-45f2-8a53-b85bf39aeec9",
        },
    }

    canonical_id, id_source = resolve_article_id(article)

    assert canonical_id == "https://doi.org/10.1038/s41586-024-07856-z"
    assert id_source == "schema:identifier"


def test_resolve_article_id_normalizes_infoscience_api_url_with_full_suffix() -> None:
    article = {
        "identifiers": {
            "schema:identifier": None,
            "pulse:infoscienceArticleIdentifier": (
                "https://infoscience.epfl.ch/server/api/entities/publication/"
                "dbce93b0-4ad7-45f2-8a53-b85bf39aeec9/full"
            ),
        },
    }

    canonical_id, id_source = resolve_article_id(article)

    assert canonical_id == (
        "https://infoscience.epfl.ch/server/api/core/items/"
        "dbce93b0-4ad7-45f2-8a53-b85bf39aeec9"
    )
    assert id_source == "pulse:infoscienceArticleIdentifier"


def test_resolve_article_id_normalizes_infoscience_core_items_url() -> None:
    article = {
        "identifiers": {
            "schema:identifier": None,
            "pulse:infoscienceArticleIdentifier": (
                "https://infoscience.epfl.ch/server/api/core/items/"
                "dbce93b0-4ad7-45f2-8a53-b85bf39aeec9"
            ),
        },
    }

    canonical_id, id_source = resolve_article_id(article)

    assert canonical_id == (
        "https://infoscience.epfl.ch/server/api/core/items/"
        "dbce93b0-4ad7-45f2-8a53-b85bf39aeec9"
    )
    assert id_source == "pulse:infoscienceArticleIdentifier"


def test_resolve_article_id_falls_back_to_uuid4() -> None:
    # Fallback emits uuid4 (was uuid5) to avoid cross-repo collisions —
    # see canonicalization id_resolution `_deterministic_uuid`.
    article = {
        "schema:name": "Graph Article",
        "schema:datePublished": "2025-06-15",
        "identifiers": {
            "schema:identifier": None,
            "pulse:infoscienceArticleIdentifier": None,
        },
    }

    canonical_id, id_source = resolve_article_id(article)

    parsed = uuid.UUID(canonical_id)
    assert parsed.version == 4
    assert id_source == "uuid"


def test_resolve_article_id_is_idempotent_for_pre_resolved_payload() -> None:
    article = {
        "id": "https://doi.org/10.1038/s41586-024-07856-z",
        "idSource": "schema:identifier",
        "identifiers": {
            "schema:identifier": "10.1038/s41586-024-07856-z",
        },
    }

    canonical_id, id_source = resolve_article_id(article)

    assert canonical_id == "https://doi.org/10.1038/s41586-024-07856-z"
    assert id_source == "schema:identifier"


def test_resolve_article_id_canonicalizes_pre_resolved_doi_identifier() -> None:
    article = {
        "id": "10.1038/s41586-024-07856-z",
        "idSource": "schema:identifier",
        "identifiers": {
            "schema:identifier": "10.1038/s41586-024-07856-z",
        },
    }

    canonical_id, id_source = resolve_article_id(article)

    assert canonical_id == "https://doi.org/10.1038/s41586-024-07856-z"
    assert id_source == "schema:identifier"


def test_resolve_article_id_output_is_strict_enum_compatible(
    load_fixture: Callable[[str, str], Any],
) -> None:
    validator = StrictSchemaValidator()
    article = load_fixture("schema/strict", "pulse_ArticleShape")[0]
    article["id"] = "https://doi.org/10.1038/s41586-024-07856-z"
    article["idSource"] = resolve_article_id(article)[1]

    result = validator.validate("article", article)

    assert result.is_valid is True
