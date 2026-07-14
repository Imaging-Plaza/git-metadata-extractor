"""Cross-vendor judge for the v2 terminal-agent PoC.

Single LLM call that votes pass/fail on the executor's output.jsonld.
Cross-vendor on purpose: if executor and judge are the same model family
they auto-confirm too easily, and the whole point of the judge is
independent verification of groundedness and coverage.

The judge looks at three artefacts:
- `output.jsonld` — what the executor produced
- `transcript.jsonl` — the executor's tool-call trace (for groundedness:
  every non-trivial assertion should be backed by a tool call)
- `gimie.jsonld` — the deterministic context (for coverage: did the
  executor at least account for every contributor / org gimie surfaced?)
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

logger = logging.getLogger(__name__)

Verdict = Literal["pass", "fail"]

# Per-section input cap (UTF-8 bytes ≈ chars). Keeps total prompt under
# ~150KB even when judging large repos. DeepSeek-V3.2 has 128K context
# but stuffing the prompt costs latency and money for marginal value.
_MAX_SECTION_CHARS = 50_000


class JudgeUnavailableError(RuntimeError):
    """Raised when the judge cannot run (missing credentials, etc.)."""


@dataclass(slots=True)
class JudgeReport:
    verdict: Verdict
    missing: list[str]
    hallucinated: list[str]
    rationale: str
    raw: dict[str, Any]
    tokens_prompt: int = 0
    tokens_completion: int = 0


@dataclass(slots=True)
class TerminalJudge:
    model: str
    base_url: str
    api_key_env: str
    temperature: float = 0.0
    max_tokens: int = 4000
    timeout_s: int = 300

    def judge(
        self,
        *,
        output_jsonld: dict[str, Any],
        transcript_path: Path | None,
        gimie_jsonld: dict[str, Any] | None,
        shacl_conforms: bool | None = None,
        shacl_errors: list[str] | None = None,
    ) -> JudgeReport:
        """Score the executor's output. Returns a structured report.

        The judge sees four signals: the produced `output.jsonld`, the
        deterministic gimie context, the executor's tool-call transcript,
        and (optionally) the SHACL validation results from the runner.
        """
        api_key = os.environ.get(self.api_key_env)
        if not api_key:
            msg = f"judge: missing env var {self.api_key_env}"
            raise JudgeUnavailableError(msg)

        try:
            from openai import OpenAI  # noqa: PLC0415 — optional in unit tests
        except ImportError as err:  # noqa: BLE001
            msg = f"judge: openai client not available ({err})"
            raise JudgeUnavailableError(msg) from err

        client = OpenAI(base_url=self.base_url, api_key=api_key, timeout=float(self.timeout_s))
        messages = self._build_messages(
            output_jsonld=output_jsonld,
            transcript_path=transcript_path,
            gimie_jsonld=gimie_jsonld,
            shacl_conforms=shacl_conforms,
            shacl_errors=shacl_errors or [],
        )
        raw, usage = self._call_with_structured_output(client, messages)
        return JudgeReport(
            verdict=_coerce_verdict(raw.get("verdict")),
            missing=_coerce_str_list(raw.get("missing")),
            hallucinated=_coerce_str_list(raw.get("hallucinated")),
            rationale=str(raw.get("rationale", "")),
            raw=raw,
            tokens_prompt=int(usage.get("prompt_tokens", 0) or 0),
            tokens_completion=int(usage.get("completion_tokens", 0) or 0),
        )

    # ------------------------------------------------------------------ prompt

    def _build_messages(
        self,
        *,
        output_jsonld: dict[str, Any],
        transcript_path: Path | None,
        gimie_jsonld: dict[str, Any] | None,
        shacl_conforms: bool | None,
        shacl_errors: list[str],
    ) -> list[dict[str, str]]:
        output_blob = _truncate(json.dumps(output_jsonld, indent=2, ensure_ascii=False))
        gimie_blob = _truncate(
            json.dumps(gimie_jsonld or {}, indent=2, ensure_ascii=False, default=str),
        )
        transcript_blob = ""
        if transcript_path is not None and transcript_path.exists():
            text = transcript_path.read_text(encoding="utf-8", errors="replace")
            # Tail-truncate: late events (final tool calls, agent_end) carry
            # more signal than early lifecycle events.
            transcript_blob = text[-_MAX_SECTION_CHARS:]

        # Cap SHACL errors at the same per-section budget — long shapes
        # graphs can produce hundreds of warnings.
        shacl_blob = _truncate("\n".join(shacl_errors)) if shacl_errors else "(none)"
        if shacl_conforms is None:
            shacl_header = "## SHACL validation (not run)\n\n"
        elif shacl_conforms:
            shacl_header = "## SHACL validation: CONFORMS ✓\n\n"
        else:
            shacl_header = "## SHACL validation: FAILS\n\n"

        # Inject the closed-shape cheatsheet so the judge knows the v2
        # ontology's allowed properties per @type — without this, the
        # judge mis-flags properties from gimie that are NOT in the
        # closed shape as "missing", penalising correct outputs.
        cheatsheet = _safe_cheatsheet()

        system = (
            "You are an independent quality reviewer for an information-extraction agent. "
            "You receive the agent's output, a deterministic ground-truth context, "
            "the agent's tool-call transcript, and structural validation results. "
            "You output ONLY a single JSON object that conforms to the schema described. "
            "You never apologise, explain, or add prose outside the JSON."
        )
        user = (
            "Score the agent's output against the artefacts below.\n\n"
            "## v2 ontology cheatsheet (CLOSED shapes)\n"
            "These are the **only** properties each `@type` is allowed to "
            "carry. The agent operates under this constraint. **Do NOT report "
            "as `missing` any property that the closed shape forbids — the "
            "agent is correct to omit it.** Examples of properties the "
            "closed shapes forbid (do not flag): `schema:description`, "
            "`schema:keywords`, `schema:contributor`, `schema:version`, "
            "`schema:downloadUrl` on SoftwareSourceCode; `schema:legalName`, "
            "`schema:logo`, `schema:url`, `schema:image` on Organization; "
            "`schema:affiliation` on Person (use Membership entities instead).\n"
            "**Also**: `identifiers.uuid` is a pipeline-internal placeholder "
            "convention, NOT a SHACL constraint — entities that omit it are "
            "still valid against the closed shape. Do NOT flag missing "
            "`identifiers.uuid` as a missing property.\n"
            "```\n"
            f"{cheatsheet}\n"
            "```\n\n"
            "## output.jsonld (what the agent produced)\n"
            "```json\n"
            f"{output_blob}\n"
            "```\n\n"
            "## gimie.jsonld (deterministic ground-truth context for the same repo)\n"
            "```json\n"
            f"{gimie_blob}\n"
            "```\n\n"
            "## transcript (tail of the agent's tool-call event stream)\n"
            "Tool results in this transcript are FIRST-CLASS evidence. "
            "An identifier (ROR, ORCID, DOI) supplied by a tool result counts "
            "as supported even if it is not in gimie. Treat the transcript as "
            "an extension of the ground truth, not as inferior to it.\n"
            "```\n"
            f"{transcript_blob}\n"
            "```\n\n"
            f"{shacl_header}"
            "Violations and warnings from the v2 ontology shapes graph:\n"
            "```\n"
            f"{shacl_blob}\n"
            "```\n\n"
            "Scoring rules:\n"
            "- `missing`: ONLY entities or properties that (a) are present "
            "in gimie/transcript AND (b) are in the v2 closed shape AND "
            "(c) are absent from output. Properties that the closed shape "
            "forbids are NOT missing — they are correctly omitted. Required "
            "properties absent from output ARE missing.\n"
            "- `hallucinated`: claims in output for which you find no "
            "supporting evidence in gimie OR the transcript. A ROR/ORCID/DOI "
            "surfaced by a `search_*` tool call IS supported — do not flag "
            "it. Only flag claims with no tool-call backing.\n"
            "- SHACL violations: weight more heavily than missing — if SHACL "
            "fails, the verdict is fail. If SHACL conforms, judge purely on "
            "coverage and groundedness.\n"
            "- `verdict`: \"pass\" if and only if missing == [] AND "
            "hallucinated == [] AND SHACL conforms. Otherwise \"fail\".\n"
            "- `rationale`: one short paragraph (<= 5 sentences) explaining "
            "the call, explicitly noting whether SHACL conformed and which "
            "of the three signals dominated the decision.\n\n"
            "Output JSON only, matching: "
            '{"verdict":"pass|fail","missing":[...],"hallucinated":[...],"rationale":"..."}.'
        )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]

    # ------------------------------------------------------------------ call

    def _call_with_structured_output(
        self,
        client: Any,
        messages: list[dict[str, str]],
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        """Try `json_schema` response_format; fall back to `json_object`.

        Returns ``(parsed_payload, usage_dict)``. Some OpenAI-compatible
        backends (vLLM/SGLang) don't yet honour the `json_schema`
        response format; the fallback to `json_object` relies on the
        prompt to keep the shape correct.
        """
        schema = {
            "type": "object",
            "additionalProperties": False,
            "required": ["verdict", "missing", "hallucinated", "rationale"],
            "properties": {
                "verdict": {"type": "string", "enum": ["pass", "fail"]},
                "missing": {"type": "array", "items": {"type": "string"}},
                "hallucinated": {"type": "array", "items": {"type": "string"}},
                "rationale": {"type": "string"},
            },
        }
        try:
            completion = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                response_format={
                    "type": "json_schema",
                    "json_schema": {"name": "judge_report", "schema": schema, "strict": True},
                },
            )
        except Exception as err:  # noqa: BLE001 — schema mode may not be supported
            logger.info("judge: json_schema mode rejected (%s), falling back to json_object", err)
            completion = client.chat.completions.create(
                model=self.model,
                messages=messages,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
                response_format={"type": "json_object"},
            )

        usage_obj = getattr(completion, "usage", None)
        usage_dict: dict[str, Any] = {}
        if usage_obj is not None:
            # The OpenAI SDK exposes usage as a model with `.prompt_tokens`
            # / `.completion_tokens` — handle both the SDK object and a
            # raw dict (in case a custom backend returns one).
            if hasattr(usage_obj, "model_dump"):
                usage_dict = usage_obj.model_dump() or {}
            elif isinstance(usage_obj, dict):
                usage_dict = usage_obj
            else:
                usage_dict = {
                    "prompt_tokens": getattr(usage_obj, "prompt_tokens", 0),
                    "completion_tokens": getattr(usage_obj, "completion_tokens", 0),
                    "total_tokens": getattr(usage_obj, "total_tokens", 0),
                }

        raw_text = (completion.choices[0].message.content or "").strip()
        if not raw_text:
            return (
                {"verdict": "fail", "missing": [], "hallucinated": [], "rationale": "judge returned empty body"},
                usage_dict,
            )
        try:
            return json.loads(raw_text), usage_dict
        except json.JSONDecodeError as err:
            logger.warning("judge: non-JSON response (%s); raw=%r", err, raw_text[:500])
            return (
                {
                    "verdict": "fail",
                    "missing": [],
                    "hallucinated": [],
                    "rationale": f"judge response was not valid JSON: {err}",
                },
                usage_dict,
            )


def _truncate(text: str) -> str:
    if len(text) <= _MAX_SECTION_CHARS:
        return text
    head = text[: _MAX_SECTION_CHARS - 200]
    return f"{head}\n…[truncated {len(text) - len(head)} chars]"


def _safe_cheatsheet() -> str:
    """Return the v2 closed-shape cheatsheet for inclusion in the judge prompt.

    Mirrors what the runner writes to `<workdir>/schema_cheatsheet.md`,
    so the judge's idea of "what's allowed" matches the agent's. Soft-
    fails to a short stub if the shapes graph is unreachable — the
    judge will be slightly less calibrated but still functional.
    """
    try:
        from git_metadata_extractor.experimental.terminal.schema_cheatsheet import build_cheatsheet  # noqa: PLC0415

        return build_cheatsheet()
    except Exception as err:  # noqa: BLE001
        logger.warning("judge: cheatsheet unavailable (%s)", err)
        return (
            "(closed-shape cheatsheet unavailable — judge with caution; "
            "do not flag schema.org properties that may not be in the "
            "v2 ontology's closed shapes)"
        )


def _coerce_verdict(value: Any) -> Verdict:
    if isinstance(value, str) and value.lower() in {"pass", "fail"}:
        return value.lower()  # type: ignore[return-value]
    return "fail"


def _coerce_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item is not None]


__all__ = ["JudgeReport", "JudgeUnavailableError", "TerminalJudge", "Verdict"]
