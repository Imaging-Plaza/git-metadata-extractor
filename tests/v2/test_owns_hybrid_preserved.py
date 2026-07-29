"""Regression guard: the hybrid pipeline preserves `pulse:owns` for
Infoscience-anchored persons.

A deployment report claimed that a GitHub USER extracted with
``agent_runtime=hybrid`` loses its entire ``pulse:owns`` list **iff** the
person is matched to an Infoscience identity (person ``@id`` becomes an
``https://infoscience.epfl.ch/...`` URL) — a supposed "fusion bug".

Driving the *real* downstream stage functions — in the exact order
``git_metadata_extractor/api.py`` runs them for a USER root in hybrid runtime — proves the
claim wrong: ``pulse:owns`` is byte-identical at every stage whether the
person is Infoscience-anchored or github-anchored, and the rule-based
person agent emits the same owns list either way. A live 20-extraction run
confirmed it (every Infoscience-matched user kept its owned repos).

These tests lock that in: if a future change makes the person's canonical
``@id`` scheme leak into ``pulse:owns`` handling, they fail.

Downstream stage order for a USER root in hybrid runtime
(from ``git_metadata_extractor/api.py``):

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

from git_metadata_extractor.agents import PersonAgentV2, ProviderSet
from git_metadata_extractor.providers.base import GitHubProvider
from git_metadata_extractor.providers.mock_infoscience import MockInfoscienceProvider
from git_metadata_extractor.pipeline.stages.article_validation import validate_articles
from git_metadata_extractor.pipeline.stages.author_validation import validate_author_classes
from git_metadata_extractor.pipeline.stages.output_assembly import assemble_output
from git_metadata_extractor.pipeline.stages.ownership_check import (
    guarantee_repo_author,
    infer_owners,
    validate_ownership,
)
from git_metadata_extractor.pipeline.stages.prune_dangling_refs import prune_dangling_refs
from git_metadata_extractor.pipeline.stages.reconciliation import reconcile_entities
from git_metadata_extractor.validation.schema_validation import StrictSchemaValidator

SOMEUSER = "someuser"
OWNED = [
    f"https://github.com/{SOMEUSER}/repo-one",
    f"https://github.com/{SOMEUSER}/repo-two",
    f"https://github.com/{SOMEUSER}/repo-three",
]


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
        infoscience_uuid = "cc69e432-9742-4ebd-a318-02a491f44e69"
        person_canonical_id = (
            f"https://infoscience.epfl.ch/entities/person/{infoscience_uuid}"
            if infoscience
            else "https://github.com/someuser"
        )
        repos = [
            {
                "id": full_name,
                "type": "schema:SoftwareSourceCode",
                "shacl": "pulse:RepositoryShape",
                "identifiers": {
                    "pulse:githubRepositoryHandle": full_name,
                    "uuid": str(uuid.uuid4()),
                },
                "idSource": "pulse:githubRepositoryHandle",
                "schema:name": full_name.rsplit("/", 1)[1],
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

    root = assembled.root_entity
    owned_internal = root.get("_owned_repositories") if isinstance(root, dict) else None
    return {"owns": _owns(root), "owned_internal": owned_internal}


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
    """Un-materialised owned repos are preserved across both identity
    anchors — without dangling the SHACL ``sh:class`` constraint.

    These repos are NOT materialised as ``schema:SoftwareSourceCode``
    nodes, so keeping them in public ``pulse:owns`` would fail
    ``sh:class``. Per the "fits the schema → public, else → internal"
    rule, the real GitHub data moves to the internal
    ``_owned_repositories`` field instead of being dropped. No data is
    lost and both anchors agree.
    """
    github = _run_user_downstream(infoscience=False, materialise_repos=False)
    infoscience = _run_user_downstream(infoscience=True, materialise_repos=False)

    # Public pulse:owns is empty — the repos aren't typed nodes here.
    assert _owns_as_targets(github["owns"]) == set()
    assert _owns_as_targets(infoscience["owns"]) == set()

    # …but the ownership data is preserved on the internal field, intact
    # and identical across both identity flavours.
    assert _owns_as_targets(github["owned_internal"]) == _owns_as_targets(OWNED)
    assert _owns_as_targets(infoscience["owned_internal"]) == _owns_as_targets(OWNED), (
        "Infoscience-anchored person lost owned repos downstream — "
        f"_owned_repositories={infoscience['owned_internal']!r}"
    )
    assert _owns_as_targets(github["owned_internal"]) == _owns_as_targets(
        infoscience["owned_internal"],
    )


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


def test_prune_dangling_refs_splits_owns_live_vs_external() -> None:
    """prune_dangling_refs keeps live in-graph repos in public `pulse:owns`,
    moves real external repo IRIs to the internal `_owned_repositories`,
    and drops mangled (non-IRI) refs."""
    from git_metadata_extractor.pipeline.stages.models import AssembledOutput
    from git_metadata_extractor.pipeline.stages.prune_dangling_refs import INTERNAL_OWNS_KEY

    repo = {
        "id": "https://github.com/pallets/click",
        "type": "schema:SoftwareSourceCode",
        "pulse:ownedBy": "https://github.com/pallets",  # anchors the org
    }
    org = {
        "id": "https://github.com/pallets",
        "type": "org:Organization",
        "pulse:owns": [
            "https://github.com/pallets/click",   # live node → public
            "https://github.com/pallets/flask",   # real external → internal
            "not-an-iri",                          # mangled → dropped
        ],
    }
    out, _ = prune_dangling_refs(
        AssembledOutput(
            root_entity=repo, related_entities=[org],
            excluded_entities=[], warnings=[],
        ),
    )
    pallets = next(e for e in out.related_entities if e["id"] == "https://github.com/pallets")
    assert pallets["pulse:owns"] == ["https://github.com/pallets/click"]
    assert pallets[INTERNAL_OWNS_KEY] == ["https://github.com/pallets/flask"]
    # The mangled ref is gone from both public and internal.
    assert "not-an-iri" not in (pallets.get(INTERNAL_OWNS_KEY) or [])
