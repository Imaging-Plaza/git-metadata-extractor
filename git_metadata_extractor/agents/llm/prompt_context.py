from __future__ import annotations

from typing import Any

UPSTREAM_STAGE_OUTPUTS_JSON_CONTEXT_KEY = "upstream_stage_outputs_json"
USER_PROMPT_APPENDIX_CONTEXT_KEY = "user_prompt_appendix"


def append_runtime_prompt_context(base_prompt: str, context: dict[str, Any]) -> str:
    """Append optional runtime prompt sections from orchestrator context."""

    user_prompt = base_prompt

    upstream_json = context.get(UPSTREAM_STAGE_OUTPUTS_JSON_CONTEXT_KEY)
    if isinstance(upstream_json, str) and upstream_json.strip():
        user_prompt = (
            f"{user_prompt}\n\n"
            "## Upstream Stage Outputs (JSON)\n"
            f"{upstream_json}"
        )

    prompt_appendix = context.get(USER_PROMPT_APPENDIX_CONTEXT_KEY)
    if isinstance(prompt_appendix, str) and prompt_appendix.strip():
        user_prompt = (
            f"{user_prompt}\n\n"
            "## Additional Context (verbatim text)\n"
            f"{prompt_appendix}"
        )

    return user_prompt
