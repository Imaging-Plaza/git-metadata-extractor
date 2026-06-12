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


def test_description_less_project_not_skipped_with_name_and_full_path():
    # Bug 09: a public project with no description and no topics, whose
    # project_id alone is under min_card_chars (64), was silently skipped.
    # name + full_path must now push it over the threshold and into the card.
    project_id = "https://gl.epfl.ch/g/p"  # 22 chars — under 64 on its own
    assert len(project_id) < 64  # noqa: PLR2004 — would have been skipped pre-fix
    row = {
        "project_id": project_id,
        "name": "imaging-analysis-toolkit",
        "full_path": "imaging/group/imaging-analysis-toolkit",
        "description": None,
        "topics": None,
    }
    chunks = _row_to_chunks(row, chunk_tokens=400, overlap=40, min_card_chars=64)
    assert len(chunks) >= 1
    text = "\n".join(c.text for c in chunks)
    assert "imaging-analysis-toolkit" in text
    assert "imaging/group/imaging-analysis-toolkit" in text


def test_still_skips_when_even_name_and_full_path_too_short():
    # Regression: genuinely tiny cards still skip (threshold still enforced).
    row = {"project_id": "x", "name": "y", "full_path": "z",
           "description": None, "topics": None}
    assert _row_to_chunks(row, chunk_tokens=400, overlap=40, min_card_chars=64) == []
