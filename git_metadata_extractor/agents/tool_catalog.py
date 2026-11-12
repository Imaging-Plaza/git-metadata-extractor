"""Reusable, versioned tool factories with task-local agent selections."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from hashlib import sha256
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from git_metadata_extractor.agents.models import ProviderSet
from git_metadata_extractor.models import InternalModel
from git_metadata_extractor.providers.cache import ProviderCache

RESERVED_TOOL_NAMES = {"list_source_evidence", "cite_source"}


def reject_credentials(value: Any) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            if str(key).lower() in {
                "api_key",
                "token",
                "password",
                "secret",
                "authorization",
                "cookie",
            }:
                msg = "Inline credentials are forbidden; use environment variable names"
                raise ValueError(msg)
            reject_credentials(child)
    elif isinstance(value, list):
        for child in value:
            reject_credentials(child)


class ToolRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    component: str = Field(min_length=1)
    options: dict[str, JsonValue] = Field(default_factory=dict)
    required: bool = True

    @model_validator(mode="after")
    def no_credentials(self):
        reject_credentials(self.options)
        return self


class ToolSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    include_defaults: bool = True
    tools: list[ToolRef] = Field(default_factory=list)


class NoToolOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ToolContext(InternalModel, frozen=True):
    providers: ProviderSet
    cache: ProviderCache | None = None
    context: dict[str, Any] = Field(default_factory=dict)


class ToolDefinition(InternalModel, frozen=True):
    factory: Callable
    version: str
    requires: tuple[str, ...]
    requires_any: tuple[str, ...]
    options_model: type[BaseModel]


class ToolCatalog:
    """Factories return one Tool (or a list), without performing external I/O."""

    def __init__(self) -> None:
        self.tools: dict[str, ToolDefinition] = {}
        self.bundles: dict[str, tuple[str, ...]] = {}
        self.defaults: dict[str, tuple[str, ...]] = {}

    def _check_new(self, name: str) -> None:
        if not name or name in self.tools or name in self.bundles:
            msg = f"Invalid or duplicate component: {name}"
            raise ValueError(msg)

    def register(  # noqa: PLR0913 - explicit factory dependencies and option schema
        self,
        name: str,
        factory: Callable,
        *,
        version: str,
        requires: tuple[str, ...] = (),
        requires_any: tuple[str, ...] = (),
        options_model: type[BaseModel] = NoToolOptions,
    ) -> None:
        self._check_new(name)
        if not version or not callable(factory):
            msg = "A tool requires a version and callable factory"
            raise ValueError(msg)
        self.tools[name] = ToolDefinition(
            factory=factory, version=version, requires=tuple(requires), requires_any=tuple(requires_any), options_model=options_model,
        )

    def register_bundle(self, name: str, members: list[str]) -> None:
        self._check_new(name)
        # Only previously registered members are legal, making cycles impossible.
        self.validate_selection(
            ToolSelection(tools=[ToolRef(component=m) for m in members]),
        )
        self.bundles[name] = tuple(members)

    def set_defaults(self, role: str, members: list[str]) -> None:
        self.validate_selection(
            ToolSelection(tools=[ToolRef(component=m) for m in members]),
        )
        self.defaults[role] = tuple(members)

    def _expand(self, reference: ToolRef) -> Iterator[ToolRef]:
        if reference.component in self.bundles:
            if reference.options:
                msg = "Bundle options must be set on individual tool components"
                raise ValueError(msg)
            for name in self.bundles[reference.component]:
                yield from self._expand(
                    ToolRef(component=name, required=reference.required),
                )
        elif reference.component in self.tools:
            yield reference
        else:
            msg = f"Unknown tool component: {reference.component}"
            raise ValueError(msg)

    def validate_selection(self, selection: ToolSelection) -> None:
        for reference in selection.tools:
            for expanded in self._expand(reference):
                self.tools[expanded.component].options_model.model_validate(
                    expanded.options,
                )

    def build(
        self, role: str, context: ToolContext, selection: ToolSelection | None = None,
    ) -> list[Any]:
        selection = selection or ToolSelection()
        self.validate_selection(selection)
        references = (
            [ToolRef(component=n, required=False) for n in self.defaults.get(role, ())]
            if selection.include_defaults
            else []
        )
        references.extend(selection.tools)
        # Explicit selections replace default options, preserving first appearance.
        expanded = {
            item.component: item for ref in references for item in self._expand(ref)
        }
        result, names = [], set()
        for name, reference in expanded.items():
            definition = self.tools[name]
            missing = [
                p
                for p in definition.requires
                if getattr(context.providers, p, None) is None
            ]
            if definition.requires_any and not any(
                getattr(context.providers, p, None) is not None
                for p in definition.requires_any
            ):
                missing.append("one of " + ", ".join(definition.requires_any))
            if missing:
                if reference.required:
                    msg = f"Tool {name} requires provider(s): {', '.join(missing)}"
                    raise ValueError(msg)
                continue
            options = definition.options_model.model_validate(
                reference.options,
            ).model_dump(mode="json")
            built = definition.factory(context, options)
            for tool in built if isinstance(built, list) else [built]:
                tool_name = getattr(tool, "name", None) or getattr(
                    tool, "__name__", None,
                )
                if (
                    not tool_name
                    or tool_name in names
                    or tool_name in RESERVED_TOOL_NAMES
                ):
                    msg = f"Duplicate tool name or reserved/unnamed tool: {tool_name}"
                    raise ValueError(msg)
                names.add(tool_name)
                result.append(tool)
        return result

    def manifest(self) -> dict:
        return {
            "tools": {
                name: {
                    "version": d.version,
                    "requires": list(d.requires),
                    "requires_any": list(d.requires_any),
                    "options_schema": d.options_model.model_json_schema(),
                }
                for name, d in self.tools.items()
            },
            "bundles": {name: list(members) for name, members in self.bundles.items()},
            "defaults": {
                name: list(members) for name, members in self.defaults.items()
            },
        }


_TOOL_SCOPE: ContextVar[tuple[ToolCatalog, dict[str, ToolSelection], str] | None] = (
    ContextVar("tool_scope", default=None)
)


@contextmanager
def tool_scope(catalog: ToolCatalog, selections: dict[str, ToolSelection]):
    for selection in selections.values():
        catalog.validate_selection(selection)
    snapshot = {key: value.model_copy(deep=True) for key, value in selections.items()}
    fingerprint = sha256(
        json.dumps(
            {
                "catalog": catalog.manifest(),
                "selections": {
                    key: value.model_dump(mode="json")
                    for key, value in snapshot.items()
                },
            },
            sort_keys=True,
        ).encode(),
    ).hexdigest()
    token = _TOOL_SCOPE.set((catalog, snapshot, fingerprint))
    try:
        yield
    finally:
        _TOOL_SCOPE.reset(token)


def current_tool_fingerprint() -> str | None:
    scoped = _TOOL_SCOPE.get()
    return scoped[2] if scoped is not None else None


def resolve_tools(role: str, context: ToolContext) -> list[Any]:
    scoped = _TOOL_SCOPE.get()
    if scoped is None:
        from git_metadata_extractor.agents.llm.tool_defaults import (  # noqa: PLC0415 - avoid agent import cycle
            default_tool_catalog,
        )

        return default_tool_catalog().build(role, context)
    catalog, selections, _ = scoped
    return catalog.build(role, context, selections.get(role))
