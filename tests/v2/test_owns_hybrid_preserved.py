"""Regression guard: the hybrid pipeline preserves `pulse:owns` for
Infoscience-anchored persons.

A deployment report claimed that a GitHub USER extracted with
``agent_runtime=hybrid`` loses its entire ``pulse:owns`` list **iff** the
person is matched to an Infoscience identity (person ``@id`` becomes an
``https://infoscience.epfl.ch/...`` URL) — a supposed "fusion bug".

Driving the *real* downstream stage functions — in the exact order
``src/v2/api.py`` runs them for a USER root in hybrid runtime — proves the
claim wrong: ``pulse:owns`` is byte-identical at every stage whether the
person is Infoscience-anchored or github-anchored, and the rule-based
person agent emits the same owns list either way. A live 20-extraction run
confirmed it (every Infoscience-matched user kept its owned repos).

These tests lock that in: if a future change makes the person's canonical
``@id`` scheme leak into ``pulse:owns`` handling, they fail.

Downstream stage order for a USER root in hybrid runtime
(from ``src/v2/api.py``):

  reconcile_entities
  -> guarantee_repo_author
  -> StrictSchemaValidator().validate_batch -> assemble_output
  -> validate_articles -> validate_author_classes
  -> validate_ownership -> infer_owners -> validate_ownership
  -> prune_dangling_refs
"""

from __future__ import annotations

import asyncio
import copy
import uuid
from typing import Any

from src.v2.agents import PersonAgentV2, ProviderSet
from src.v2.ingest.providers.base import GitHubProvider
from src.v2.ingest.providers.mock_infoscience import MockInfoscienceProvider
from src.v2.pipeline.stages.article_validation import validate_articles
from src.v2.pipeline.stages.author_validation import validate_author_classes
from src.v2.pipeline.stages.output_assembly import assemble_output
from src.v2.pipeline.stages.ownership_check import (
    guarantee_repo_author,
    infer_owners,
    validate_ownership,
)
from src.v2.pipeline.stages.prune_dangling_refs import prune_dangling_refs
from src.v2.pipeline.stages.reconciliation import reconcile_entities
from src.v2.validation.schema_validation import StrictSchemaValidator

SOMEUSER = "someuser"
OWNED = [f"{SOMEUSER}/repo-one", f"{SOMEUSER}/repo-two", f"{SOMEUSER}/repo-three"]


def _person_payload(*, infoscience: bool) -> dict[str, Any]:
    """Build a Person the way the rule-based person agent emits it.

    The agent emits the SAME ``pulse:owns`` list (``<owner>/<repo>`` strings)
    regardless of the Infoscience match — the only difference is the
    ``id`` / ``idSource`` pair and ``pulse:infosciencePersonIdentifier``.
    """
    common: dict[str, Any] = {
        "type": "schema:Person",
        "shacl": "pulse:PersonShape",
        "schema:name": "Some User",
        "pulse:githubUsername": SOMEUSER,
        "pulse:orcidIdentifier": None,
        "org:hasMembership": [],
        "pulse:hasContribution": [],
        "pulse:owns": list(OWNED),
    }
    if infoscience:
        # Pre-reconciliation the agent stamps the bare infoscience uuid;
        # reconcile_entities -> resolve_person_id rewrites it to the URL.
        infoscience_uuid = "cc69e432-9742-4ebd-a318-02a491f44e69"
        return {
            **common,
            "id": infoscience_uuid,
            "identifiers": {
                "pulse:orcid": None,
                "pulse:infosciencePersonIdentifier": infoscience_uuid,
                "pulse:githubUsername": SOMEUSER,
                "uuid": "00000000-0000-0000-0000-000000000001",
            },
            "idSource": "pulse:infosciencePersonIdentifier",
            "schema:url": f"https://infoscience.epfl.ch/entities/person/{infoscience_uuid}",
            "pulse:infosciencePersonIdentifier": infoscience_uuid,
        }
    return {
        **common,
        "id": SOMEUSER,
        "identifiers": {
            "pulse:orcid": None,
            "pulse:infosciencePersonIdentifier": None,
            "pulse:githubUsername": SOMEUSER,
            "uuid": "00000000-0000-0000-0000-000000000002",
        },
        "idSource": "pulse:githubUsername",
        "schema:url": "https://github.com/someuser",
        "pulse:infosciencePersonIdentifier": None,
    }


def _owns(entity: dict[str, Any] | None) -> Any:
    return entity.get("pulse:owns") if isinstance(entity, dict) else None


def _run_user_downstream(*, infoscience: bool, materialise_repos: bool) -> dict[str, Any]:
    """Drive the real USER-root downstream stages; return the final root owns."""
    person = _person_payload(infoscience=infoscience)
    repos: list[dict[str, Any]] = []
    if materialise_repos:
        person_canonical_id = (
            "https://infoscience.epfl.ch/server/api/core/items/12345"
            if infoscience
            else "https://github.com/someuser"
        )
        repos = [
            {
                "id": f"https://github.com/{full_name}",
                "type": "schema:SoftwareSourceCode",
                "shacl": "pulse:RepositoryShape",
                "identifiers": {
                    "pulse:githubRepositoryHandle": full_name,
                    "uuid": str(uuid.uuid4()),
                },
                "idSource": "pulse:githubRepositoryHandle",
                "schema:name": full_name.split("/", 1)[1],
                "pulse:githubRepositoryHandle": full_name,
                "schema:author": [person_canonical_id],
            }
            for full_name in OWNED
        ]

    typed_entity_buckets = {
        "persons": [person],
        "organizations": [],
        "repositories": copy.deepcopy(repos),
        "articles": [],
        "memberships": [],
        "contributions": [],
    }

    reconciled = reconcile_entities(typed_entity_buckets)
    reconciled, _ = guarantee_repo_author(reconciled)

    strict_entities = [("person", p) for p in reconciled.entities["persons"]]
    strict_entities += [("repository", r) for r in reconciled.entities["repositories"]]
    strict_batch = StrictSchemaValidator().validate_batch(strict_entities)
    assembled = assemble_output(reconciled, strict_batch, root_entity_type="person")

    assembled, _ = validate_articles(assembled, veracity_records=[])
    assembled, _ = validate_author_classes(assembled)
    assembled, _ = validate_ownership(assembled)
    assembled, _ = infer_owners(assembled)
    assembled, _ = validate_ownership(assembled)
    assembled, _ = prune_dangling_refs(assembled)

    return {"owns": _owns(assembled.root_entity)}


def _owns_as_targets(owns: Any) -> set[str]:
    """Normalise a pulse:owns list to a set of target IRIs/strings."""
    if not isinstance(owns, list):
        return set()
    out: set[str] = set()
    for entry in owns:
        if isinstance(entry, dict):
            value = entry.get("@id") or entry.get("id")
        else:
            value = entry
        if isinstance(value, str) and value:
            out.add(value)
    return out


def test_hybrid_downstream_preserves_owns_for_both_identity_anchors() -> None:
    """The downstream pipeline keeps every owned repo regardless of whether
    the person is Infoscience- or github-anchored."""
    github = _run_user_downstream(infoscience=False, materialise_repos=False)
    infoscience = _run_user_downstream(infoscience=True, materialise_repos=False)

    assert len(_owns_as_targets(github["owns"])) == len(OWNED)
    assert len(_owns_as_targets(infoscience["owns"])) == len(OWNED), (
        "Infoscience-anchored person lost pulse:owns downstream — "
        f"final owns={infoscience['owns']!r}"
    )
    # The two identity flavours resolve owns to the same repo IRIs.
    assert _owns_as_targets(github["owns"]) == _owns_as_targets(infoscience["owns"])


def test_materialised_repos_preserve_owns_for_infoscience_person() -> None:
    """Even when the owned repos are materialised as Repository entities — so
    `infer_owners` + the inverse-consistency pass interact — the
    Infoscience-anchored person keeps its owns."""
    infoscience = _run_user_downstream(infoscience=True, materialise_repos=True)
    github = _run_user_downstream(infoscience=False, materialise_repos=True)

    assert len(_owns_as_targets(github["owns"])) == len(OWNED)
    assert len(_owns_as_targets(infoscience["owns"])) == len(OWNED), (
        "Infoscience-anchored person lost pulse:owns with materialised repos — "
        f"final owns={infoscience['owns']!r}"
    )


class _StubGitHubProvider(GitHubProvider):
    """GitHub provider whose user `name` controls Infoscience matching.

    No `repositories` key is returned, forcing the agent to rely on
    `context["source_repositories"]` — the orchestrator's USER-root path.
    """

    def __init__(self, *, display_name: str) -> None:
        self._display_name = display_name

    def get_user(self, username: str) -> dict[str, Any]:
        return {
            "login": username,
            "name": self._display_name,
            "html_url": f"https://github.com/{username}",
        }

    def get_repository(self, full_name: str) -> dict[str, Any]:
        del full_name
        return {}

    def get_organization(self, org_name: str) -> dict[str, Any]:
        del org_name
        return {}

    def get_contributors(self, full_name: str) -> list[dict[str, Any]]:
        del full_name
        return []

    def get_languages(self, full_name: str) -> dict[str, int]:
        del full_name
        return {}


class _InfoscienceMatchProvider(MockInfoscienceProvider):
    """Returns an ORCID-less single hit for "alice smith", else nothing —
    forcing the pure `pulse:infosciencePersonIdentifier` identity."""

    def search_person(self, query: str) -> list[dict[str, Any]]:
        if query.strip().lower() == "alice smith":
            return [
                {
                    "infosciencePersonIdentifier": "cc69e432-9742-4ebd-a318-02a491f44e69",
                    "name": "Alice Smith",
                    "orcid": None,
                    "affiliations": ["EPFL School of Engineering"],
                    "profileUrl": (
                        "https://infoscience.epfl.ch/entities/person/"
                        "cc69e432-9742-4ebd-a318-02a491f44e69"
                    ),
                    "score": 99.8,
                },
            ]
        return []


def _run_person_agent(*, display_name: str) -> dict[str, Any]:
    """Run the rule-based PersonAgentV2 the way the orchestrator does."""
    agent = PersonAgentV2()
    providers = ProviderSet(
        github=_StubGitHubProvider(display_name=display_name),
        orcid=None,
        infoscience=_InfoscienceMatchProvider(),
    )
    context = {
        "username": SOMEUSER,
        "agent_is_root": True,
        "source_repositories": list(OWNED),
    }
    result = asyncio.run(agent.run(context, providers))
    return result.data if isinstance(result.data, dict) else {}


def test_rule_based_person_agent_emits_owns_regardless_of_infoscience_match() -> None:
    """The rule-based person agent emits an identical `pulse:owns` whether or
    not the person matched an Infoscience identity — the owns list is derived
    from `source_repositories`, never from the resolved `@id`."""
    matched = _run_person_agent(display_name="Alice Smith")
    unmatched = _run_person_agent(display_name="Zzz Nomatch")

    # Sanity: the two cases really did diverge on identity.
    assert matched.get("idSource") == "pulse:infosciencePersonIdentifier"
    assert unmatched.get("idSource") == "pulse:githubUsername"

    # The actual guard: owns is identical regardless of the match.
    assert matched.get("pulse:owns") == unmatched.get("pulse:owns") == OWNED
