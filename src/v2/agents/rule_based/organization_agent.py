from __future__ import annotations

import re
from copy import deepcopy
from typing import Any

from src.v2.agents.models import (
    AgentResult,
    ProviderSet,
    generate_uuid,
    validate_permissive,
)
from src.v2.ingest.providers.base import ProviderNotFoundError


def _resolve_org_name(context: dict[str, Any]) -> str:
    for key in ("org_name", "organization", "github_organization_handle"):
        value = context.get(key)
        if isinstance(value, str) and value:
            return value
    message = "Organization context is missing a GitHub organization handle"
    raise ValueError(message)


_ORG_TOKEN_RE = re.compile(r"[A-Za-zÀ-ÖØ-öø-ÿ]{2,}")
_ACRONYM_TOKEN_RE = re.compile(r"[A-Za-z]{2,}")


def _org_name_tokens(value: Any) -> set[str]:
    """Lowercase alphabetic tokens (≥2 chars). Splits camelCase and
    snake/kebab variants so `EPFL-Open-Science` and `EPFL Open Science`
    both yield {epfl, open, science}."""
    if not isinstance(value, str):
        return set()
    cleaned = re.sub(r"([a-z])([A-Z])", r"\1 \2", value)
    return {match.group(0).lower() for match in _ORG_TOKEN_RE.finditer(cleaned)}


def _org_record_tokens(record: dict[str, Any]) -> set[str]:
    """Aggregate name + aliases + acronyms into a single token set so
    ROR matches by alias (e.g. `EPFL` for "École Polytechnique Fédérale
    de Lausanne") are recognised."""
    tokens: set[str] = set()
    tokens |= _org_name_tokens(record.get("name"))
    for key in ("aliases", "acronyms", "labels"):
        value = record.get(key)
        if isinstance(value, list):
            for item in value:
                if isinstance(item, str):
                    tokens |= _org_name_tokens(item)
                elif isinstance(item, dict):
                    label = item.get("label") or item.get("value")
                    if isinstance(label, str):
                        tokens |= _org_name_tokens(label)
    return tokens


def _pick_best_orgunit_match(
    results: Any,
    *,
    candidate_name: str | None = None,
) -> dict[str, Any] | None:
    """Accept an Infoscience org-unit match only when the name shares at
    least one ≥2-char alphabetic token with the candidate query.

    Without this, a query like "Statistics Botswana" (forwarded from a
    person's affiliation list with no actual connection to the project)
    can match an arbitrary Infoscience org and put it in the graph. Same
    defensive principle as the person-agent fix in commit 5bc7733.
    """
    if not isinstance(results, list):
        return None
    candidates = [item for item in results if isinstance(item, dict)]
    if not candidates:
        return None
    candidate_tokens = _org_name_tokens(candidate_name)
    if not candidate_tokens:
        # Without any signal to verify against, conservatively skip.
        return None
    for item in candidates:
        if _org_record_tokens(item) & candidate_tokens:
            return item
    return None


# Generic org-structure / weak-content words. A ROR match whose entire
# token overlap with the query is drawn from this set is a coincidental
# collision, not a real identity — production examples: "Center for Digital
# Trust" ↔ "RISM Digital Center", "Imaging-Plaza" ↔ "Kanazawa Education
# Plaza". Such matches are declined so the org flows to the LLM-backed
# `infer_github_handle_parents` selector instead of being finalised here.
_GENERIC_ORG_TOKENS: frozenset[str] = frozenset(
    {
        "center", "centre", "digital", "plaza", "lab", "labs", "laboratory",
        "group", "team", "institute", "institut", "department", "dept",
        "division", "school", "college", "faculty", "unit", "office",
        "foundation", "the", "of", "for", "and", "in", "at", "an", "on",
    },
)


def _select_ror_match(
    ror_matches: list[dict[str, Any]],
    *,
    country_bias: str | None,
    warnings: list[str],
    ror_query: str,
) -> dict[str, Any] | None:
    """Pick a ROR match from ``ror_matches`` with optional country bias
    AND name-token overlap.

    ``country_bias`` is an ISO 3166-1 alpha-2 code. When set, the first
    match whose ``country.country_code`` equals the bias is preferred.

    Acceptance gate: the chosen match must share at least one
    *distinctive* (non-generic) ≥2-char alphabetic token with
    ``ror_query`` (via the record's name, aliases, acronyms, or labels).
    ROR search returns relevance-ranked best-effort matches even for
    queries that have no real ROR counterpart — e.g. a free-text
    affiliation string the indexer spuriously bound to a person — and a
    match resting only on generic org-structure words ("center", "lab",
    "digital", "plaza", …) is a coincidental collision. Without this
    check the top hit gets promoted to the graph as a phantom
    organisation.

    Background: ROR's HTTP search returns the most-cited org first for
    a given query. For acronyms that collide across countries (``SDSC``:
    Swiss Data Science Center vs San Diego Supercomputer Center;
    ``NIH``: Swiss vs US), the top hit is almost always the US one. When
    we're enriching an org that already has an Infoscience match, we
    *know* the Swiss bias is correct.
    """
    if not ror_matches:
        return None

    candidate_tokens = _org_name_tokens(ror_query)

    def _accepts(record: dict[str, Any]) -> bool:
        if not candidate_tokens:
            return False
        overlap = _org_record_tokens(record) & candidate_tokens
        # Require at least one *distinctive* shared token. A match resting
        # only on generic org-structure words ("center", "lab", "digital",
        # "plaza", …) is a coincidental collision; declining it leaves
        # `pulse:ror` null so the agent-backed parent selector decides.
        return any(token not in _GENERIC_ORG_TOKENS for token in overlap)

    if not country_bias:
        top = ror_matches[0]
        if _accepts(top):
            return top
        warnings.append(
            f"ROR top hit for {ror_query!r} ({top.get('id') if isinstance(top, dict) else top}) "
            "shares no name token with the query; skipping enrichment "
            "to avoid a phantom organisation.",
        )
        return None
    biased = next(
        (
            candidate
            for candidate in ror_matches
            if isinstance(candidate, dict)
            and isinstance(candidate.get("country"), dict)
            and candidate["country"].get("country_code") == country_bias
        ),
        None,
    )
    if biased is not None:
        if not _accepts(biased):
            warnings.append(
                f"ROR country-bias hit for {ror_query!r} "
                f"({biased.get('id')}) shares no name token with the query; "
                "skipping enrichment to avoid a phantom organisation.",
            )
            return None
        if biased is not ror_matches[0]:
            top = ror_matches[0]
            top_id = top.get("id") if isinstance(top, dict) else None
            warnings.append(
                f"ROR country-bias applied for {ror_query!r}: "
                f"skipping top hit {top_id} (country != {country_bias}) "
                f"in favour of {biased.get('id')}.",
            )
        return biased
    # No CH-anchored match — fall back to the top hit only if its name
    # tokens overlap with the query; otherwise skip enrichment.
    top = ror_matches[0]
    top_country = (top.get("country") or {}).get("country_code") if isinstance(top, dict) else None
    if not _accepts(top):
        warnings.append(
            f"ROR top hit for {ror_query!r} ({top.get('id') if isinstance(top, dict) else top}) "
            "shares no name token with the query and no country-bias "
            "match exists; skipping enrichment to avoid a phantom organisation.",
        )
        return None
    warnings.append(
        f"ROR country-bias requested ({country_bias}) for {ror_query!r} "
        f"but no match in that country; falling back to top hit "
        f"{top.get('id') if isinstance(top, dict) else None} "
        f"(country={top_country}).",
    )
    return top


def _classify_organization_type(ror_types: list[str]) -> str:
    normalized = [value.lower() for value in ror_types]
    if any("education" in value for value in normalized):
        return "pulse:University"
    if any("government" in value for value in normalized):
        return "pulse:GovernmentAgency"
    if any("nonprofit" in value or "non-profit" in value for value in normalized):
        return "pulse:NonProfitOrganization"
    if any("company" in value for value in normalized):
        return "pulse:PrivateCompany"
    if any("research" in value for value in normalized):
        return "pulse:ResearchInstitution"
    return "pulse:OtherOrganizationType"


class OrganizationAgentV2:
    """Organization agent wrapper with permissive output validation."""

    async def run(  # noqa: C901, PLR0912, PLR0915
        self,
        context: dict[str, Any],
        providers: ProviderSet,
    ) -> AgentResult:
        warnings: list[str] = []
        org_name = _resolve_org_name(context)
        github_lookup_enabled = context.get("github_lookup_enabled")
        if not isinstance(github_lookup_enabled, bool):
            github_lookup_enabled = True

        github_org: dict[str, Any] = {}
        if github_lookup_enabled:
            github_org = providers.github.get_organization(org_name)

        # Profile README (`<org>/.github/profile/README.md`) — the richest
        # free-text "what is this org" text GitHub exposes. Fetched directly
        # alongside the org profile (like `get_organization` above); read by
        # downstream agents, notably the ROR parent selector.
        org_profile_readme: str | None = None
        if github_lookup_enabled:
            try:
                readme = providers.github.get_profile_readme(
                    org_name,
                    is_organization=True,
                )
            except Exception as exc:  # noqa: BLE001
                warnings.append(f"Profile README fetch failed: {exc}")
                readme = ""
            if isinstance(readme, str) and readme.strip():
                org_profile_readme = readme.strip()

        # Infoscience first: when it returns a hit, the org is by definition
        # from the EPFL/Swiss universe and we bias the subsequent ROR lookup
        # toward `country_code = "CH"` to avoid acronym collisions (the
        # canonical example is "SDSC", which top-ranks San Diego Supercomputer
        # Center in ROR's global index instead of the Swiss Data Science
        # Center).
        infoscience_match: dict[str, Any] | None = None
        if providers.infoscience:
            infoscience_query = context.get("infoscience_query") or github_org.get("name") or org_name
            infoscience_results = providers.infoscience.search_orgunit(str(infoscience_query))
            infoscience_match = _pick_best_orgunit_match(
                infoscience_results,
                candidate_name=str(infoscience_query),
            )
            if infoscience_results and infoscience_match is None:
                warnings.append(
                    f"Infoscience orgunit search returned {len(infoscience_results)} hits for "
                    f"{infoscience_query!r} but none share a name token; skipping enrichment.",
                )
        else:
            warnings.append("Infoscience provider not configured for organization enrichment")

        ror_record: dict[str, Any] | None = None
        if providers.ror:
            ror_id_hint = context.get("ror_id")
            try:
                if isinstance(ror_id_hint, str) and ror_id_hint:
                    ror_record = providers.ror.get_organization(ror_id_hint)
                else:
                    ror_query = (
                        context.get("ror_query")
                        or github_org.get("name")
                        or github_org.get("login")
                        or org_name
                    )
                    ror_matches = providers.ror.search_organizations(str(ror_query))
                    if ror_matches:
                        ror_record = _select_ror_match(
                            ror_matches,
                            country_bias=(
                                "CH" if isinstance(infoscience_match, dict) else None
                            ),
                            warnings=warnings,
                            ror_query=str(ror_query),
                        )
            except ProviderNotFoundError as exc:
                warnings.append(f"ROR lookup failed: {exc}")
        else:
            warnings.append("ROR provider not configured for organization enrichment")

        from src.v2.canonicalization.infoscience import infoscience_org_iri

        ror_id = ror_record.get("id") if isinstance(ror_record, dict) else None
        # v2.2.0: stamp Infoscience IDs in canonical URL form
        # (`https://infoscience.epfl.ch/entities/orgunit/<uuid>`). The
        # helper tolerates bare-UUID input.
        infoscience_id = infoscience_org_iri(
            infoscience_match.get("infoscienceOrgUnitIdentifier")
            if isinstance(infoscience_match, dict)
            else None,
        )
        github_handle: str | None = None
        github_login = github_org.get("login")
        if isinstance(github_login, str) and github_login:
            github_handle = github_login
        elif github_lookup_enabled:
            github_handle = org_name
        uuid_value = context.get("uuid")
        if not isinstance(uuid_value, str) or not uuid_value.strip():
            uuid_value = generate_uuid()

        identifier_hierarchy: list[tuple[str, str | None]] = [
            ("pulse:ror", ror_id if isinstance(ror_id, str) else None),
            (
                "pulse:infoscienceOrganizationIdentifier",
                infoscience_id if isinstance(infoscience_id, str) else None,
            ),
            ("pulse:githubOrganizationHandle", github_handle),
            ("uuid", uuid_value),
        ]
        id_source, resolved_id = next(
            (identifier, value)
            for identifier, value in identifier_hierarchy
            if value
        )

        ror_types = []
        if isinstance(ror_record, dict) and isinstance(ror_record.get("types"), list):
            ror_types = [item for item in ror_record["types"] if isinstance(item, str)]

        organization_type = _classify_organization_type(ror_types)
        resolved_name = (
            (ror_record or {}).get("name")
            or (infoscience_match or {}).get("name")
            or github_org.get("name")
            or org_name
        )
        parent_org = None
        has_units: list[str] = []
        if isinstance(ror_record, dict):
            relationships = ror_record.get("relationships")
            if isinstance(relationships, dict):
                parent_payload = relationships.get("parent")
                if isinstance(parent_payload, dict):
                    parent_id = parent_payload.get("id")
                    if isinstance(parent_id, str):
                        parent_org = parent_id
                children_payload = relationships.get("children")
                if isinstance(children_payload, list):
                    for child in children_payload:
                        if not isinstance(child, dict):
                            continue
                        child_id = child.get("id")
                        if isinstance(child_id, str):
                            has_units.append(child_id)

        if not parent_org and isinstance(infoscience_match, dict):
            parent_candidate = infoscience_match.get("parentOrganization")
            if isinstance(parent_candidate, str) and parent_candidate:
                parent_org = parent_candidate

        repositories = context.get("repositories")
        source_repositories = context.get("source_repositories")
        owns: list[str] = []
        if isinstance(source_repositories, list):
            owns = [value for value in source_repositories if isinstance(value, str) and value]
        elif isinstance(repositories, list):
            owns = [value for value in repositories if isinstance(value, str) and value]
        elif github_lookup_enabled and isinstance(github_org.get("repositories"), list) and github_handle:
            owns = [
                f"{github_handle}/{repo_name}"
                for repo_name in github_org["repositories"]
                if isinstance(repo_name, str) and repo_name
            ]

        # Build the alias set from the ROR record + the original search
        # query. The lookup phase in `membership_agent` indexes orgs by
        # `aliases` so any text we feed here becomes a valid resolution key.
        # Without this, an ORCID-derived employment at "Aalto-yliopisto"
        # resolves to ROR "Aalto University" but the membership agent can
        # only see the canonical name and emits "Unresolved membership
        # organization mapping" — losing the affiliation.
        ror_aliases: list[str] = []
        ror_acronyms: list[str] = []
        ror_labels: list[Any] = []
        if isinstance(ror_record, dict):
            raw_aliases = ror_record.get("aliases")
            if isinstance(raw_aliases, list):
                ror_aliases = [v for v in raw_aliases if isinstance(v, str) and v]
            raw_acronyms = ror_record.get("acronyms")
            if isinstance(raw_acronyms, list):
                ror_acronyms = [v for v in raw_acronyms if isinstance(v, str) and v]
            raw_labels = ror_record.get("labels")
            if isinstance(raw_labels, list):
                ror_labels = [v for v in raw_labels if v]

        # Always add the original search-string as an alias when the
        # canonical name differs — preserves the upstream label that was
        # used to find this org.
        merged_aliases: list[str] = list(ror_aliases)
        if isinstance(org_name, str) and org_name and org_name != resolved_name:
            if org_name not in merged_aliases:
                merged_aliases.append(org_name)

        payload = {
            "id": resolved_id,
            "type": "org:Organization",
            "shacl": "pulse:OrganizationShape",
            "identifiers": {
                "pulse:ror": ror_id if isinstance(ror_id, str) else None,
                "pulse:infoscienceOrganizationIdentifier": (
                    infoscience_id if isinstance(infoscience_id, str) else None
                ),
                "pulse:githubOrganizationHandle": github_handle,
                "uuid": uuid_value,
            },
            "idSource": id_source,
            "schema:name": (
                resolved_name
            ),
            "schema:identifier": ror_id if isinstance(ror_id, str) else None,
            "pulse:githubOrganizationHandle": github_handle,
            "pulse:infoscienceOrganizationIdentifier": (
                infoscience_id if isinstance(infoscience_id, str) else None
            ),
            "pulse:OrganizationType": organization_type,
            "pulse:githubOrgFollowers": github_org.get("followers"),
            "org:hasUnit": has_units,
            "org:unitOf": [parent_org] if isinstance(parent_org, str) and parent_org else [],
            "pulse:owns": owns,
            # Internal lookup keys (`_`-prefix is stripped before strict
            # validation and JSON-LD output by default; surfaced when
            # the caller passes `?include_internal_fields=true`). Used
            # by `membership_agent` to resolve `org:hasMembership`
            # composite IDs and by downstream consumers / LLM refiners
            # to access the rich GitHub-org + ROR + Infoscience
            # metadata that the v2 ontology doesn't yet model.
            "_aliases": merged_aliases,
            "_acronyms": ror_acronyms,
            "_labels": ror_labels,
            # GitHub organization profile (when the org has a GitHub presence).
            "_avatar_url":         github_org.get("avatar_url"),
            "_html_url":           github_org.get("html_url"),
            "_blog":               github_org.get("blog"),
            "_description":        github_org.get("description"),
            "_company":            github_org.get("company"),
            "_location":           github_org.get("location"),
            "_profile_readme":     org_profile_readme,
            "_email":              github_org.get("email"),
            "_twitter_username":   github_org.get("twitter_username"),
            "_public_repos":       github_org.get("public_repos"),
            "_followers_count":    github_org.get("followers"),
            "_github_created_at":  github_org.get("created_at"),
            "_github_updated_at":  github_org.get("updated_at"),
            "_github_account_type": github_org.get("type"),
            # GitHub trust + activity signals we previously dropped on the
            # floor. `is_verified` is GitHub's domain-ownership verification
            # flag — when true, GitHub has confirmed the org owns the
            # domain(s) listed on its profile, and the downstream
            # `org_resolver` can short-circuit (the org is real, the
            # ROR lookup is the only remaining question). `archived_at`
            # is the org-level archival timestamp (distinct from per-repo
            # `_archived`); pruning rollups against this is much cheaper
            # than scanning all repos.
            "_is_verified":            github_org.get("is_verified"),
            "_archived_at":            github_org.get("archived_at"),
            "_public_gists":           github_org.get("public_gists"),
            "_following_count":        github_org.get("following"),
            "_has_organization_projects": github_org.get("has_organization_projects"),
            "_has_repository_projects":   github_org.get("has_repository_projects"),
            # ROR profile extras.
            "_ror_country":        ror_record.get("country") if isinstance(ror_record, dict) else None,
            "_ror_types":          ror_record.get("types") if isinstance(ror_record, dict) else None,
            "_ror_status":         ror_record.get("status") if isinstance(ror_record, dict) else None,
            "_ror_established":    ror_record.get("established") if isinstance(ror_record, dict) else None,
            "_ror_links":          ror_record.get("links") if isinstance(ror_record, dict) else None,
            # Infoscience profile extras (uses the new structured columns
            # we backfilled: acronym, infoscience_code, unit_code,
            # parent_acronym, director_name, org_type_dspace).
            "_infoscience_code":         (
                infoscience_match.get("infoscience_code") if isinstance(infoscience_match, dict) else None
            ),
            "_unit_code":                (
                infoscience_match.get("unit_code") if isinstance(infoscience_match, dict) else None
            ),
            "_parent_acronym":           (
                infoscience_match.get("parent_acronym") if isinstance(infoscience_match, dict) else None
            ),
            "_director_name":            (
                infoscience_match.get("director_name") if isinstance(infoscience_match, dict) else None
            ),
            "_org_type_dspace":          (
                infoscience_match.get("org_type_dspace") if isinstance(infoscience_match, dict) else None
            ),
            "_infoscience_url":          (
                infoscience_match.get("infoscience_url") if isinstance(infoscience_match, dict) else None
            ),
        }

        overrides = context.get("agent_overrides")
        if isinstance(overrides, dict):
            payload.update(overrides)

        raw_output = deepcopy(payload)
        validated_payload, validation_warnings = validate_permissive(
            payload,
            schema_name="organization",
        )
        warnings.extend(validation_warnings)

        derivation_stats = {
            "organization_id": validated_payload.get("id"),
            "organization_name": validated_payload.get("schema:name"),
            "github_lookup_enabled": github_lookup_enabled,
            "source_repositories": (
                deepcopy(source_repositories)
                if isinstance(source_repositories, list)
                else []
            ),
            "owned_repositories": deepcopy(owns),
            "parent_organization": parent_org,
            "unit_ids": deepcopy(has_units),
            "ror_types": deepcopy(ror_types),
        }

        return AgentResult(
            data=validated_payload,
            warnings=warnings,
            raw_output=raw_output,
            stats={"derivation": derivation_stats},
        )
