from __future__ import annotations

from src.v2.ingest.github_accounts.models import Person
from src.v2.pipeline.stages.privacy import anonymize_email
from src.v2.pipeline.stages.reconciliation import reconcile_entities


def _v1_anonymized_email(email: str) -> str:
    person = Person(name="Example User", emails=[email])
    assert person.emails is not None
    return person.emails[0]


def _person_with_email(email: str) -> dict[str, object]:
    return {
        "schema:name": "Example User",
        "schema:email": email,
        "pulse:githubUsername": "example-user",
        "identifiers": {
            "pulse:orcid": None,
            "pulse:infosciencePersonIdentifier": None,
            "pulse:githubUsername": "example-user",
            "uuid": "e3c8e4f8-8c9f-4f74-a857-73feefc57dd2",
        },
    }


def test_anonymize_email_matches_v1_hashing_logic() -> None:
    raw_email = "user@example.com"
    assert anonymize_email(raw_email) == _v1_anonymized_email(raw_email)


def test_anonymize_email_is_deterministic() -> None:
    raw_email = "deterministic@example.com"
    assert anonymize_email(raw_email) == anonymize_email(raw_email)


def test_anonymize_email_preserves_domain_and_hides_local_part() -> None:
    raw_email = "privacy@example.com"
    anonymized = anonymize_email(raw_email)
    assert anonymized.endswith("@example.com")
    assert not anonymized.startswith("privacy@")


def test_anonymize_email_does_not_double_hash_pre_anonymized_values() -> None:
    first_pass = anonymize_email("already@example.com")
    assert anonymize_email(first_pass) == first_pass


def test_reconciliation_anonymizes_person_email_in_output() -> None:
    reconciled = reconcile_entities(
        {
            "persons": [_person_with_email("visible@example.com")],
            "organizations": [],
            "repositories": [],
        },
    )

    person = reconciled.entities["persons"][0]
    assert person["schema:email"] == anonymize_email("visible@example.com")
    assert person["schema:email"] != "visible@example.com"
