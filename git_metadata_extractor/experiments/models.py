"""Data-only configuration. YAML cannot import or execute Python components."""

from __future__ import annotations

from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, JsonValue, model_validator

from git_metadata_extractor.agents.tool_catalog import ToolSelection, reject_credentials
from git_metadata_extractor.pipeline.orchestrator import PLAN_BY_TYPE

ROLE_NAMES = {
    "repo_agent": "repository",
    "person_agent": "person",
    "org_agent": "organization",
    "article_agent": "article",
    "membership_agent": "membership",
    "contribution_agent": "contribution",
}


class ModelSelection(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model: str = Field(min_length=1)
    provider: Literal["openai", "openrouter", "openai-compatible"] | None = None
    base_url: str | None = None
    api_key_env: str | None = Field(default=None, pattern=r"^[A-Za-z_][A-Za-z0-9_]*$")

    @model_validator(mode="after")
    def credential_free_url(self):
        if self.base_url:
            parsed = urlsplit(self.base_url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.netloc
                or parsed.username
                or parsed.password
                or parsed.query
                or parsed.fragment
            ):
                msg = "base_url must be an HTTP(S) endpoint without credentials, query or fragment"
                raise ValueError(msg)
        return self


class ComponentRef(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    component: str = Field(min_length=1)
    options: dict[str, JsonValue] = Field(default_factory=dict)
    model: ModelSelection | None = None

    @model_validator(mode="after")
    def no_credentials(self):
        reject_credentials(self.options)
        return self


class PipelineProfile(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    schema_version: Literal[1] = 1
    name: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
    version: str = "1"
    runtime: Literal["rule_based", "llm", "hybrid"] = "rule_based"
    collectors: list[ComponentRef] = Field(
        default_factory=lambda: [ComponentRef(component="github.context")], min_length=1,
    )
    agents: dict[str, ComponentRef] = Field(default_factory=dict)
    toolsets: dict[str, ToolSelection] = Field(default_factory=dict)
    stages: dict[str, list[str]] = Field(default_factory=dict)
    max_concurrent_agents: int = Field(default=6, ge=1, le=128)
    retry_max_retries: int = Field(default=3, ge=0, le=10)
    model: ModelSelection | None = None

    @model_validator(mode="after")
    def valid_roles_and_plans(self):
        if set(self.agents) - ROLE_NAMES.keys():
            msg = "Unknown agent role"
            raise ValueError(msg)
        for kind, stages in self.stages.items():
            standard = PLAN_BY_TYPE.get(kind)
            if (
                standard is None
                or stages[:2] != standard[:2]
                or len(stages) != len(set(stages))
            ):
                msg = "Plans must start with context gathering and the input kind's root agent"
                raise ValueError(msg)
            if stages != [stage for stage in standard if stage in stages]:
                msg = "Stages must be a subsequence of the standard dependency order"
                raise ValueError(msg)
        return self


def load_profile(path: str | Path) -> PipelineProfile:
    return PipelineProfile.model_validate(
        yaml.safe_load(Path(path).read_text(encoding="utf-8")),
    )
