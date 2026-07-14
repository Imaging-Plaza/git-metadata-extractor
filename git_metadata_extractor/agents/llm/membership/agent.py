from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

import asyncio
import json
from copy import deepcopy
from datetime import date
from typing import Any

from pydantic import ValidationError

from git_metadata_extractor.agents.llm._loader import load_prompt
from git_metadata_extractor.agents.llm._payload_helpers import force_server_uuid
from git_metadata_extractor.agents.llm.agent_tools.orcid_person import make_orcid_person_tool
from git_metadata_extractor.agents.llm.agent_tools.query_orcid import make_query_orcid_tool
from git_metadata_extractor.agents.llm.agent_tools.selenium_fetch import (
    make_fetch_link_content_tool,
)
from git_metadata_extractor.agents.llm.prompt_context import append_runtime_prompt_context
from git_metadata_extractor.agents.models import AgentResult, ProviderSet, generate_uuid
from git_metadata_extractor.providers.cache import ProviderCache
from git_metadata_extractor.observation.query_log import stamp_current_agent
from git_metadata_extractor.schema.models.agent import AgentMembershipShape
from git_metadata_extractor.schema.models.strict import MembershipModel
from git_metadata_extractor.agents.llm.runtime import LLMRuntimeError, V2LLMRuntime

MAX_CONTEXT_ENTITIES = 30

_PROMPTS_PACKAGE = "git_metadata_extractor.agents.llm.membership.prompts"
_SYSTEM_PROMPT = load_prompt(_PROMPTS_PACKAGE, "system_prompt.md")
_USER_PROMPT_TEMPLATE = load_prompt(_PROMPTS_PACKAGE, "user_prompt.md")


def _resolve_membership_seed(context: dict[str, Any]) -> str:
    seed = context.get("membership_seed")
    if isinstance(seed, str) and seed.strip():
        return seed.strip()

    known_persons = context.get("known_persons")
    if isinstance(known_persons, list):
        for person in known_persons:
            if not isinstance(person, dict):
                continue
            person_id = person.get("id")
            if isinstance(person_id, str) and person_id.strip():
                return person_id.strip()

    message = "Membership context is missing membership_seed or known person id"
    raise ValueError(message)


def _strict_validate(payload: dict[str, Any]) -> list[str]:
    warnings: list[str] = []
    try:
        MembershipModel.model_validate(payload)
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


def _country_code_from_org(organization: Any) -> str | None:
    """Best-effort 2-letter country code from an org payload.

    Looks at common shapes produced by upstream stages: a bare
    ``country_code``, a nested ``country.country_code`` (ROR's own
    serialisation), or an ``addresses[0].country_code`` (also ROR).
    Case-normalised; returns ``None`` when no plausible code is found.
    """
    if not isinstance(organization, dict):
        return None
    direct = organization.get("country_code")
    if isinstance(direct, str) and len(direct.strip()) == 2:
        return direct.strip().upper()
    country = organization.get("country")
    if isinstance(country, dict):
        nested = country.get("country_code") or country.get("code")
        if isinstance(nested, str) and len(nested.strip()) == 2:
            return nested.strip().upper()
    addresses = organization.get("addresses")
    if isinstance(addresses, list) and addresses:
        first = addresses[0]
        if isinstance(first, dict):
            code = first.get("country_code") or first.get("code")
            if isinstance(code, str) and len(code.strip()) == 2:
                return code.strip().upper()
    return None


def _collect_allowed_org_ids(
    *,
    target_organizations: list[dict[str, Any]],
    known_organizations: Any,
    organization_derivations: Any,
) -> set[str]:
    """Return the set of org `@id`s the LLM is allowed to point Memberships at.

    The membership agent's job is to express employment/affiliation for a
    target person inside the graph that other agents have already built.
    A Membership target outside this set is, in practice, an LLM-invented
    cross-reference (typically from `query_orcid` returning a name-fuzzy
    hit at an unrelated company). Returns an empty set when no
    organizations have been surfaced at all — then the filter degrades
    open (lets the LLM's choice through) so we don't strangle deployments
    that genuinely run without an org-detection upstream.
    """
    allowed: set[str] = set()
    for org in target_organizations:
        if isinstance(org, dict):
            oid = org.get("id") or org.get("@id")
            if isinstance(oid, str) and oid.strip():
                allowed.add(oid.strip())
    for collection in (known_organizations, organization_derivations):
        if not isinstance(collection, list):
            continue
        for org in collection:
            if isinstance(org, dict):
                oid = org.get("id") or org.get("@id")
                if isinstance(oid, str) and oid.strip():
                    allowed.add(oid.strip())
    return allowed


def _infer_target_country_code(
    *,
    target_organizations: list[dict[str, Any]],
    repository_context: Any,
    pipeline_outputs: Any,
) -> str | None:
    """Pick the country code the LLM should default-bias toward.

    Priority: explicit `target_organizations[*].country_code` >
    repository's owning-org country (from `repository_context.owning_org`
    or `pipeline_outputs.organization.country_code`). Returns ``None``
    when nothing is grounded — the LLM then has no bias and falls back
    to the prompt's general counter-rules without a CH default.
    """
    for org in target_organizations:
        code = _country_code_from_org(org)
        if code:
            return code
    if isinstance(repository_context, dict):
        owning = repository_context.get("owning_org") or repository_context.get("owner")
        code = _country_code_from_org(owning)
        if code:
            return code
    if isinstance(pipeline_outputs, dict):
        org_payload = pipeline_outputs.get("organization") or pipeline_outputs.get(
            "owning_organization",
        )
        if isinstance(org_payload, dict):
            code = _country_code_from_org(org_payload)
            if code:
                return code
    return None


class LLMMembershipAgentV2:
    """LLM-backed agent that produces an org:Membership entity."""

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
        membership_seed = _resolve_membership_seed(context)

        stamp_current_agent(
            name="membership_agent",
            context={"membership_seed": membership_seed},
        )

        uuid_value = context.get("uuid")
        if not isinstance(uuid_value, str) or not uuid_value.strip():
            uuid_value = generate_uuid()

        llm_input: dict[str, Any] = {
            "membership_seed": membership_seed,
            "uuid": uuid_value,
            "detected_type": context.get("detected_type"),
            "source_url": context.get("source_url"),
            "known_persons": _list_of_dicts(context.get("known_persons")),
            "known_organizations": _list_of_dicts(context.get("known_organizations")),
            "person_derivations": _list_of_dicts(context.get("person_derivations")),
            "organization_derivations": _list_of_dicts(
                context.get("organization_derivations"),
            ),
            "typed_entity_buckets": context.get("typed_entity_buckets"),
        }

        target_person = context.get("target_person")
        if isinstance(target_person, dict) and target_person:
            llm_input["target_person"] = deepcopy(target_person)

        target_organizations = context.get("target_organizations")
        normalized_target_organizations = _list_of_dicts(target_organizations)
        if normalized_target_organizations:
            llm_input["target_organizations"] = normalized_target_organizations

        repository_context = context.get("repository_context")
        if isinstance(repository_context, dict) and repository_context:
            llm_input["repository_context"] = deepcopy(repository_context)

        pipeline_outputs = context.get("pipeline_outputs")
        if isinstance(pipeline_outputs, dict) and pipeline_outputs:
            llm_input["pipeline_outputs"] = pipeline_outputs

        # Country prior derived from the repo's owning org. Without
        # this, the LLM happily stamps Memberships to RaySearch (SE),
        # 10X Genomics (SE), Volvo Cars (SE), Spotify (SE), Statistics
        # Botswana, etc. for GitHub contributors of EPFL/SDSC repos
        # whose usernames merely *resemble* unrelated company slugs.
        # The prompt's "Counter-rules" section instructs the model to
        # default-reject candidate orgs whose ROR country differs.
        target_country_code = _infer_target_country_code(
            target_organizations=normalized_target_organizations,
            repository_context=repository_context,
            pipeline_outputs=pipeline_outputs,
        )
        if target_country_code:
            llm_input["target_country_code"] = target_country_code

        context_json = json.dumps(llm_input, ensure_ascii=True, sort_keys=True, default=str)
        user_prompt = _USER_PROMPT_TEMPLATE.replace("{context_json}", context_json)
        user_prompt = append_runtime_prompt_context(user_prompt, context)
        tools = [make_fetch_link_content_tool(self._cache)]
        if providers.orcid is not None:
            tools.append(make_orcid_person_tool(providers.orcid))
            tools.append(
                make_query_orcid_tool(
                    providers.orcid,
                    orcid_rag_provider=providers.orcid_rag,
                ),
            )

        try:
            llm_result = await asyncio.wait_for(
                self._llm_runtime.run_json_prompt(
                    system_prompt=_SYSTEM_PROMPT,
                    user_prompt=user_prompt,
                    output_type=AgentMembershipShape,
                    tools=tools,
                ),
                timeout=self._llm_call_timeout_seconds,
            )
        except asyncio.TimeoutError as exc:
            message = (
                f"{membership_seed} — LLM call timed out after "
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

        force_server_uuid(payload, uuid_value)

        # Defend against malformed and inverted Membership dates from ORCID:
        # invalid dates like "2000-09-31" (Sept has 30 days) trip the SHACL
        # `xsd:date` literal check; inverted ranges trip `lessThanOrEquals`.
        # Drop unparseable values, then swap when needed.
        def _safe_date(value: object) -> str | None:
            if not isinstance(value, str):
                return None
            try:
                return date.fromisoformat(value[:10]).isoformat()
            except ValueError:
                return None

        beg = _safe_date(payload.get("time:hasBeginning"))
        end = _safe_date(payload.get("time:hasEnd"))
        if "time:hasBeginning" in payload:
            payload["time:hasBeginning"] = beg
        if "time:hasEnd" in payload:
            payload["time:hasEnd"] = end
        if beg and end and beg > end:
            payload["time:hasBeginning"], payload["time:hasEnd"] = end, beg

        # Spurious-membership filter — code-level enforcement of the
        # emit-or-skip prompt rules. The LLM was observed ignoring the
        # counter-rules in `system_prompt.md`: it still stamps
        # Memberships to ROR orgs that only matched via a `query_orcid`
        # name fuzzy hit (e.g. `jamalsenouci` (GitHub contributor of an
        # EPFL repo) → Spotify (ror.org/00hbd6420) just because some
        # *other* "Jamal Senouci" works at Spotify). Reject any
        # Membership whose target org isn't reachable from the
        # context — the cost of a false negative (legitimate ORCID
        # employment dropped because the org wasn't surfaced upstream)
        # is far smaller than the false-positive misattribution noise.
        target_org_ref = payload.get("org:organization")
        if isinstance(target_org_ref, dict):
            target_org_id = target_org_ref.get("@id") or target_org_ref.get("id")
        else:
            target_org_id = target_org_ref
        if isinstance(target_org_id, str) and target_org_id.strip():
            allowed_org_ids = _collect_allowed_org_ids(
                target_organizations=normalized_target_organizations,
                known_organizations=llm_input.get("known_organizations", []),
                organization_derivations=llm_input.get("organization_derivations", []),
            )
            if allowed_org_ids and target_org_id.strip() not in allowed_org_ids:
                warning = (
                    f"llm_membership: dropping Membership {payload.get('id')!r} — "
                    f"target org {target_org_id!r} not present in known_organizations "
                    "(likely a spurious `query_orcid` name match; the LLM ignored "
                    "the emit-or-skip rules in the system prompt)."
                )
                logger.info(warning)
                return AgentResult(
                    data={},
                    warnings=[warning],
                    raw_output={},
                    model=llm_result.model,
                    provider=llm_result.provider,
                    tokens_prompt=llm_result.tokens_prompt,
                    tokens_completion=llm_result.tokens_completion,
                    stats={
                        "agent_runtime": "llm",
                        "memberships": [],
                        "membership_count": 0,
                        "derivation": {
                            "membership_seed": membership_seed,
                            "skipped_reason": "org_not_in_context",
                            "target_org_id": target_org_id,
                        },
                    },
                )

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
            stats={
                "agent_runtime": "llm",
                "memberships": [deepcopy(payload)] if payload else [],
                "membership_count": 1 if payload else 0,
                "derivation": {
                    "membership_seed": membership_seed,
                    "membership_id": payload.get("id"),
                    "person_id": membership_seed,
                    "organization_id": payload.get("org:organization"),
                },
            },
        )
