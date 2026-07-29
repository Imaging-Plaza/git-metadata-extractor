from __future__ import annotations

import json
import logging
import os
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from git_metadata_extractor.agents.llm._loader import load_prompt
from git_metadata_extractor.agents.llm.agent_tools.duckduckgo_search import (
    make_duckduckgo_search_tool,
)
from git_metadata_extractor.agents.llm.agent_tools.repository_corpus_grep import (
    make_repository_corpus_grep_tool,
)
from pydantic_ai.usage import UsageLimits

from git_metadata_extractor.agents.models import AgentResult, ProviderSet
from git_metadata_extractor.agents.llm.runtime import LLMRuntimeError, V2LLMRuntime
from git_metadata_extractor.providers.cache import ProviderCache
from git_metadata_extractor.observation.query_log import stamp_current_agent

logger = logging.getLogger(__name__)

# Scout-mode usage budget. The scout prompt explicitly tells the LLM to
# spend ~20 tool calls up-front so per-entity agents don't have to; the
# global `V2_LLM_REQUEST_LIMIT` default of 25 (set in
# `git_metadata_extractor/agents/llm/runtime.py`) caps it before it finishes and the
# brief comes back empty, leaving downstream agents to re-discover every
# ORCID / ROR / DOI themselves. Override locally so scout gets the
# budget it was designed for. Per-call request count tracked by
# pydantic-ai; tool-call budget gates the actual external lookups.
#
# TODO: lift these into a general agent-tuning config file (alongside
# `V2_LLM_REQUEST_LIMIT` / `V2_LLM_TOOL_CALLS_LIMIT`) so every agent can
# declare its own budget without touching code. Today the two knobs are
# global env vars + this single per-agent override; a YAML/TOML config
# keyed by agent name would scale better as more agents need tuning.
_SCOUT_REQUEST_LIMIT = 60
_SCOUT_TOOL_CALLS_LIMIT = 120

_PROMPTS_PACKAGE = "git_metadata_extractor.agents.llm.context_summary.prompts"
_SYSTEM_PROMPT = load_prompt(_PROMPTS_PACKAGE, "system_prompt.md")
_USER_PROMPT_TEMPLATE = load_prompt(_PROMPTS_PACKAGE, "user_prompt.md")
# Loaded lazily only when scout mode is enabled, so projects that don't
# opt in never pay the import + read cost. The scout prompt produces the
# same `{summary_markdown}` shape as the default prompt — downstream
# agents are unchanged.
_SCOUT_PROMPT_FILENAME = "system_prompt_scout.md"


def _is_scout_mode_enabled() -> bool:
    """`V2_CONTEXT_SUMMARY_SCOUT_MODE=true` opts into the broader recon stage.

    Off by default — the existing 2-tool (corpus_grep + DuckDuckGo)
    summary keeps producing the historical brief shape. Turn on to give
    the summary agent the per-entity RAG search toolkit (orcid_rag,
    ror_rag, infoscience_rag, openalex_rag, selenium_fetch, etc.) so it
    consolidates discovery work that downstream agents currently
    duplicate.
    """
    raw = os.environ.get("V2_CONTEXT_SUMMARY_SCOUT_MODE", "").strip().lower()
    return raw in {"1", "true", "yes", "on"}

MAX_DOCUMENT_COUNT = 120
MAX_SINGLE_DOCUMENT_CHARS = 120_000
MAX_REPOSITORY_FILE_CHARS = 20_000
MAX_PROMPT_DOC_PREVIEW_CHARS = 400


class LLMContextSummaryOutput(BaseModel):
    model_config = ConfigDict(extra="ignore")
    summary_markdown: str = Field(
        ...,
        description="Compiled markdown brief for downstream extraction agents.",
    )


def _to_non_empty_string(value: Any) -> str | None:
    if isinstance(value, str):
        candidate = value.strip()
        if candidate:
            return candidate
    return None


def _truncate_text(value: str, *, limit: int) -> str:
    if len(value) <= limit:
        return value
    return f"{value[: limit - 3]}..."


def _coerce_repository_file_entries(candidate: Any) -> list[dict[str, str]]:
    rows: list[dict[str, str]] = []
    if isinstance(candidate, dict):
        for path, content in candidate.items():
            path_value = _to_non_empty_string(path)
            content_value = _to_non_empty_string(content)
            if not path_value or not content_value:
                continue
            rows.append({"path": path_value, "content": content_value})
        return rows

    if not isinstance(candidate, list):
        return rows

    for item in candidate:
        if not isinstance(item, dict):
            continue
        path_value = _to_non_empty_string(
            item.get("path"),
        ) or _to_non_empty_string(
            item.get("file_path"),
        ) or _to_non_empty_string(
            item.get("name"),
        )
        content_value = _to_non_empty_string(
            item.get("content"),
        ) or _to_non_empty_string(
            item.get("text"),
        ) or _to_non_empty_string(
            item.get("body"),
        )
        if not path_value or not content_value:
            continue
        rows.append({"path": path_value, "content": content_value})
    return rows


def _append_document(
    documents: list[dict[str, Any]],
    *,
    label: str,
    repository: str,
    origin: str,
    path: str,
    content: str,
) -> None:
    normalized_content = _to_non_empty_string(content)
    if not normalized_content:
        return
    if len(documents) >= MAX_DOCUMENT_COUNT:
        return
    documents.append(
        {
            "id": f"doc-{len(documents) + 1}",
            "label": label,
            "repository": repository,
            "origin": origin,
            "path": path,
            "content": _truncate_text(normalized_content, limit=MAX_SINGLE_DOCUMENT_CHARS),
        },
    )


def _append_repository_documents(
    documents: list[dict[str, Any]],
    repository_context: dict[str, Any],
) -> None:
    full_name = _to_non_empty_string(repository_context.get("full_name")) or "unknown/repository"
    readme_content = _to_non_empty_string(repository_context.get("readme_content"))
    if readme_content:
        _append_document(
            documents,
            label="Repository README",
            repository=full_name,
            origin="repository_context.readme_content",
            path="README.md",
            content=readme_content,
        )

    gimie_jsonld = repository_context.get("gimie_jsonld")
    if isinstance(gimie_jsonld, (dict, list)):
        serialized = json.dumps(gimie_jsonld, ensure_ascii=True, sort_keys=True)
        _append_document(
            documents,
            label="Raw GIMIE JSON-LD",
            repository=full_name,
            origin="repository_context.gimie_jsonld",
            path="gimie.jsonld",
            content=serialized,
        )

    file_entries = _coerce_repository_file_entries(
        repository_context.get("repository_files") or repository_context.get("files"),
    )
    for entry in file_entries:
        _append_document(
            documents,
            label="Repository File",
            repository=full_name,
            origin="repository_context.repository_files",
            path=entry["path"],
            content=_truncate_text(entry["content"], limit=MAX_REPOSITORY_FILE_CHARS),
        )


def _build_corpus_documents(
    detected_type: str,
    gathered_context: dict[str, Any],
) -> list[dict[str, Any]]:
    documents: list[dict[str, Any]] = []

    if detected_type == "repository":
        repository_context = gathered_context.get("repository")
        if isinstance(repository_context, dict):
            _append_repository_documents(documents, repository_context)
        return documents

    if detected_type == "user":
        user_context = gathered_context.get("user")
        if isinstance(user_context, dict):
            profile_readme = _to_non_empty_string(
                user_context.get("profile", {}).get("readme_content")
                if isinstance(user_context.get("profile"), dict)
                else None,
            )
            username = _to_non_empty_string(user_context.get("username")) or "unknown-user"
            if profile_readme:
                _append_document(
                    documents,
                    label="User Profile README",
                    repository=f"{username}/{username}",
                    origin="user_context.profile.readme_content",
                    path="README.md",
                    content=profile_readme,
                )

            repository_contexts = user_context.get("repository_contexts")
            if isinstance(repository_contexts, dict):
                for value in repository_contexts.values():
                    if isinstance(value, dict):
                        _append_repository_documents(documents, value)
        return documents

    if detected_type == "organization":
        organization_context = gathered_context.get("organization")
        if isinstance(organization_context, dict):
            profile_readme = _to_non_empty_string(
                organization_context.get("profile", {}).get("readme_content")
                if isinstance(organization_context.get("profile"), dict)
                else None,
            )
            org_name = _to_non_empty_string(organization_context.get("org_name")) or "unknown-org"
            if profile_readme:
                _append_document(
                    documents,
                    label="Organization Profile README",
                    repository=f"{org_name}/.github",
                    origin="organization_context.profile.readme_content",
                    path=".github/profile/README.md",
                    content=profile_readme,
                )

            repository_contexts = organization_context.get("repository_contexts")
            if isinstance(repository_contexts, dict):
                for value in repository_contexts.values():
                    if isinstance(value, dict):
                        _append_repository_documents(documents, value)
        return documents

    return documents


def _build_scout_tools(
    *,
    providers: ProviderSet,
    cache: ProviderCache | None,
    corpus_documents: list[dict[str, Any]],
) -> list[Any]:
    """Assemble the broad recon toolset for context_summary in scout mode.

    Mirrors the per-entity agents' tool sets (person, org, article,
    membership, contribution) so the scout sees the same search
    capabilities collectively. Only RAG `search_*` factories are
    pulled in — the heavier `fetch_chunks` / `fetch_records` stay out
    so the scout doesn't burn its budget on full-record retrieval.

    Each `provider.* is not None` guard mirrors how the per-entity
    agents wire tools — a missing index degrades silently.
    """
    # Local imports keep the legacy (non-scout) code path import-cost-free.
    from git_metadata_extractor.agents.llm.agent_tools.epfl_graph_rag import (  # noqa: PLC0415
        make_epfl_graph_rag_search_tool,
    )
    from git_metadata_extractor.agents.llm.agent_tools.ethz_research_collection_rag import (  # noqa: PLC0415
        make_ethz_research_collection_rag_search_tool,
    )
    from git_metadata_extractor.agents.llm.agent_tools.huggingface_rag import (  # noqa: PLC0415
        make_huggingface_rag_search_tool,
    )
    from git_metadata_extractor.agents.llm.agent_tools.oamonitor_rag import (  # noqa: PLC0415
        make_oamonitor_rag_search_tool,
    )
    from git_metadata_extractor.agents.llm.agent_tools.infoscience_rag import (  # noqa: PLC0415
        make_infoscience_rag_search_tool,
    )
    from git_metadata_extractor.agents.llm.agent_tools.openalex_rag import (  # noqa: PLC0415
        make_openalex_rag_search_tool,
    )
    from git_metadata_extractor.agents.llm.agent_tools.orcid_rag import (  # noqa: PLC0415
        make_orcid_rag_search_tool,
    )
    from git_metadata_extractor.agents.llm.agent_tools.renkulab_rag import (  # noqa: PLC0415
        make_renkulab_rag_search_tool,
    )
    from git_metadata_extractor.agents.llm.agent_tools.ror_rag import (  # noqa: PLC0415
        make_ror_rag_search_tool,
    )
    from git_metadata_extractor.agents.llm.agent_tools.selenium_fetch import (  # noqa: PLC0415
        make_fetch_link_content_tool,
    )
    from git_metadata_extractor.agents.llm.agent_tools.snsf_rag import (  # noqa: PLC0415
        make_snsf_rag_search_tool,
    )
    from git_metadata_extractor.agents.llm.agent_tools.zenodo_rag import (  # noqa: PLC0415
        make_zenodo_rag_search_tool,
    )

    tools: list[Any] = [
        # Always-on baseline (no provider dependency).
        make_repository_corpus_grep_tool(corpus_documents),
        make_duckduckgo_search_tool(cache=cache),
        make_fetch_link_content_tool(cache),
    ]
    if providers.orcid_rag is not None:
        tools.append(make_orcid_rag_search_tool(providers.orcid_rag))
    if providers.ror_rag is not None:
        tools.append(make_ror_rag_search_tool(providers.ror_rag))
    if providers.infoscience_rag is not None:
        tools.append(make_infoscience_rag_search_tool(providers.infoscience_rag))
    if providers.openalex_rag is not None:
        tools.append(make_openalex_rag_search_tool(providers.openalex_rag))
    if providers.zenodo_rag is not None:
        tools.append(make_zenodo_rag_search_tool(providers.zenodo_rag))
    if providers.oamonitor_rag is not None:
        tools.append(make_oamonitor_rag_search_tool(providers.oamonitor_rag))
    if providers.ethz_research_collection_rag is not None:
        tools.append(
            make_ethz_research_collection_rag_search_tool(
                providers.ethz_research_collection_rag,
            ),
        )
    if providers.huggingface_rag is not None:
        tools.append(make_huggingface_rag_search_tool(providers.huggingface_rag))
    if providers.renkulab_rag is not None:
        tools.append(make_renkulab_rag_search_tool(providers.renkulab_rag))
    if providers.snsf_rag is not None:
        tools.append(make_snsf_rag_search_tool(providers.snsf_rag))
    if providers.epfl_graph_rag is not None:
        tools.append(make_epfl_graph_rag_search_tool(providers.epfl_graph_rag))
    return tools


def _document_manifest(corpus_documents: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for document in corpus_documents:
        content = document.get("content")
        if not isinstance(content, str):
            continue
        rows.append(
            {
                "id": document.get("id"),
                "label": document.get("label"),
                "repository": document.get("repository"),
                "origin": document.get("origin"),
                "path": document.get("path"),
                "char_count": len(content),
                "line_count": len(content.splitlines()),
                "preview": _truncate_text(content, limit=MAX_PROMPT_DOC_PREVIEW_CHARS),
            },
        )
    return rows


class LLMContextSummaryAgentV2:
    """Compile raw context into a single markdown brief for downstream agents."""

    def __init__(
        self,
        *,
        llm_runtime: V2LLMRuntime | None = None,
        cache: ProviderCache | None = None,
    ) -> None:
        self._llm_runtime = llm_runtime or V2LLMRuntime()
        self._cache = cache

    async def run(
        self,
        context: dict[str, Any],
        providers: ProviderSet,
    ) -> AgentResult:
        # Scout mode (off by default) needs `providers` to wire RAG
        # search tools; the legacy path never used them but we keep the
        # parameter for ABI stability.
        scout_mode = _is_scout_mode_enabled()
        detected_type = _to_non_empty_string(context.get("detected_type")) or "repository"
        source_url = _to_non_empty_string(context.get("source_url")) or ""

        stamp_current_agent(
            name="context_summary_agent",
            context={"source_url": source_url} if source_url else {},
        )
        gathered_context = context.get("gathered_context")
        if not isinstance(gathered_context, dict):
            gathered_context = {}

        corpus_documents = _build_corpus_documents(detected_type, gathered_context)
        manifest = _document_manifest(corpus_documents)
        total_chars = sum(
            len(document.get("content", ""))
            for document in corpus_documents
            if isinstance(document.get("content"), str)
        )

        llm_input = {
            "detected_type": detected_type,
            "source_url": source_url,
            "document_count": len(corpus_documents),
            "total_chars": total_chars,
            "corpus_manifest": manifest,
        }
        context_json = json.dumps(llm_input, ensure_ascii=True, sort_keys=True)
        user_prompt = _USER_PROMPT_TEMPLATE.replace("{context_json}", context_json)

        warnings: list[str] = []
        if not corpus_documents:
            warnings.append(
                "context_summary_agent: no raw corpus documents available; using empty summary context",
            )

        # Pick prompt + tool catalog based on scout mode. Default path
        # is unchanged (2 tools, original prompt). Scout mode adds the
        # RAG providers' search tools so people / orgs / articles get
        # recon'd up-front.
        usage_limits: UsageLimits | None = None
        if scout_mode:
            system_prompt = load_prompt(_PROMPTS_PACKAGE, _SCOUT_PROMPT_FILENAME)
            tools = _build_scout_tools(
                providers=providers,
                cache=self._cache,
                corpus_documents=corpus_documents,
            )
            usage_limits = UsageLimits(
                request_limit=_SCOUT_REQUEST_LIMIT,
                tool_calls_limit=_SCOUT_TOOL_CALLS_LIMIT,
            )
            logger.info(
                "context_summary_agent: scout mode ON (%d tools, "
                "request_limit=%d, tool_calls_limit=%d)",
                len(tools),
                _SCOUT_REQUEST_LIMIT,
                _SCOUT_TOOL_CALLS_LIMIT,
            )
        else:
            system_prompt = _SYSTEM_PROMPT
            tools = [
                make_repository_corpus_grep_tool(corpus_documents),
                make_duckduckgo_search_tool(cache=self._cache),
            ]

        try:
            run_kwargs: dict[str, Any] = {}
            if usage_limits is not None:
                run_kwargs["usage_limits"] = usage_limits
            llm_result = await self._llm_runtime.run_json_prompt(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                output_type=LLMContextSummaryOutput,
                tools=tools,
                **run_kwargs,
            )
        except LLMRuntimeError as exc:
            warning = f"context_summary_agent: LLM call failed; proceeding without compiled summary ({exc})"
            warnings.append(warning)
            logger.warning(warning)
            return AgentResult(
                data={"summary_markdown": ""},
                warnings=warnings,
                raw_output={},
                is_partial=True,
                failure_reason=str(exc),
                stats={
                    "document_count": len(corpus_documents),
                    "total_chars": total_chars,
                },
            )

        payload = dict(llm_result.payload)
        summary_markdown = _to_non_empty_string(payload.get("summary_markdown")) or ""
        if not summary_markdown:
            warnings.append(
                "context_summary_agent: empty summary output; downstream agents will run without compiled summary",
            )

        return AgentResult(
            data={"summary_markdown": summary_markdown},
            warnings=warnings,
            raw_output=deepcopy(payload),
            model=llm_result.model,
            provider=llm_result.provider,
            tokens_prompt=llm_result.tokens_prompt,
            tokens_completion=llm_result.tokens_completion,
            stats={
                "agent_runtime": "llm",
                "document_count": len(corpus_documents),
                "total_chars": total_chars,
            },
        )
