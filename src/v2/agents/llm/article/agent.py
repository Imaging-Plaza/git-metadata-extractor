from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from typing import Any

from pydantic import ValidationError

from src.v2.agents.llm._loader import load_prompt
from src.v2.agents.llm._payload_helpers import force_server_uuid
from src.v2.agents.llm._verdict_cache import (
    get_cached_agent_verdict,
    store_agent_verdict,
)
from src.v2.agents.llm.agent_tools.infoscience_publications import (
    make_infoscience_publications_search_tool,
)
from src.v2.agents.llm.agent_tools.infoscience_rag import (
    make_infoscience_rag_fetch_chunks_tool,
    make_infoscience_rag_fetch_records_tool,
    make_infoscience_rag_search_tool,
)
from src.v2.agents.llm.agent_tools.selenium_fetch import (
    make_fetch_link_content_tool,
)
from src.v2.agents.llm.prompt_context import append_runtime_prompt_context
from src.v2.agents.models import AgentResult, ProviderSet, generate_uuid
from src.v2.ingest.cache import ProviderCache
from src.v2.observation.query_log import stamp_current_agent
from src.v2.schema.models.agent import AgentArticleShape
from src.v2.schema.models.strict import ArticleModel
from src.v2.agents.llm.runtime import LLMRuntimeError, V2LLMRuntime

README_CONTEXT_MAX_CHARS = 2000
GIMIE_JSONLD_MAX_CHARS = 4000
MAX_CONTEXT_ENTITIES = 30

# Sentinel strings the LLM emits when it has no real identifier but tries to
# emit an article anyway. These bypass placeholder-DOI checks (which look
# for `10.0000/...`) so we catch them explicitly here.
_IDENTIFIER_SENTINELS: frozenset[str] = frozenset(
    {"unknown", "n/a", "na", "none", "null", "tbd", "todo", "?", "-"},
)


def _is_real_doi(value: Any) -> bool:
    """Return True for a string that *plausibly* looks like a real DOI.

    A real DOI starts with `10.` followed by a 4-9 digit registrant prefix,
    a slash, and a non-empty suffix. Placeholder DOIs `10.0000/...` are also
    rejected as they're a known LLM-hallucination pattern.
    """
    if not isinstance(value, str):
        return False
    candidate = value.strip()
    if not candidate:
        return False
    for prefix in (
        "https://doi.org/",
        "http://doi.org/",
        "https://dx.doi.org/",
        "http://dx.doi.org/",
    ):
        if candidate.lower().startswith(prefix):
            candidate = candidate[len(prefix) :]
            break
    if not candidate.startswith("10.") or "/" not in candidate:
        return False
    # `10.0000/` is reserved for testing — never a real DOI.
    if candidate.lower().startswith("10.0000/"):
        return False
    return True


def _has_real_article_identifier(payload: dict[str, Any]) -> bool:
    """An article must carry at least one verifiable identifier."""

    schema_identifier = payload.get("schema:identifier")
    if isinstance(schema_identifier, str) and schema_identifier.strip().lower() in _IDENTIFIER_SENTINELS:
        schema_identifier = None
    if _is_real_doi(schema_identifier):
        return True
    identifiers = payload.get("identifiers")
    if isinstance(identifiers, dict):
        nested_identifier = identifiers.get("schema:identifier")
        if (
            isinstance(nested_identifier, str)
            and nested_identifier.strip().lower() not in _IDENTIFIER_SENTINELS
            and _is_real_doi(nested_identifier)
        ):
            return True
        infosci = identifiers.get("pulse:infoscienceArticleIdentifier")
        if isinstance(infosci, str) and infosci.strip():
            return True
    direct_infosci = payload.get("pulse:infoscienceArticleIdentifier")
    if isinstance(direct_infosci, str) and direct_infosci.strip():
        return True
    return False

_PROMPTS_PACKAGE = "src.v2.agents.llm.article.prompts"
_SYSTEM_PROMPT = load_prompt(_PROMPTS_PACKAGE, "system_prompt.md")
_USER_PROMPT_TEMPLATE = load_prompt(_PROMPTS_PACKAGE, "user_prompt.md")


def _resolve_article_seed(context: dict[str, Any]) -> str:
    for key in ("article_seed", "full_name", "username", "org_name"):
        value = context.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    message = "Article context is missing article_seed/full_name/username/org_name"
    raise ValueError(message)


def _strict_validate(payload: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    try:
        ArticleModel.model_validate(payload)
    except ValidationError as exc:
        for error in exc.errors():
            field = " -> ".join(str(loc) for loc in error["loc"])
            warnings.append(f"Strict schema warning at {field}: {error['msg']}")
    return warnings


def _list_of_dicts(value: Any, *, max_items: int = MAX_CONTEXT_ENTITIES) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    collected: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        if len(collected) >= max_items:
            break
        collected.append(deepcopy(item))
    return collected


def _repository_context_summary(repository_context: Any) -> dict[str, Any] | None:
    if not isinstance(repository_context, dict) or not repository_context:
        return None

    summary: dict[str, Any] = {}
    metadata = repository_context.get("metadata")
    if isinstance(metadata, dict) and metadata:
        summary["metadata"] = {
            key: metadata.get(key)
            for key in (
                "name",
                "full_name",
                "description",
                "owner",
                "created_at",
                "updated_at",
                "pushed_at",
            )
            if metadata.get(key) is not None
        }

    contributors = repository_context.get("contributors")
    if isinstance(contributors, list) and contributors:
        summary["contributors"] = deepcopy(contributors[:MAX_CONTEXT_ENTITIES])

    languages = repository_context.get("languages")
    if isinstance(languages, dict) and languages:
        summary["languages"] = deepcopy(languages)

    readme = repository_context.get("readme_content")
    if isinstance(readme, str) and readme:
        summary["readme_content"] = readme[:README_CONTEXT_MAX_CHARS]

    gimie_jsonld = repository_context.get("gimie_jsonld")
    if isinstance(gimie_jsonld, dict) and gimie_jsonld:
        summary["gimie_jsonld"] = json.dumps(
            gimie_jsonld,
            ensure_ascii=True,
        )[:GIMIE_JSONLD_MAX_CHARS]

    return summary or None


class LLMArticleAgentV2:
    """LLM-backed agent that produces a schema:ScholarlyArticle entity."""

    def __init__(
        self,
        *,
        llm_runtime: V2LLMRuntime | None = None,
        llm_call_timeout_seconds: float = 180.0,
        cache: ProviderCache | None = None,
    ) -> None:
        if llm_call_timeout_seconds <= 0:
            message = "llm_call_timeout_seconds must be > 0"
            raise ValueError(message)
        self._llm_runtime = llm_runtime or V2LLMRuntime()
        self._llm_call_timeout_seconds = float(llm_call_timeout_seconds)
        self._cache = cache

    async def run(
        self,
        context: dict[str, Any],
        providers: ProviderSet,
    ) -> AgentResult:
        article_seed = _resolve_article_seed(context)

        stamp_current_agent(
            name="article_agent",
            context={"article_seed": article_seed} if isinstance(article_seed, str) else {},
        )

        full_name = context.get("full_name")
        identity = (
            {"full_name": full_name.strip().lower()}
            if isinstance(full_name, str) and full_name.strip()
            else None
        )
        is_root = bool(context.get("agent_is_root"))
        cached_result = get_cached_agent_verdict(
            self._cache,
            agent_name="article",
            identity=identity,
            is_root=is_root,
        )
        if cached_result is not None:
            return cached_result

        uuid_value = context.get("uuid")
        if not isinstance(uuid_value, str) or not uuid_value.strip():
            uuid_value = generate_uuid()

        llm_input: dict[str, Any] = {
            "article_seed": article_seed,
            "uuid": uuid_value,
            "detected_type": context.get("detected_type"),
            "source_url": context.get("source_url"),
            "full_name": context.get("full_name"),
            "username": context.get("username"),
            "org_name": context.get("org_name"),
            "known_persons": _list_of_dicts(context.get("known_persons")),
            "known_organizations": _list_of_dicts(context.get("known_organizations")),
            "known_repositories": _list_of_dicts(context.get("known_repositories"), max_items=5),
            "person_derivations": _list_of_dicts(context.get("person_derivations")),
            "organization_derivations": _list_of_dicts(
                context.get("organization_derivations"),
            ),
            "repository_derivations": _list_of_dicts(
                context.get("repository_derivations"),
                max_items=5,
            ),
            "typed_entity_buckets": context.get("typed_entity_buckets"),
        }

        repository_context = _repository_context_summary(context.get("repository_context"))
        if repository_context:
            llm_input["repository_context"] = repository_context

        pipeline_outputs = context.get("pipeline_outputs")
        if isinstance(pipeline_outputs, dict) and pipeline_outputs:
            llm_input["pipeline_outputs"] = pipeline_outputs

        context_json = json.dumps(llm_input, ensure_ascii=True, sort_keys=True, default=str)
        user_prompt = _USER_PROMPT_TEMPLATE.replace("{context_json}", context_json)
        user_prompt = append_runtime_prompt_context(user_prompt, context)

        tools = [make_fetch_link_content_tool(self._cache)]
        if providers.infoscience is not None:
            tools.append(
                make_infoscience_publications_search_tool(providers.infoscience),
            )
        if providers.infoscience_rag is not None:
            tools.append(make_infoscience_rag_search_tool(providers.infoscience_rag))
            tools.append(
                make_infoscience_rag_fetch_chunks_tool(providers.infoscience_rag),
            )
            tools.append(
                make_infoscience_rag_fetch_records_tool(providers.infoscience_rag),
            )

        try:
            llm_result = await asyncio.wait_for(
                self._llm_runtime.run_json_prompt(
                    system_prompt=_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    output_type=AgentArticleShape,
                    tools=tools,
                ),
                timeout=self._llm_call_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            message = (
                f"{article_seed} — LLM call timed out after "
                f"{self._llm_call_timeout_seconds:.1f}s"
            )
            raise LLMRuntimeError(message) from exc
        except LLMRuntimeError:
            raise
        except Exception as exc:
            raise LLMRuntimeError(str(exc)) from exc

        payload = {key: value for key, value in llm_result.payload.items() if value is not None}
        overrides = context.get("agent_overrides")
        if isinstance(overrides, dict):
            payload.update(overrides)

        # Drop the article entirely if the LLM didn't ground it in a real
        # identifier. The agent should not emit an entity whose only
        # `schema:identifier` is a placeholder DOI (`10.0000/...`) or a
        # sentinel like `UNKNOWN` / `N/A` / `TBD`. The downstream
        # `validate_articles` stage would catch these too, but dropping
        # them here keeps `excluded_entities` and the prompt context for
        # later stages clean.
        if payload and not _has_real_article_identifier(payload):
            warning = (
                f"{article_seed} — article dropped: no real DOI or "
                f"infoscience identifier (got "
                f"schema:identifier={payload.get('schema:identifier')!r})."
            )
            empty_result = AgentResult(
                data={},
                warnings=[warning],
                raw_output=deepcopy(payload),
                model=llm_result.model,
                provider=llm_result.provider,
                tokens_prompt=llm_result.tokens_prompt,
                tokens_completion=llm_result.tokens_completion,
                stats={
                    "agent_runtime": "llm",
                    "articles": [],
                    "article_count": 0,
                    "derivation": {
                        "article_seed": article_seed,
                        "dropped_reason": "no_real_identifier",
                        "rejected_schema_identifier": payload.get(
                            "schema:identifier",
                        ),
                    },
                },
            )
            store_agent_verdict(
                self._cache,
                agent_name="article",
                identity=identity,
                result=empty_result,
            )
            return empty_result

        force_server_uuid(payload, uuid_value)

        raw_output = deepcopy(payload)
        validation_warnings = _strict_validate(payload)

        result = AgentResult(
            data=payload,
            warnings=validation_warnings,
            raw_output=raw_output,
            model=llm_result.model,
            provider=llm_result.provider,
            tokens_prompt=llm_result.tokens_prompt,
            tokens_completion=llm_result.tokens_completion,
            stats={
                "agent_runtime": "llm",
                "articles": [deepcopy(payload)] if payload else [],
                "article_count": 1 if payload else 0,
                "derivation": {
                    "article_seed": article_seed,
                    "article_id": payload.get("id"),
                    "author_count": len(payload.get("schema:author", []))
                    if isinstance(payload.get("schema:author"), list)
                    else 0,
                },
            },
        )
        store_agent_verdict(
            self._cache,
            agent_name="article",
            identity=identity,
            result=result,
        )
        return result
