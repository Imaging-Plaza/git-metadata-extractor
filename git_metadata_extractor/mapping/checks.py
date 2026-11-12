"""Resolve target properties from generated contracts, without a second field list."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from pydantic import BaseModel

from git_metadata_extractor.schema.generated import canonical, raw
from git_metadata_extractor.schema.models import agent

if TYPE_CHECKING:
    from git_metadata_extractor.mapping.models import MappingSpec


def _contracts():
    result = {}
    for namespace, module in [("agent", agent), ("raw", raw), ("canonical", canonical)]:
        for name, model in vars(module).items():
            if (
                not isinstance(model, type)
                or not issubclass(model, BaseModel)
                or model is BaseModel
            ):
                continue
            if namespace == "agent" and not (
                name.startswith("Agent") and name.endswith("Shape")
            ):
                continue
            if namespace != "agent" and not name.endswith("Model"):
                continue
            short = (
                name.removeprefix("Agent")
                .removeprefix("Raw")
                .removesuffix("Shape")
                .removesuffix("Model")
            )
            short = re.sub(r"(?<!^)(?=[A-Z])", "_", short).lower()
            result[f"{namespace}.{short}"] = model
    result["raw.person_profile"] = raw.RawPlatformProfileModel
    return result


CONTRACTS = _contracts()


def _properties(schema, root):
    if "$ref" in schema:
        schema = root["$defs"][schema["$ref"].rsplit("/", 1)[-1]]
    properties = dict(schema.get("properties", {}))
    for branch in schema.get("anyOf", []):
        properties.update(_properties(branch, root))
    return properties


def check_targets(spec: MappingSpec) -> None:
    if spec.target_contract not in CONTRACTS:
        message = f"{spec.name}: unknown target contract {spec.target_contract}"
        raise ValueError(message)
    root = CONTRACTS[spec.target_contract].model_json_schema(by_alias=True)
    for target in spec.fields:
        if target in {"@id", "@type"} and not spec.target_contract.startswith("agent."):
            continue
        schema = root
        for part in target.split("."):
            properties = _properties(schema, root)
            if part not in properties:
                message = f"{spec.name}: undeclared target property {target}"
                raise ValueError(message)
            schema = properties[part]
