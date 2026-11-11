"""Load packaged YAML and apply field rules without mutating source records."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from copy import deepcopy
from functools import lru_cache
from hashlib import sha256
from pathlib import Path
from typing import TYPE_CHECKING

import yaml

from git_metadata_extractor.mapping.checks import CONTRACTS, check_targets
from git_metadata_extractor.mapping.models import FieldRule, MappingSpec
from git_metadata_extractor.mapping.policies import load_policy
from git_metadata_extractor.mapping.registry import TRANSFORMS
from git_metadata_extractor.observation.evidence import activity

CONFIG_ROOT = Path(__file__).resolve().parent / "config"

if TYPE_CHECKING:
    from pydantic import JsonValue


class UniqueKeyLoader(yaml.SafeLoader):
    """Safe YAML that also rejects silently overwritten mapping keys."""

    def construct_mapping(self, node, deep=False):  # noqa: FBT002 - PyYAML override signature
        self.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str):
                message = "YAML mapping keys must be strings"
                raise TypeError(message)
            if key in result:
                message = f"Duplicate YAML key: {key}"
                raise ValueError(message)
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def parse_mapping(text: str) -> MappingSpec:
    loader = UniqueKeyLoader(text)
    try:
        spec = MappingSpec.model_validate(loader.get_single_data())
    finally:
        loader.dispose()
    check_targets(spec)
    return spec


@lru_cache(maxsize=32)
def _load(name: str) -> MappingSpec:
    if not re.fullmatch(r"[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*", name):
        message = "Mapping names must be namespace.name, not filesystem paths"
        raise ValueError(message)
    path = CONFIG_ROOT.joinpath(*name.split(".")).with_suffix(".yaml")
    spec = parse_mapping(path.read_text(encoding="utf-8"))
    if spec.name != name:
        message = f"Mapping name does not match resource: {name}"
        raise ValueError(message)
    return spec


def load_mapping(name: str) -> MappingSpec:
    return _load(name).model_copy(deep=True)


def _empty(value: JsonValue) -> bool:
    return value is None or (isinstance(value, (str, list, dict)) and not value)


def _get(source: Mapping[str, JsonValue], path: str) -> JsonValue:
    value = source
    for key in path.split("."):
        if not isinstance(value, Mapping):
            return None
        value = value.get(key)
    return value


def _field_value(rule: FieldRule, source: Mapping[str, JsonValue]) -> JsonValue:
    value = deepcopy(rule.value)
    for path in rule.sources or []:
        candidate = _get(source, path)
        if not _empty(candidate) or (rule.empty == "preserve" and candidate is not None):
            value = candidate
            break

    if rule.transform == "enum":
        value = deepcopy(rule.values.get(str(value).lower()))
    else:
        value = TRANSFORMS[rule.transform](value)
    return None if _empty(value) and rule.empty != "preserve" else value


def map_fields(name: str, source: Mapping[str, JsonValue]) -> dict[str, JsonValue]:
    spec = _load(name)
    result: dict[str, JsonValue] = {}
    with activity(f"mapping:{name}", kind="mapping") as record:
        if record is not None:
            record.update(
                mapping_version=spec.version,
                mapping_fingerprint=mapping_manifest()["fingerprint"],
                source_contract=spec.source_contract,
                target_contract=spec.target_contract,
            )
        for target, rule in (spec.fields | spec.internal_fields).items():
            value = _field_value(rule, source)
            if value is None and rule.missing != "null":
                continue
            cursor = result
            parts = target.split(".")
            for part in parts[:-1]:
                cursor = cursor.setdefault(part, {})
            cursor[parts[-1]] = value
    return result


@lru_cache(maxsize=1)
def _manifest() -> dict:
    digest = sha256()
    mappings = []
    policies = []
    for path in sorted(CONFIG_ROOT.rglob("*.yaml")):
        name = ".".join(path.relative_to(CONFIG_ROOT).with_suffix("").parts)
        spec = load_policy(name) if name.startswith("policies.") else _load(name)
        digest.update(name.encode())
        digest.update(path.read_bytes())
        if name.startswith("policies."):
            policies.append({"name": name, "version": spec.version})
            continue
        mappings.append(
            {
                "name": name,
                "version": spec.version,
                "source_contract": spec.source_contract,
                "target_contract": spec.target_contract,
            },
        )
    contracts = {
        name: model.model_json_schema(by_alias=True)
        for name, model in CONTRACTS.items()
    }
    digest.update(json.dumps(contracts, sort_keys=True).encode())
    for name in ["models.py", "registry.py", "engine.py", "policies.py", "checks.py"]:
        digest.update(Path(__file__).with_name(name).read_bytes())
    return {
        "fingerprint": digest.hexdigest(),
        "mappings": mappings,
        "policies": policies,
    }


def mapping_manifest() -> dict:
    return deepcopy(_manifest())
