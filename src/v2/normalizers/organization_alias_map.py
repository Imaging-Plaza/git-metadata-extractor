from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import TYPE_CHECKING, Any, Literal

from src.v2.normalizers.id_resolution import resolve_organization_id
from src.v2.normalizers.string_utils import normalize_string as _normalize_string

if TYPE_CHECKING:
    from src.v2.agents import ProviderSet
    from src.v2.graph.store import GraphStore

AliasSourceName = Literal["ror", "agent", "manual", "derived"]

HIGH_CONFIDENCE_THRESHOLD = 0.9
LOW_CONFIDENCE_THRESHOLD = 0.5


@dataclass(frozen=True, slots=True)
class AliasResolution:
    canonical_id: str
    confidence: float
    source: AliasSourceName
    is_new_entity: bool


@dataclass(frozen=True, slots=True)
class _CandidateMatch:
    canonical_id: str
    confidence: float
    source: AliasSourceName
    name: str | None = None
    aliases: tuple[str, ...] = ()


class OrganizationAliasResolver:
    def __init__(
        self,
        store: GraphStore,
        *,
        high_confidence_threshold: float = HIGH_CONFIDENCE_THRESHOLD,
        low_confidence_threshold: float = LOW_CONFIDENCE_THRESHOLD,
    ) -> None:
        self._store = store
        self._high_confidence_threshold = high_confidence_threshold
        self._low_confidence_threshold = low_confidence_threshold

    @staticmethod
    def normalize_string(value: str) -> str:
        return _normalize_string(value)

    @staticmethod
    def score_candidate(
        normalized_input: str,
        candidate_name: str,
        candidate_aliases: list[str] | tuple[str, ...],
    ) -> float:
        if not normalized_input:
            return 0.0

        candidates = [candidate_name, *candidate_aliases]
        normalized_candidates = [item for item in (_normalize_string(name) for name in candidates) if item]
        if not normalized_candidates:
            return 0.0

        input_tokens = set(normalized_input.split())
        best_score = 0.0
        for candidate in normalized_candidates:
            if candidate == normalized_input:
                return 1.0

            candidate_tokens = set(candidate.split())
            token_overlap = 0.0
            if input_tokens and candidate_tokens:
                token_overlap = len(input_tokens & candidate_tokens) / max(
                    len(input_tokens),
                    len(candidate_tokens),
                )

            sequence_score = SequenceMatcher(None, normalized_input, candidate).ratio()
            candidate_score = max(sequence_score, token_overlap)
            if candidate.startswith(normalized_input) or normalized_input.startswith(candidate):
                candidate_score = min(1.0, candidate_score + 0.05)

            best_score = max(best_score, candidate_score)

        return round(best_score, 4)

    def resolve(self, org_name: str, providers: ProviderSet) -> AliasResolution:
        normalized_input = self.normalize_string(org_name)
        if not normalized_input:
            message = "Organization name must contain at least one non-whitespace character"
            raise ValueError(message)

        direct_alias = self._store.lookup_alias(org_name)
        if direct_alias is not None:
            return AliasResolution(
                canonical_id=direct_alias.canonical_entity_id,
                confidence=direct_alias.confidence,
                source=direct_alias.source,
                is_new_entity=False,
            )

        normalized_alias_hit = self._lookup_alias_by_normalized(normalized_input)
        if normalized_alias_hit is not None:
            return normalized_alias_hit

        best_existing = self._best_existing_match(normalized_input)
        best_ror = self._best_ror_match(org_name, normalized_input, providers)
        best_match = self._select_best(best_existing, best_ror)

        if best_match is not None and best_match.confidence >= self._high_confidence_threshold:
            if best_match.source == "ror":
                self._upsert_ror_entity(best_match)
            self._store_alias(
                alias_string=org_name,
                canonical_id=best_match.canonical_id,
                confidence=best_match.confidence,
                source=best_match.source,
            )
            return AliasResolution(
                canonical_id=best_match.canonical_id,
                confidence=best_match.confidence,
                source=best_match.source,
                is_new_entity=False,
            )

        if best_match is not None and best_match.confidence >= self._low_confidence_threshold:
            self._store_alias(
                alias_string=org_name,
                canonical_id=best_match.canonical_id,
                confidence=best_match.confidence,
                source=best_match.source,
            )
            return AliasResolution(
                canonical_id=best_match.canonical_id,
                confidence=best_match.confidence,
                source=best_match.source,
                is_new_entity=False,
            )

        return self._create_new_entity(org_name)

    def _lookup_alias_by_normalized(self, normalized_input: str) -> AliasResolution | None:
        for organization in self._store.get_entities_by_type("organization"):
            aliases = self._store.get_aliases_for_entity(organization.id)
            for alias in aliases:
                if self.normalize_string(alias.alias_string) != normalized_input:
                    continue
                return AliasResolution(
                    canonical_id=organization.id,
                    confidence=alias.confidence,
                    source=alias.source,
                    is_new_entity=False,
                )
        return None

    def _best_existing_match(self, normalized_input: str) -> _CandidateMatch | None:
        best: _CandidateMatch | None = None
        for organization in self._store.get_entities_by_type("organization"):
            candidate_name = self._organization_name(organization.data)
            aliases = [alias.alias_string for alias in self._store.get_aliases_for_entity(organization.id)]
            score = self.score_candidate(normalized_input, candidate_name, aliases)
            candidate = _CandidateMatch(
                canonical_id=organization.id,
                confidence=score,
                source="derived",
                name=candidate_name,
                aliases=tuple(aliases),
            )
            if best is None or candidate.confidence > best.confidence:
                best = candidate
        return best

    def _best_ror_match(
        self,
        org_name: str,
        normalized_input: str,
        providers: ProviderSet,
    ) -> _CandidateMatch | None:
        if providers.ror is None:
            return None

        search_results = providers.ror.search_organizations(org_name)
        best: _CandidateMatch | None = None
        for result in search_results:
            candidate_name = str(result.get("name", ""))
            candidate_aliases = _extract_ror_aliases(result)
            score = self.score_candidate(
                normalized_input,
                candidate_name,
                candidate_aliases,
            )
            canonical_id, _ = resolve_organization_id(
                {"identifiers": {"pulse:ror": result.get("id")}},
            )
            candidate = _CandidateMatch(
                canonical_id=canonical_id,
                confidence=score,
                source="ror",
                name=candidate_name,
                aliases=tuple(candidate_aliases),
            )
            if best is None or candidate.confidence > best.confidence:
                best = candidate
        return best

    @staticmethod
    def _select_best(
        left: _CandidateMatch | None,
        right: _CandidateMatch | None,
    ) -> _CandidateMatch | None:
        if left is None:
            return right
        if right is None:
            return left
        return left if left.confidence >= right.confidence else right

    def _upsert_ror_entity(self, candidate: _CandidateMatch) -> None:
        existing = self._store.get_entity(candidate.canonical_id)
        if existing is not None:
            return

        entity_name = candidate.name or candidate.canonical_id
        aliases_payload = [alias for alias in candidate.aliases if alias]
        entity_data = {
            "schema:name": entity_name,
            "name": entity_name,
            "aliases": aliases_payload,
        }
        identifiers = {"ror": candidate.canonical_id}
        self._store.insert_entity(
            entity_type="organization",
            entity_id=candidate.canonical_id,
            data=entity_data,
            identifiers=identifiers,
            id_source="ror",
        )

    def _create_new_entity(self, org_name: str) -> AliasResolution:
        canonical_id, id_source = resolve_organization_id(
            {
                "schema:name": org_name,
                "identifiers": {},
            },
        )
        is_new_entity = self._store.get_entity(canonical_id) is None
        if is_new_entity:
            self._store.insert_entity(
                entity_type="organization",
                entity_id=canonical_id,
                data={"schema:name": org_name, "name": org_name},
                identifiers={},
                id_source=id_source,
            )
        self._store_alias(
            alias_string=org_name,
            canonical_id=canonical_id,
            confidence=1.0,
            source="agent",
        )
        return AliasResolution(
            canonical_id=canonical_id,
            confidence=0.0,
            source="agent",
            is_new_entity=is_new_entity,
        )

    def _store_alias(
        self,
        *,
        alias_string: str,
        canonical_id: str,
        confidence: float,
        source: AliasSourceName,
    ) -> None:
        self._store.insert_alias(
            alias_string=alias_string,
            canonical_entity_id=canonical_id,
            confidence=confidence,
            source=source,
        )

    @staticmethod
    def _organization_name(data: dict[str, Any]) -> str:
        for key in ("schema:name", "name", "label"):
            value = data.get(key)
            if isinstance(value, str) and value.strip():
                return value
        return ""


def _extract_ror_aliases(payload: dict[str, Any]) -> list[str]:
    aliases: list[str] = []
    for key in ("aliases", "acronyms"):
        value = payload.get(key)
        if isinstance(value, list):
            aliases.extend(str(item) for item in value if isinstance(item, str))
    labels = payload.get("labels")
    if isinstance(labels, list):
        for label_payload in labels:
            if not isinstance(label_payload, dict):
                continue
            label = label_payload.get("label")
            if isinstance(label, str):
                aliases.append(label)
    return aliases
