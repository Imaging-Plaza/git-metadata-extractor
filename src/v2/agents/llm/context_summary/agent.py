from __future__ import annotations

import json
import logging
from copy import deepcopy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from src.v2.agents.llm._loader import load_prompt
from src.v2.agents.llm.agent_tools.duckduckgo_search import (
    make_duckduckgo_search_tool,
)
from src.v2.agents.llm.agent_tools.repository_corpus_grep import (
    make_repository_corpus_grep_tool,
)
from src.v2.agents.models import AgentResult, ProviderSet
from src.v2.llm.runtime import LLMRuntimeError, V2LLMRuntime

logger = logging.getLogger(__name__)

_PROMPTS_PACKAGE = "src.v2.agents.llm.context_summary.prompts"
_SYSTEM_PROMPT = load_prompt(_PROMPTS_PACKAGE, "system_prompt.md")
_USER_PROMPT_TEMPLATE = load_prompt(_PROMPTS_PACKAGE, "user_prompt.md")

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
    ) -> None:
        self._llm_runtime = llm_runtime or V2LLMRuntime()

    async def run(
        self,
        context: dict[str, Any],
        providers: ProviderSet,
    ) -> AgentResult:
        del providers
        detected_type = _to_non_empty_string(context.get("detected_type")) or "repository"
        source_url = _to_non_empty_string(context.get("source_url")) or ""
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

        try:
            llm_result = await self._llm_runtime.run_json_prompt(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                output_type=LLMContextSummaryOutput,
                tools=[
                    make_repository_corpus_grep_tool(corpus_documents),
                    make_duckduckgo_search_tool(),
                ],
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
