"""Named extension points; execution still uses the production orchestrator."""

from __future__ import annotations

import inspect
import json
import os
from contextlib import contextmanager
from copy import deepcopy
from hashlib import sha256
from typing import TYPE_CHECKING, Any, Callable

from pydantic import BaseModel

from git_metadata_extractor.agents.llm.runtime import (
    get_request_model_override,
    reset_request_model_override,
    set_request_model_override,
)
from git_metadata_extractor.agents.llm.tool_defaults import default_tool_catalog
from git_metadata_extractor.agents.registry import AgentRuntimeRegistry
from git_metadata_extractor.agents.runtime import AgentRuntime
from git_metadata_extractor.experiments.models import (
    ROLE_NAMES,
    ComponentRef,
    ModelSelection,
    PipelineProfile,
)
from git_metadata_extractor.models import InternalModel
from git_metadata_extractor.observation.evidence import activity, get_trace
from git_metadata_extractor.pipeline.orchestrator import (
    PLAN_BY_TYPE,
    PipelineOrchestrator,
)
from git_metadata_extractor.pipeline.stages.models import ContextBundle

if TYPE_CHECKING:
    from git_metadata_extractor.agents.tool_catalog import ToolCatalog


class CollectionContext(InternalModel, frozen=True):
    detected_type: str
    classification: Any
    context: dict[str, Any]


class Component(InternalModel, frozen=True):
    name: str
    version: str
    implementation: Callable
    runtime: str | None = None
    options_model: type[BaseModel] | None = None


@contextmanager
def selected_model(model: ModelSelection | None):
    if model is None:
        yield
        return
    if os.getenv("V2_ALLOW_REQUEST_MODEL_OVERRIDE", "").lower() not in {
        "true",
        "1",
        "yes",
        "on",
        "t",
        "y",
    }:
        msg = "Model selection requires V2_ALLOW_REQUEST_MODEL_OVERRIDE=true"
        raise ValueError(msg)
    token = set_request_model_override(
        {**(get_request_model_override() or {}), **model.model_dump(exclude_none=True)},
    )
    try:
        yield
    finally:
        reset_request_model_override(token)


async def invoke(function: Callable, *args, **kwargs):
    result = function(*args, **kwargs)
    return await result if inspect.isawaitable(result) else result


def _merge(target: dict, additions: dict) -> None:
    for key, value in additions.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _merge(target[key], value)
        else:
            target[key] = deepcopy(value)


class ComponentCatalog:
    def __init__(self, *, tools: ToolCatalog | None = None) -> None:
        self.collectors: dict[str, Component] = {}
        self.agents: dict[str, Component] = {}
        self.tools = tools if tools is not None else default_tool_catalog()

    def _register(self, table: dict, component: Component) -> None:
        if component.name in table:
            msg = f"Component already registered: {component.name}"
            raise ValueError(msg)
        if not component.version or not callable(component.implementation):
            msg = "A component requires a version and callable implementation"
            raise ValueError(msg)
        table[component.name] = component

    def register_collector(
        self,
        name: str,
        function: Callable,
        *,
        version: str,
        options_model: type[BaseModel] | None = None,
    ) -> None:
        self._register(
            self.collectors,
            Component(name=name, version=version, implementation=function, options_model=options_model),
        )

    def register_agent(
        self,
        name: str,
        factory: Callable,
        *,
        version: str,
        runtime: str,
        options_model: type[BaseModel] | None = None,
    ) -> None:
        if runtime not in {"llm", "rule_based"}:
            msg = "Agent runtime must be llm or rule_based"
            raise ValueError(msg)
        self._register(
            self.agents, Component(name=name, version=version, implementation=factory, runtime=runtime, options_model=options_model),
        )

    @staticmethod
    def options(component: Component, reference: ComponentRef) -> dict:
        if component.options_model is not None:
            return component.options_model.model_validate(reference.options).model_dump(
                mode="json",
            )
        return deepcopy(reference.options)

    def bindings(self, profile: PipelineProfile) -> dict[str, ComponentRef]:
        runtime = "llm" if profile.runtime == "llm" else "rule_based"
        return {
            role: profile.agents.get(role, ComponentRef(component=f"{runtime}.{kind}"))
            for role, kind in ROLE_NAMES.items()
        }

    def validate(self, profile: PipelineProfile) -> None:  # noqa: C901 - validate all profile extension points
        if profile.toolsets and profile.runtime != "llm":
            msg = "Toolset selections require llm runtime"
            raise ValueError(msg)
        for role, selection in profile.toolsets.items():
            if role not in {*ROLE_NAMES, "context_summary_agent", "context_scout"}:
                msg = f"Unknown toolset role: {role}"
                raise ValueError(msg)
            self.tools.validate_selection(selection)
        for reference in profile.collectors:
            if reference.component not in self.collectors:
                msg = f"unknown collector: {reference.component}"
                raise ValueError(msg)
            if reference.model is not None:
                msg = "Collectors do not accept model overrides"
                raise ValueError(msg)
            self.options(self.collectors[reference.component], reference)
        runtime = "llm" if profile.runtime == "llm" else "rule_based"
        for reference in self.bindings(profile).values():
            component = self.agents.get(reference.component)
            if component is None:
                msg = f"unknown agent: {reference.component}"
                raise ValueError(msg)
            if component.runtime != runtime:
                msg = f"Agent runtime does not match profile: {reference.component}"
                raise ValueError(msg)
            if reference.model and runtime != "llm":
                msg = "Generator model overrides require llm runtime"
                raise ValueError(msg)
            self.options(component, reference)
        if profile.runtime == "rule_based" and profile.model:
            msg = "A rule_based profile cannot select a model"
            raise ValueError(msg)

    def manifest(self, profile: PipelineProfile) -> dict[str, Any]:
        self.validate(profile)
        components = {
            ref.component: self.collectors[ref.component].version
            for ref in profile.collectors
        }
        components.update(
            {
                ref.component: self.agents[ref.component].version
                for ref in self.bindings(profile).values()
            },
        )
        payload = {"profile": profile.model_dump(mode="json"), "components": components,
                   "tool_catalog": self.tools.manifest()}
        digest = sha256(
            json.dumps(payload, sort_keys=True, separators=(",", ":")).encode(),
        ).hexdigest()
        return {**payload, "fingerprint": digest}

    def build(self, profile: PipelineProfile, *, cache=None) -> PipelineOrchestrator:
        self.validate(profile)
        registry = AgentRuntimeRegistry()
        for role, reference in self.bindings(profile).items():
            component = self.agents[reference.component]
            runner = component.implementation(self.options(component, reference), cache)
            registry.register_runner(
                role,
                self._agent_runner(component, runner, reference),
                runtime=AgentRuntime(profile.runtime),
            )

        async def collect(detected_type, classification, providers):
            combined = ContextBundle(detected_type=detected_type)
            for reference in profile.collectors:
                component = self.collectors[reference.component]
                context = CollectionContext(
                    detected_type=detected_type, classification=classification, context=deepcopy(combined.context),
                )
                with activity(
                    f"collector:{component.name}@{component.version}", kind="retrieval",
                ):
                    bundle = await invoke(
                        component.implementation,
                        context,
                        providers,
                        self.options(component, reference),
                    )
                if (
                    not isinstance(bundle, ContextBundle)
                    or bundle.detected_type != detected_type
                ):
                    msg = f"Collector returned incompatible ContextBundle: {component.name}"
                    raise ValueError(msg)
                _merge(combined.context, bundle.context)
                combined.warnings.extend(bundle.warnings)
            return combined

        return PipelineOrchestrator(
            context_gatherer=collect,
            agent_registry=registry,
            cache=cache,
            max_concurrent_agents=profile.max_concurrent_agents,
            retry_max_retries=profile.retry_max_retries,
            plans_by_type={**PLAN_BY_TYPE, **profile.stages},
        )

    @staticmethod
    def _agent_runner(component: Component, runner: Callable, reference: ComponentRef):
        async def run(context, providers):
            with selected_model(reference.model), activity(
                f"agent:{component.name}@{component.version}",
                kind=component.runtime or "agent",
            ):
                result = await invoke(runner, context, providers)
                trace = get_trace()
                if trace is not None:
                    trace.observe(result.data)
                return result

        run.__qualname__ = f"{component.name}@{component.version}"
        return run
