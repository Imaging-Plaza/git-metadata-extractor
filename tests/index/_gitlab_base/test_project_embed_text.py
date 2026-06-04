from src.index._gitlab_base.project_embed import _row_to_chunks, _row_to_payload


def test_payload_shape():
    row = {"project_id": "https://gitlab.epfl.ch/g/p", "host": "gitlab.epfl.ch",
           "full_path": "g/p", "visibility": "public", "star_count": 2, "is_fork": False,
           "name": "p", "description": "d"}
    p = _row_to_payload(row)
    assert p["entity_type"] == "projects" and p["entity_id"] == row["project_id"]
    assert p["project_id"] == row["project_id"] and p["host"] == "gitlab.epfl.ch"
    assert p["name"] == "p"
    assert p["description"] == "d"


def test_chunks_skip_when_too_short():
    row = {"project_id": "x", "description": None, "topics": None}
    assert _row_to_chunks(row, chunk_tokens=400, overlap=40, min_card_chars=64) == []


def test_chunks_built_when_enough_text():
    row = {"project_id": "https://gitlab.epfl.ch/g/p",
           "description": "A reasonably long description of a research software project. " * 3,
           "topics": '["ml", "rust"]'}
    chunks = _row_to_chunks(row, chunk_tokens=400, overlap=40, min_card_chars=16)
    assert len(chunks) >= 1
