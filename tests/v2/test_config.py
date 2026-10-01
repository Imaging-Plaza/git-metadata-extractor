from __future__ import annotations

import pytest

from git_metadata_extractor.agents.runtime import AgentRuntime
from git_metadata_extractor.config import V2Config

V2_CONFIG_ENV_KEYS = {
    "GME_GITHUB_TOKEN",
    "V2_AGENT_RUNTIME_DEFAULT",
}


def _clear_v2_config_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in V2_CONFIG_ENV_KEYS:
        monkeypatch.delenv(key, raising=False)


def test_v2_agent_runtime_default_is_llm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_v2_config_env(monkeypatch)
    monkeypatch.setenv("GME_GITHUB_TOKEN", "test-value")

    config = V2Config()

    assert config.V2_AGENT_RUNTIME_DEFAULT == AgentRuntime.LLM


# Only values other than the default (`llm`) can show the variable is read.
@pytest.mark.parametrize("runtime", [AgentRuntime.RULE_BASED, AgentRuntime.HYBRID])
def test_v2_agent_runtime_default_can_be_set_from_env(
    monkeypatch: pytest.MonkeyPatch,
    runtime: AgentRuntime,
) -> None:
    _clear_v2_config_env(monkeypatch)
    monkeypatch.setenv("V2_AGENT_RUNTIME_DEFAULT", runtime.value)

    config = V2Config()

    assert config.V2_AGENT_RUNTIME_DEFAULT is runtime


def test_v2_agent_runtime_default_rejects_invalid_values(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _clear_v2_config_env(monkeypatch)
    monkeypatch.setenv("GME_GITHUB_TOKEN", "test-value")
    monkeypatch.setenv("V2_AGENT_RUNTIME_DEFAULT", "not_a_runtime")

    with pytest.raises(ValueError, match="Invalid runtime value for V2_AGENT_RUNTIME_DEFAULT"):
        V2Config()
