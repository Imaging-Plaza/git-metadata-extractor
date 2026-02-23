from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from jsonschema import validate

from src.v2.testing.mock_generator import DATASET_KEY_TO_FILE, generate_dataset

MIN_ORG_MEMBERSHIP_PARTS = 2


def test_generate_dataset_is_deterministic_for_same_seed() -> None:
    dataset_a = generate_dataset(seed=42)
    dataset_b = generate_dataset(seed=42)

    assert dataset_a == dataset_b


def test_generate_dataset_changes_when_seed_changes() -> None:
    dataset_a = generate_dataset(seed=42)
    dataset_b = generate_dataset(seed=99)

    assert dataset_a != dataset_b


def test_generated_entities_validate_against_strict_schemas(
    load_schema: Callable[[str, str], dict[str, Any]],
) -> None:
    dataset = generate_dataset(seed=42)

    schema_map = {
        "persons": "person",
        "repositories": "repository",
        "organizations": "organization",
        "memberships": "membership",
        "contributions": "contribution",
        "articles": "article",
    }

    for dataset_key, schema_name in schema_map.items():
        schema = load_schema("strict", schema_name)
        for instance in dataset[dataset_key]:
            validate(instance=instance, schema=schema)


def test_generated_cross_references_are_consistent() -> None:
    dataset = generate_dataset(seed=42)

    person_ids = {person["id"] for person in dataset["persons"]}
    repo_ids = {repository["id"] for repository in dataset["repositories"]}
    org_ids = {organization["id"] for organization in dataset["organizations"]}

    for membership in dataset["memberships"]:
        assert membership["org:organization"] in org_ids
        membership_parts = membership["id"].split("_", maxsplit=1)
        assert len(membership_parts) == MIN_ORG_MEMBERSHIP_PARTS
        assert membership_parts[0] in person_ids

    for contribution in dataset["contributions"]:
        assert contribution["schema:author"] in person_ids
        assert contribution["pulse:contributionTo"] in repo_ids

    for repository in dataset["repositories"]:
        assert set(repository["schema:author"]).issubset(person_ids)
        owned_by = repository.get("pulse:ownedBy")
        if owned_by is not None:
            assert owned_by in person_ids | org_ids

    for article in dataset["articles"]:
        assert set(article["schema:author"]).issubset(person_ids)
        source_org = article.get("schema:sourceOrganization")
        if source_org is not None:
            assert source_org in org_ids


def test_edge_case_generation_contains_boundary_entities() -> None:
    dataset = generate_dataset(seed=42, edge_cases=True)

    assert any(
        person["idSource"] == "uuid"
        and person["pulse:githubUsername"] is None
        and person["pulse:orcidIdentifier"] is None
        and person["pulse:infosciencePersonIdentifier"] is None
        for person in dataset["persons"]
    )
    assert any(
        contribution["pulse:contributionCount"] == 0
        for contribution in dataset["contributions"]
    )
    assert any(
        repository["pulse:isForkOf"] is not None for repository in dataset["repositories"]
    )


def test_cli_writes_dataset_files(tmp_path: Path) -> None:
    output_dir = tmp_path / "generated"

    subprocess.run(
        [
            sys.executable,
            "scripts/v2/generate_mock_data.py",
            "--seed",
            "42",
            "--output",
            str(output_dir),
        ],
        check=True,
        cwd=Path(__file__).resolve().parents[2],
    )

    for file_name in DATASET_KEY_TO_FILE.values():
        output_file = output_dir / file_name
        assert output_file.exists()
        with output_file.open(encoding="utf-8") as file_handle:
            parsed = json.load(file_handle)
        assert isinstance(parsed, list)
