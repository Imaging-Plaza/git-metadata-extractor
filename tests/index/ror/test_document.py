from __future__ import annotations

from src.index.ror.document import display_name, to_document


def _epfl(records):
    return next(r for r in records if r["id"] == "https://ror.org/02s376052")


def test_display_name_picks_ror_display(mini_dump):
    assert display_name(_epfl(mini_dump)) == "École polytechnique fédérale de Lausanne"


def test_to_document_includes_core_fields(mini_dump):
    doc = to_document(_epfl(mini_dump))
    assert "Name: École polytechnique fédérale de Lausanne" in doc
    assert "Acronyms: EPFL" in doc
    assert "Other names: Swiss Federal Institute of Technology Lausanne" in doc
    assert "Types: education, facility" in doc
    assert "Location: Lausanne, Vaud, Switzerland" in doc
    assert "Website: https://www.epfl.ch" in doc
    assert "Relationships: child of Center for Imaging EPFL" in doc


def test_to_document_handles_minimal_record():
    record = {
        "id": "https://ror.org/000aaa000",
        "names": [{"value": "Solo Lab", "types": ["ror_display"]}],
    }
    doc = to_document(record)
    assert doc.startswith("Name: Solo Lab")
    # No acronyms / location / website lines when absent.
    assert "Acronyms" not in doc
    assert "Location" not in doc
    assert "Website" not in doc


def test_to_document_falls_back_to_id_when_unnamed():
    record = {"id": "https://ror.org/000xxx000", "names": []}
    assert to_document(record) == "Name: https://ror.org/000xxx000"
