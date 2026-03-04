# LLM Agents — Developer Guide

This guide explains the architecture of the `src/v2/agents/llm/` sub-package and walks through the exact steps required to add a new LLM-backed agent. Use the existing `LLMRepositoryAgentV2` and `LLMPersonAgentV2` as reference implementations.

---

## Directory layout

```
src/v2/agents/llm/
├── __init__.py                  # Public exports (LLMRepositoryAgentV2, LLMPersonAgentV2)
├── _loader.py                   # load_prompt() — importlib.resources wrapper
├── prompt_context.py            # Optional runtime prompt append blocks
├── agent_tools/
│   ├── __init__.py
│   ├── disciplines.py           # Static tool: list_disciplines_tool
│   ├── infoscience_search.py    # Factory: make_infoscience_search_tool(provider)
│   └── orcid_person.py          # Factory: make_orcid_person_tool(provider)
├── repository/
│   ├── __init__.py              # Exports LLMRepositoryAgentV2
│   ├── agent.py                 # LLMRepositoryAgentV2 class
│   └── prompts/
│       ├── __init__.py          # Empty marker (required for importlib.resources)
│       ├── system_prompt.md
│       └── user_prompt.md
└── person/
    ├── __init__.py              # Exports LLMPersonAgentV2
    ├── agent.py                 # LLMPersonAgentV2 class
    └── prompts/
        ├── __init__.py          # Empty marker (required for importlib.resources)
        ├── system_prompt.md
        └── user_prompt.md
```

---

## Core concepts

### `V2LLMRuntime` (`src/v2/llm/runtime.py`)

All LLM agents delegate to `V2LLMRuntime.run_json_prompt()`. This is the single call surface to pydantic-ai.

```python
result: LLMRuntimeResult = await self._llm_runtime.run_json_prompt(
    system_prompt=_SYSTEM_PROMPT,     # str — loaded from prompts/system_prompt.md
    user_prompt=user_prompt,           # str — filled template from prompts/user_prompt.md
    output_type=MyOutputShape,         # Pydantic BaseModel class (NOT a union RootModel)
    tools=[tool1, tool2],              # list[pydantic_ai.Tool] — may be empty
)
```

`LLMRuntimeResult` fields:
- `payload: dict[str, Any]` — serialized output (`model_dump(by_alias=True)`)
- `model: str`, `provider: str` — from model config
- `tokens_prompt: int | None`, `tokens_completion: int | None`
- `requests: int | None`, `tool_calls: int | None` — extracted from `result.usage()`

**Important**: `output_type` must be a flat `BaseModel` with `type: "object"` JSON schema. pydantic-ai cannot use `RootModel` union types (their schema is `{"anyOf": [...]}`) — create a flat mirror model if needed (see `LLMPersonOutputShape` in `person/agent.py`).

### Two-pass validation

Every agent validates the LLM output twice:

| Pass | Tool | On failure |
|---|---|---|
| 1st (permissive) | pydantic-ai enforces `output_type` during generation | pydantic-ai retries (configurable `max_retries`) then raises `LLMRuntimeError` |
| 2nd (strict) | `_strict_validate()` calls `XxxModel.model_validate(payload)` | Appends warnings to `AgentResult.warnings`, never raises |

The strict model comes from `src/v2/generated/entities.py` (TTL-shape-aligned).

### `RuntimeAgent` protocol (`src/v2/agents/contracts.py`)

Any class with this signature satisfies `RuntimeAgent`:

```python
async def run(self, context: dict[str, Any], providers: ProviderSet) -> AgentResult: ...
```

No inheritance needed. This is structural duck typing.

### Prompt loading

Prompts live as Markdown files inside a `prompts/` sub-package. Load them at module import time:

```python
from src.v2.agents.llm._loader import load_prompt

_PROMPTS_PACKAGE = "src.v2.agents.llm.myagent.prompts"
_SYSTEM_PROMPT = load_prompt(_PROMPTS_PACKAGE, "system_prompt.md")
_USER_PROMPT_TEMPLATE = load_prompt(_PROMPTS_PACKAGE, "user_prompt.md")
```

The `prompts/` directory must contain an empty `__init__.py` so `importlib.resources` can resolve it as a package.

---

## Tools

Tools are `pydantic_ai.Tool` instances passed to `run_json_prompt`. They are invoked by the LLM during generation (agentic tool-use).

### Static tool (no provider dependency)

```python
# agent_tools/my_static_tool.py
from pydantic_ai import Tool

def _my_function() -> list[str]:
    """Return a list of valid options."""
    return ["option_a", "option_b"]

my_static_tool = Tool(
    _my_function,
    name="my_tool_name",
    description="Describe when and why the LLM should call this.",
)
```

Import and use directly: `tools=[my_static_tool]`.

### Factory tool (captures a provider at runtime)

Use this pattern when the tool needs a live provider (HTTP calls, etc.):

```python
# agent_tools/my_provider_tool.py
from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Any
from pydantic_ai import Tool

if TYPE_CHECKING:
    from src.v2.providers.base import MyProvider

logger = logging.getLogger(__name__)

def make_my_provider_tool(provider: MyProvider) -> Tool:
    """Factory — captures provider in closure, returns pydantic-ai Tool."""

    def call_my_provider(query: str) -> dict[str, Any]:
        """Docstring shown to LLM. Describe arguments and return shape."""
        logger.info("tool call: call_my_provider — query=%r", query)
        return provider.search(query)

    return Tool(
        call_my_provider,
        name="call_my_provider",
        description="When and why the LLM should call this tool.",
    )
```

Build the tool at `run()` time and include it only when the provider is present:

```python
tools = []
if providers.my_provider is not None:
    tools.append(make_my_provider_tool(providers.my_provider))
```

**Existing tools:**

| File | Export | Type | Used by |
|---|---|---|---|
| `agent_tools/disciplines.py` | `list_disciplines_tool` | Static | `LLMRepositoryAgentV2` |
| `agent_tools/infoscience_search.py` | `make_infoscience_search_tool(provider)` | Factory | `LLMPersonAgentV2` |
| `agent_tools/orcid_person.py` | `make_orcid_person_tool(provider)` | Factory | `LLMPersonAgentV2` |

---

## Adding a new LLM agent — step-by-step

This section assumes you are adding `LLMOrganizationAgentV2`. Substitute the entity name throughout.

### Step 1 — Create the agent package

```
src/v2/agents/llm/organization/
├── __init__.py
├── agent.py
└── prompts/
    ├── __init__.py       ← empty, required
    ├── system_prompt.md
    └── user_prompt.md
```

### Step 2 — Write the `output_type` shape

Create a flat `BaseModel` that mirrors the fields the LLM must produce. Use `alias=` for ontology field names (e.g. `schema:name`). Set `model_config = ConfigDict(extra="ignore")` so unexpected LLM fields are silently dropped.

```python
from pydantic import BaseModel, ConfigDict, Field

class LLMOrganizationOutputShape(BaseModel):
    model_config = ConfigDict(extra="ignore")

    id: str = Field(..., description="Resolved IRI for this organization.")
    type: str = Field(..., description="Must be 'schema:Organization'.")
    shacl: str = Field(..., description="Must be 'pulse:OrganizationShape'.")
    schema_name: str = Field(..., alias="schema:name")
    # … all required fields from the agent schema …
```

### Step 3 — Write the agent class

Minimum viable structure (copy, adapt):

```python
# src/v2/agents/llm/organization/agent.py
from __future__ import annotations

import json
import logging
from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from src.v2.agents.llm._loader import load_prompt
from src.v2.agents.llm.prompt_context import append_runtime_prompt_context
from src.v2.agents.models import AgentResult, ProviderSet
from src.v2.generated.entities import OrganizationModel          # strict model
from src.v2.llm.runtime import LLMRuntimeError, V2LLMRuntime

logger = logging.getLogger(__name__)

_PROMPTS_PACKAGE = "src.v2.agents.llm.organization.prompts"
_SYSTEM_PROMPT = load_prompt(_PROMPTS_PACKAGE, "system_prompt.md")
_USER_PROMPT_TEMPLATE = load_prompt(_PROMPTS_PACKAGE, "user_prompt.md")


def _strict_validate(payload: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    try:
        OrganizationModel.model_validate(payload)
    except ValidationError as exc:
        for error in exc.errors():
            field = " -> ".join(str(loc) for loc in error["loc"])
            warnings.append(f"Strict schema warning at {field}: {error['msg']}")
    return warnings


class LLMOrganizationAgentV2:
    def __init__(self, *, llm_runtime: V2LLMRuntime | None = None) -> None:
        self._llm_runtime = llm_runtime or V2LLMRuntime()

    async def run(self, context: dict[str, Any], providers: ProviderSet) -> AgentResult:
        # 1. Extract required context fields; raise ValueError on missing identity.
        # 2. Build llm_input dict.
        context_json = json.dumps(llm_input, ensure_ascii=True, sort_keys=True)
        user_prompt = _USER_PROMPT_TEMPLATE.replace("{context_json}", context_json)
        user_prompt = append_runtime_prompt_context(user_prompt, context)

        # 3. Build tools (factory closures capturing providers).
        tools = []
        # tools.append(make_ror_tool(providers.ror)) if providers.ror is not None

        # 4. LLM call.
        logger.info("calling LLM (%d tool(s))", len(tools))
        try:
            llm_result = await self._llm_runtime.run_json_prompt(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                output_type=LLMOrganizationOutputShape,
                tools=tools,
            )
        except LLMRuntimeError:
            raise
        except Exception as exc:
            raise LLMRuntimeError(str(exc)) from exc

        payload = {k: v for k, v in llm_result.payload.items() if v is not None}
        overrides = context.get("agent_overrides")
        if isinstance(overrides, dict):
            payload.update(overrides)

        raw_output = deepcopy(payload)
        validation_warnings = _strict_validate(payload)

        return AgentResult(
            data=payload,
            warnings=validation_warnings,
            raw_output=raw_output,
            model=llm_result.model,
            provider=llm_result.provider,
            tokens_prompt=llm_result.tokens_prompt,
            tokens_completion=llm_result.tokens_completion,
            stats={"agent_runtime": "llm", "derivation": {}},
        )
```

Key conventions:
- Strip `None` top-level optional fields before strict validation (`{k: v for k, v in ... if v is not None}`).
- Apply `agent_overrides` from context after the LLM call, before validation.
- `del providers` only if the agent genuinely needs no providers (like `LLMRepositoryAgentV2`). If you build tools from providers, keep the reference.
- For long-running person extraction, wrap runtime calls with an explicit timeout (see `LLMPersonAgentV2.llm_call_timeout_seconds`).

### Step 4 — Write the prompts

**`system_prompt.md`** — explain:
- The target entity shape and required fields.
- Identifier hierarchy / `idSource` logic.
- Which tools are available and when to call them.
- Output format constraints.

**`user_prompt.md`** — minimal, single placeholder:
```
Build an Organization payload using the runtime context:

{context_json}
```

### Step 5 — Export from sub-package `__init__.py`

```python
# src/v2/agents/llm/organization/__init__.py
from src.v2.agents.llm.organization.agent import LLMOrganizationAgentV2

__all__ = ["LLMOrganizationAgentV2"]
```

### Step 6 — Export from `src/v2/agents/llm/__init__.py`

```python
from src.v2.agents.llm.organization import LLMOrganizationAgentV2
from src.v2.agents.llm.person import LLMPersonAgentV2
from src.v2.agents.llm.repository import LLMRepositoryAgentV2

__all__ = ["LLMOrganizationAgentV2", "LLMPersonAgentV2", "LLMRepositoryAgentV2"]
```

### Step 7 — Export from `src/v2/agents/__init__.py`

```python
from src.v2.agents.llm import LLMOrganizationAgentV2, LLMPersonAgentV2, LLMRepositoryAgentV2
# … add to __all__ …
```

---

## Wiring into the registry (`src/v2/agents/registry.py`)

`AgentRuntimeRegistry` routes LLM agents by `(runtime, stage_key)`. Add a stage constant and a routing branch:

```python
from src.v2.agents.llm import LLMOrganizationAgentV2   # add import

STAGE_ORG_AGENT = "org_agent"   # already defined in orchestrator; reuse or keep local

class AgentRuntimeRegistry:
    def __init__(
        self,
        *,
        rule_based_runners=None,
        llm_repository_agent=None,
        llm_person_agent=None,
        llm_organization_agent=None,   # new
    ) -> None:
        # … existing …
        self._llm_organization_agent = llm_organization_agent or LLMOrganizationAgentV2()

    def resolve_runner(self, *, stage_key, runtime, detected_type):
        # … existing repo + person blocks …

        if runtime == AgentRuntime.LLM and stage_key == STAGE_ORG_AGENT:
            return self._llm_organization_agent.run

        # fallback to rule-based
        runner = self._rule_based_runners.get(stage_key)
        if runner is None:
            raise ValueError(f"No runner registered for stage '{stage_key}'")
        return runner
```

---

## Wiring into the orchestrator (`src/v2/pipeline/orchestrator.py`)

`PipelineOrchestrator` constructs `AgentRuntimeRegistry`. Add the new agent parameter:

```python
from src.v2.agents import LLMOrganizationAgentV2   # add to existing import block

class PipelineOrchestrator:
    def __init__(
        self,
        *,
        # … existing params …
        llm_organization_agent: RuntimeAgent | None = None,
    ) -> None:
        self._llm_organization_agent = llm_organization_agent or LLMOrganizationAgentV2()

        self._agent_registry = agent_registry or AgentRuntimeRegistry(
            rule_based_runners=self._rule_based_runners,
            llm_repository_agent=self._llm_repository_agent,
            llm_person_agent=self._llm_person_agent,
            llm_organization_agent=self._llm_organization_agent,   # new
        )
```

If the agent maps to a stage already in `PLAN_BY_TYPE` (e.g. `STAGE_ORG_AGENT`), no further orchestrator changes are needed — the existing plan routing will pick up the new LLM runner automatically when `agent_runtime=llm`.

If you are adding a **brand-new pipeline stage**, also add the stage name to the relevant `PLAN_BY_TYPE` entries.

---

## Existing rule-based agents (for context)

The rule-based agents (`PersonAgentV2`, `OrganizationAgentV2`, etc.) live in `src/v2/agents/` (not inside `llm/`). They satisfy the same `RuntimeAgent` protocol and are the default when `agent_runtime=rule_based`. The orchestrator's `_rule_based_runners` dict maps stage keys to their `.run` methods.

LLM agents are selected by passing `agent_runtime=llm` in the pipeline context (or as a query parameter on the API). The `AgentRuntimeRegistry` routes accordingly.

---

## Writing tests

Create `tests/v2/test_llm_{entity}_agent.py`. The test module for `LLMPersonAgentV2` (`tests/v2/test_llm_person_agent.py`) is the canonical reference.

### Fake runtime pattern

Inject a fake `_llm_runtime` to avoid real LLM calls:

```python
class _FakeLLMRuntime:
    def __init__(self, payload: dict[str, Any]) -> None:
        self._payload = payload

    async def run_json_prompt(self, *, system_prompt, user_prompt, output_type=None, tools=None):
        assert system_prompt
        assert user_prompt
        return LLMRuntimeResult(
            payload=dict(self._payload),
            model="openai/gpt-test",
            provider="openai",
            tokens_prompt=13,
            tokens_completion=29,
        )

agent = LLMOrganizationAgentV2(llm_runtime=_FakeLLMRuntime(_valid_org_payload()))
result = await agent.run(context, providers)
```

### What to test

| Test | Asserts |
|---|---|
| `test_validates_payload_and_exposes_model_metadata` | jsonschema validates against agent schema; `model`/`provider`/token fields populated; `stats["agent_runtime"] == "llm"` |
| `test_propagates_llm_runtime_error` | `LLMRuntimeError` from raising fake runtime bubbles up unchanged |
| `test_records_strict_schema_warnings` | Invalid payload field → `result.warnings` non-empty |
| `test_builds_tools_from_providers` | Capturing fake records `tools` arg; expected tool names present when providers are set |
| `test_no_tools_without_providers` | Empty tools list when provider-dependent tools have no provider |
| `test_works_with_minimal_context` | Accepts context with only the minimum required identifier |

### Validating against agent schema

```python
import jsonschema
from tests.v2.conftest import load_schema  # or load directly

def test_validates(load_schema, result):
    schema = load_schema("agent", "organization")  # tests/v2/fixtures/schema/agent/organization.schema.json
    jsonschema.validate(result.data, schema)
```

### Integration test (real LLM)

Gate behind a credentials check:

```python
_HAS_LLM_CREDENTIALS = bool(
    os.getenv("RCP_TOKEN") or os.getenv("OPENAI_API_KEY") or os.getenv("OPENROUTER_API_KEY")
)

@pytest.mark.llm_integration
@pytest.mark.skipif(not _HAS_LLM_CREDENTIALS, reason="No LLM credentials")
async def test_real_provider_call():
    agent = LLMOrganizationAgentV2()
    result = await agent.run({"name": "EPFL"}, _providers_full())
    assert result.data.get("schema:name")
```

---

## Cross-cutting concerns

### Schemas

LLM output is validated against two schema layers. When your agent introduces a new entity type, you must update schemas in **all three** locations (see `AGENTS.md` for the triplication rule):

1. `src/v2/schemas/agent/{entity}.schema.json` — permissive, LLM I/O
2. `dev/ontology-v2-json-response/a-001/json-schema/agent/pulse_{Entity}Shape.schema.json` — promoted
3. `tests/v2/fixtures/schema/agent/{entity}.schema.json` — test fixture

Then regenerate Pydantic models:
```bash
just v2-models-generate
```

The generated model classes land in `src/v2/generated/agent_entities.py` (permissive) and `src/v2/generated/entities.py` (strict).

### `generate_uuid()`

Use `src.v2.agents.models.generate_uuid()` (UUIDv4) for any stable person/entity UUID generated inside an agent. Do not use deterministic UUIDv5 emitters for agent outputs.

### `agent_overrides`

Agents should apply `context.get("agent_overrides")` after the LLM call, before strict validation. This lets callers inject field values for testing or reconciliation without changing the agent.

### Runtime Prompt Context Append Blocks

Both `LLMRepositoryAgentV2` and `LLMPersonAgentV2` support optional prompt append sections via `append_runtime_prompt_context(...)`:

- `upstream_stage_outputs_json` (string): appended under `## Upstream Stage Outputs (JSON)`.
- `user_prompt_appendix` (string): appended under `## Additional Context (verbatim text)`.

These values are treated as raw strings and are not parsed by the helper.

### Concurrency

`PipelineOrchestrator` limits fanout via `max_concurrent_agents` (default 3). A fresh `asyncio.Semaphore` wraps each item inside `_execute_stage`. The standalone script `scripts/v2/run_llm_repo_and_persons.py` maintains its own `asyncio.Semaphore(3)` because it calls agents directly without going through the orchestrator.

Prompt propagation in orchestrated runs is configurable:

- `include_upstream_stage_outputs_in_prompt` (bool): serializes accumulated `pipeline_outputs` and forwards them as `upstream_stage_outputs_json` to each child stage context.
- `user_prompt_appendix` (str): forwards a verbatim text block to each child context so agents can append pre-concatenated multi-file text directly to user prompts.

### Logging

Follow the pattern established in the existing agents:

```python
import logging
logger = logging.getLogger(__name__)

# At entry points:
logger.info("%s — starting", identifier)
# Before external calls:
logger.info("%s — fetching external resource", identifier)
# After LLM call:
logger.info("%s — LLM done (prompt=%s tokens, completion=%s tokens)", identifier, ...)
```

---

## Validation checklist before merging

```bash
# Unit tests for the new agent
just test-file tests/v2/test_llm_{entity}_agent.py

# Full v2 suite — no regressions
PYTHONPATH=. .venv/bin/pytest tests/v2/ -q --tb=line

# Lint and type checks
just lint
just type-check
```
