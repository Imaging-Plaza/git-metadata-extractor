from __future__ import annotations

import json
from copy import deepcopy
from typing import Any
from urllib.parse import urlparse

from pydantic import ValidationError

from src.v2.agents.llm._loader import load_prompt
from src.v2.agents.llm.agent_tools.disciplines import list_disciplines_tool
from src.v2.agents.llm.agent_tools.selenium_fetch import (
    fetch_link_content_via_selenium_tool,
)
from src.v2.agents.llm.prompt_context import append_runtime_prompt_context
from src.v2.agents.models import AgentResult, ProviderSet
from src.v2.schema.models.agent import AgentRepositoryShape
from src.v2.schema.models.strict import RepositoryModel
from src.v2.agents.llm.runtime import (
    LLMRuntimeError,
    V2LLMRuntime,
)

MIN_REPOSITORY_SEGMENTS = 2
README_CONTENT_MAX_CHARS = 4000
GIMIE_JSONLD_MAX_CHARS = 8000

_PROMPTS_PACKAGE = "src.v2.agents.llm.repository.prompts"
_SYSTEM_PROMPT = load_prompt(_PROMPTS_PACKAGE, "system_prompt.md")
_USER_PROMPT_TEMPLATE = load_prompt(_PROMPTS_PACKAGE, "user_prompt.md")


def _ensure_repo_handle(context: dict[str, Any]) -> str:
    """Resolve ``owner/repo`` from context or source URL."""

    for key in ("full_name", "repository_handle", "github_repository_handle"):
        value = context.get(key)
        if isinstance(value, str) and "/" in value:
            return value.strip()

    source_url = context.get("source_url")
    if isinstance(source_url, str):
        parsed = urlparse(source_url if "://" in source_url else f"https://{source_url}")
        segments = [segment for segment in parsed.path.split("/") if segment]
        if len(segments) >= MIN_REPOSITORY_SEGMENTS:
            return f"{segments[0]}/{segments[1]}"

    message = "Repository context is missing a GitHub owner/repository handle"
    raise ValueError(message)


def _extract_contributor_logins(contributors: Any) -> list[str]:
    """Return unique contributor logins, excluding organization accounts."""

    if not isinstance(contributors, list):
        return []

    logins: list[str] = []
    seen: set[str] = set()
    for contributor in contributors:
        login: str | None = None
        if isinstance(contributor, dict):
            contributor_type = contributor.get("type")
            if isinstance(contributor_type, str) and contributor_type.lower() == "organization":
                continue
            candidate = contributor.get("login")
            if isinstance(candidate, str) and candidate.strip():
                login = candidate.strip()
        elif isinstance(contributor, str) and contributor.strip():
            login = contributor.strip()

        if login is None or login in seen:
            continue
        seen.add(login)
        logins.append(login)
    return logins


def _strict_validate(payload: dict[str, Any]) -> list[str]:
    """Validate payload against the strict RepositoryModel; return warnings only."""

    warnings: list[str] = []
    try:
        RepositoryModel.model_validate(payload)
    except ValidationError as exc:
        for error in exc.errors():
            field = " -> ".join(str(loc) for loc in error["loc"])
            warnings.append(f"Strict schema warning at {field}: {error['msg']}")
    return warnings


class LLMRepositoryAgentV2:
    """LLM-backed agent that produces a pulse:RepositoryShape entity from gathered
    repository context.

    Data flow:
      1. Context extraction — pulls full_name, metadata, contributors, languages,
         and readme_content from the repository_context bundle assembled by the
         pipeline's context-gather stage (backed by GIMIE).
      2. Prompt assembly — serialises the extracted context as JSON and injects it
         into the Markdown prompt templates loaded from the ``prompts/`` sub-package
         via importlib.resources.
      3. LLM call — delegates to V2LLMRuntime with ``output_type=AgentRepositoryShape``,
         so pydantic-ai validates the model output natively against the agent schema
         (first validation pass — permissive, coerces minor type mismatches).
      4. Strict validation — validates the agent-schema-clean payload a second time
         against RepositoryModel (strict schema). Failures add warnings only; they
         do not reject the result.
      5. Stats — attaches derivation metadata (contributor logins, language names,
         owner info) and LLM telemetry (model, provider, token counts) to AgentResult.

    Errors:
      LLMRuntimeError — raised on LLM call failures (config, network, or schema
                        rejection after pydantic-ai retries are exhausted).
      ValueError      — raised when the repository handle cannot be resolved from
                        the supplied context.
    """

    def __init__(
        self,
        *,
        llm_runtime: V2LLMRuntime | None = None,
    ) -> None:
        self._llm_runtime = llm_runtime or V2LLMRuntime()

    async def run(
        self,
        context: dict[str, Any],
        providers: ProviderSet,
    ) -> AgentResult:
        """Generate and double-validate a repository payload from gathered context.

        Args:
            context: Runtime context dict. Expected keys:
                - ``full_name`` (str): GitHub ``owner/repo`` handle.
                - ``source_url`` (str, optional): Repository URL.
                - ``repository_context`` (dict, optional): Sub-dict with keys
                  ``metadata``, ``contributors``, ``languages``, ``readme_content``
                  as assembled by the pipeline's context-gather stage.
                - ``agent_overrides`` (dict, optional): Field overrides applied
                  after the LLM call, before validation.
            providers: Injected provider bundle (not used by this agent).

        Returns:
            AgentResult with the validated repository payload, merged warnings
            from both validation passes, raw LLM output, token counts, and
            derivation stats.
        """

        del providers
        full_name = _ensure_repo_handle(context)

        repository_context = context.get("repository_context")
        if not isinstance(repository_context, dict):
            repository_context = {}
        metadata = repository_context.get("metadata")
        if not isinstance(metadata, dict):
            metadata = {}
        contributors = repository_context.get("contributors")
        if not isinstance(contributors, list):
            contributors = []
        languages = repository_context.get("languages")
        if not isinstance(languages, dict):
            languages = {}
        readme_content = repository_context.get("readme_content") or ""
        gimie_jsonld = repository_context.get("gimie_jsonld")

        llm_input = {
            "full_name": full_name,
            "metadata": metadata,
            "contributors": contributors,
            "languages": languages,
            "source_url": context.get("source_url"),
            "readme_content": readme_content[:README_CONTENT_MAX_CHARS] or None,
        }
        if isinstance(gimie_jsonld, dict) and gimie_jsonld:
            llm_input["gimie_jsonld"] = json.dumps(
                gimie_jsonld,
                ensure_ascii=True,
            )[:GIMIE_JSONLD_MAX_CHARS]
        context_json = json.dumps(llm_input, ensure_ascii=True, sort_keys=True)
        user_prompt = _USER_PROMPT_TEMPLATE.replace("{context_json}", context_json)
        user_prompt = append_runtime_prompt_context(user_prompt, context)

        try:
            llm_result = await self._llm_runtime.run_json_prompt(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                output_type=AgentRepositoryShape,
                tools=[list_disciplines_tool, fetch_link_content_via_selenium_tool],
            )
        except LLMRuntimeError:
            raise
        except Exception as exc:
            raise LLMRuntimeError(str(exc)) from exc

        payload = dict(llm_result.payload)
        overrides = context.get("agent_overrides")
        if isinstance(overrides, dict):
            payload.update(overrides)

        raw_output = deepcopy(payload)

        # Second validation pass: strict schema (warnings only, never raises).
        validation_warnings = _strict_validate(payload)

        language_names = sorted(
            language
            for language in payload.get("schema:programmingLanguage", [])
            if isinstance(language, str) and language
        )
        derivation_stats = {
            "repository_full_name": full_name,
            "source_repositories": [full_name],
            "owner_login": metadata.get("owner", {}).get("login")
            if isinstance(metadata.get("owner"), dict)
            else None,
            "owner_type": metadata.get("owner", {}).get("type")
            if isinstance(metadata.get("owner"), dict)
            else None,
            "contributor_logins": _extract_contributor_logins(contributors),
            "contributors": deepcopy(contributors),
            "language_names": language_names,
        }

        return AgentResult(
            data=payload,
            warnings=validation_warnings,
            raw_output=raw_output,
            model=llm_result.model,
            provider=llm_result.provider,
            tokens_prompt=llm_result.tokens_prompt,
            tokens_completion=llm_result.tokens_completion,
            stats={
                "agent_runtime": "llm",
                "derivation": derivation_stats,
            },
        )
