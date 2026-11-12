"""A bounded repository retrieval tool reusable by any entity agent."""

from __future__ import annotations

import asyncio
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from pydantic_ai import Tool


class RepositoryToolOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")
    max_chars: int = Field(default=12000, ge=1000, le=50000)


def make_repository_tool(context, options):
    async def fetch_github_repository(
        full_name: str,
        section: Literal["metadata", "readme", "releases", "aux_files"] = "metadata",
    ) -> dict:
        """Fetch repository metadata, README, releases or citation/config files.

        Supply owner/repo. Content is bounded; check truncated before drawing
        conclusions about missing facts. This tool only reads public/provider data.
        """
        if not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", full_name) or any(
            p in {".", ".."} for p in full_name.split("/")
        ):
            msg = "Expected a GitHub owner/repository handle"
            raise ValueError(msg)
        methods = {
            "metadata": "get_repository",
            "readme": "get_repository_readme",
            "releases": "get_repository_releases",
            "aux_files": "get_repository_aux_files",
        }
        if section not in methods:
            msg = "Unknown repository section"
            raise ValueError(msg)
        data = await asyncio.to_thread(
            getattr(context.providers.github, methods[section]), full_name,
        )
        text = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
        return {
            "source_url": f"https://github.com/{full_name}",
            "section": section,
            "content": text[: options["max_chars"]],
            "truncated": len(text) > options["max_chars"],
        }

    return Tool(fetch_github_repository, takes_ctx=False)
