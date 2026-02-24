from __future__ import annotations

import uuid

from src.v2.canonicalization import resolve_article_id

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
    assert id_source == "infoscienceArticleIdentifier"


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
    assert id_source == "infoscienceArticleIdentifier"


def test_resolve_article_id_falls_back_to_uuid_v5() -> None:
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
    assert parsed.version == UUID_V5_VERSION
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
