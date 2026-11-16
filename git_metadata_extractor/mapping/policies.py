"""Typed ontology routing, identity and classification policies."""

from __future__ import annotations

from functools import lru_cache
from string import Formatter
from typing import Literal

from pydantic import Field, model_validator

from git_metadata_extractor.mapping.checks import CONTRACTS
from git_metadata_extractor.models import DataModel
from git_metadata_extractor.schema.generated import canonical, raw
from git_metadata_extractor.validation.json_schema import schema_validator


class PolicyHeader(DataModel, frozen=True):
    schema_version: Literal[1] = 1
    name: str
    version: str = Field(min_length=1)


class PlatformRule(DataModel, frozen=True):
    platform: str
    username_index: int
    default_host: bool = True


class ResolverRule(DataModel, frozen=True):
    match_keys: list[str]
    id_priority: list[str] = Field(default_factory=list)
    same_as: str | None = None
    legacy_match_contracts: dict[str, str] = Field(default_factory=dict)


def _properties(models):
    return {
        field.alias or name
        for model in models.values()
        for name, field in model.model_fields.items()
    }


def _known(values, allowed, label):
    if unknown := set(values) - set(allowed):
        message = f"{label}: undeclared terms {sorted(unknown)}"
        raise ValueError(message)


def _enum_value(contract, field, value):
    schema = CONTRACTS[contract].model_json_schema(by_alias=True)
    validator = schema_validator(
        {"$defs": schema.get("$defs", {}), **schema["properties"][field]},
    )
    if not validator.is_valid(value):
        message = f"{contract}.{field}: invalid policy value {value}"
        raise ValueError(message)


class OntologyPolicy(PolicyHeader):
    name: Literal["policies.ontology"] = "policies.ontology"
    raw_passthrough: dict[str, list[str]]
    canonical_passthrough: dict[str, list[str]]
    iri_properties: list[str]
    platforms: dict[str, PlatformRule]
    registry_platforms: dict[str, str]
    source_by_property: dict[str, str]
    profile_ref_properties: list[str]
    source_url_fields: list[str]
    index_backed_platforms: list[str]
    part_of_run_types: list[str]

    @model_validator(mode="after")
    def declared_terms(self):
        for table, models in [
            (self.raw_passthrough, raw.MODELS_BY_TARGET_CLASS),
            (self.canonical_passthrough, canonical.MODELS_BY_TARGET_CLASS),
        ]:
            _known(table, models, "passthrough types")
            for kind, fields in table.items():
                _known(fields, _properties({kind: models[kind]}), kind)
        raw_fields = _properties(raw.MODELS_BY_TARGET_CLASS)
        _known(self.iri_properties, raw_fields, "IRI properties")
        _known(self.source_by_property, raw_fields, "source attribution")
        _known(self.profile_ref_properties, raw_fields, "profile references")
        _known(
            self.source_url_fields,
            _properties(
                {
                    name: model
                    for name, model in CONTRACTS.items()
                    if name.startswith("agent.")
                },
            ),
            "source URL fields",
        )
        expected = {
            kind
            for kind, model in raw.MODELS_BY_TARGET_CLASS.items()
            if "pulse:partOfRun" in {f.alias for f in model.model_fields.values()}
        }
        if set(self.part_of_run_types) != expected:
            message = "part_of_run_types must match the raw ontology shapes"
            raise ValueError(message)
        for platform in [
            *(p.platform for p in self.platforms.values()),
            *self.registry_platforms.values(),
            *self.source_by_property.values(),
            *self.index_backed_platforms,
        ]:
            _enum_value("raw.repository", "pulse:platform", platform)
        return self


class MergeRules(PolicyHeader):
    name: Literal["policies.merge"] = "policies.merge"
    per_run_properties: list[str]
    dispositions: dict[str, Literal["union", "select", "per_run", "max", "min"]]
    resolvers: dict[str, ResolverRule]
    iri_templates: dict[str, str]

    @model_validator(mode="after")
    def declared_terms(self):
        models = canonical.MODELS_BY_TARGET_CLASS
        fields = _properties(models)
        _known(self.dispositions, fields, "merge dispositions")
        _known(self.resolvers, models, "merge resolver types")
        _known(
            self.per_run_properties,
            _properties(raw.MODELS_BY_TARGET_CLASS),
            "per-run properties",
        )
        _known(self.iri_templates, fields, "identity templates")
        uncapped = set().union(
            *(
                _properties({kind: model})
                - canonical.SINGLE_VALUED_BY_TARGET_CLASS[kind]
                for kind, model in models.items()
            ),
        )
        _known(uncapped, self.dispositions, "unclassified merge properties")
        if any(
            value == "union"
            for key, value in self.dispositions.items()
            if key not in uncapped
        ):
            message = "Always-capped properties cannot use union"
            raise ValueError(message)
        for kind, rule in self.resolvers.items():
            entity_fields = _properties({kind: models[kind]})
            _known(rule.id_priority, entity_fields, f"{kind} identity fields")
            _known(rule.legacy_match_contracts, rule.match_keys, "legacy match fields")
            for field, contract in rule.legacy_match_contracts.items():
                _known([contract], CONTRACTS, "legacy match contracts")
                _known([field], _properties({contract: CONTRACTS[contract]}), contract)
            _known(
                rule.match_keys,
                entity_fields | rule.legacy_match_contracts.keys(),
                f"{kind} match fields",
            )
            _known(rule.id_priority, self.iri_templates, "identity template coverage")
            if rule.id_priority and rule.match_keys[:1] != rule.id_priority[:1]:
                message = f"{kind}: highest-priority identity must be matched first"
                raise ValueError(message)
        for template in self.iri_templates.values():
            parts = list(Formatter().parse(template))
            if [(f, s, c) for _, f, s, c in parts if f is not None] != [("", "", None)]:
                message = "Identity templates require exactly one plain {} placeholder"
                raise ValueError(message)
        return self


class ClassificationRule(DataModel, frozen=True):
    value: str
    keywords: list[str] = Field(min_length=1)


class ExtractionPolicy(PolicyHeader):
    name: Literal["policies.extraction"] = "policies.extraction"
    repository_rules: list[ClassificationRule]
    documentation_languages: list[str]
    documentation_type: str
    repository_default: str
    auxiliary_files: dict[str, list[str]]
    organization_rules: list[ClassificationRule]
    organization_default: str

    @model_validator(mode="after")
    def valid_classifications(self):
        for value in [
            *(r.value for r in self.repository_rules),
            self.documentation_type,
            self.repository_default,
        ]:
            _enum_value("agent.repository", "pulse:repositoryType", value)
        for value in [
            *(r.value for r in self.organization_rules),
            self.organization_default,
        ]:
            _enum_value("agent.organization", "pulse:OrganizationType", value)
        if any(not key.startswith("_") for key in self.auxiliary_files):
            message = "Auxiliary file targets must be internal metadata"
            raise ValueError(message)
        return self


POLICIES = {
    "policies.ontology": OntologyPolicy,
    "policies.merge": MergeRules,
    "policies.extraction": ExtractionPolicy,
}


@lru_cache(maxsize=3)
def _load_policy(name):
    # Load lazily: the engine uses these policy models when building its manifest.
    from git_metadata_extractor.mapping.engine import (  # noqa: PLC0415
        CONFIG_ROOT,
        UniqueKeyLoader,
    )

    if name not in POLICIES:
        message = f"Unknown policy {name}"
        raise ValueError(message)
    path = CONFIG_ROOT.joinpath(*name.split(".")).with_suffix(".yaml")
    loader = UniqueKeyLoader(path.read_text(encoding="utf-8"))
    try:
        return POLICIES[name].model_validate(loader.get_single_data())
    finally:
        loader.dispose()


def load_policy(name):
    return _load_policy(name).model_copy(deep=True)
