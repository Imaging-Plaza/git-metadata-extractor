"""Adapters for existing collectors and agents; custom catalogs can extend these."""

from __future__ import annotations

import asyncio

from pydantic import BaseModel, ConfigDict

from git_metadata_extractor import agents
from git_metadata_extractor.experiments.catalog import ComponentCatalog
from git_metadata_extractor.pipeline.stages.context_gather import gather_context
from git_metadata_extractor.pipeline.stages.models import ContextBundle


class NoOptions(BaseModel):
    model_config = ConfigDict(extra="forbid")


def _no_options(options):
    if options:
        msg = "This built-in component does not accept options"
        raise ValueError(msg)


async def _context(context, providers, options):
    _no_options(options)
    return await gather_context(
        context.detected_type, context.classification, providers,
    )


async def _metadata(context, providers, options):
    _no_options(options)
    if context.detected_type != "repository":
        msg = "github.repository_metadata supports repositories only"
        raise ValueError(msg)
    full_name = f"{context.classification.owner}/{context.classification.repo}"
    metadata = await asyncio.to_thread(providers.github.get_repository, full_name)
    return ContextBundle(
        detected_type="repository",
        context={
            "repository": {
                "full_name": full_name,
                "metadata": metadata,
                "contributors": [],
                "languages": {},
            },
        },
    )


async def _readme(context, providers, options):
    _no_options(options)
    if context.detected_type != "repository":
        msg = "github.readme supports repositories only"
        raise ValueError(msg)
    full_name = f"{context.classification.owner}/{context.classification.repo}"
    text = await asyncio.to_thread(providers.github.get_repository_readme, full_name)
    return ContextBundle(
        detected_type="repository", context={"repository": {"readme_content": text}},
    )


def _factory(agent_class, *, llm):
    def create(options, cache):
        _no_options(options)
        return agent_class(**({"cache": cache} if llm else {})).run

    return create


def default_catalog() -> ComponentCatalog:
    catalog = ComponentCatalog()
    for name, function in {
        "github.context": _context,
        "github.repository_metadata": _metadata,
        "github.readme": _readme,
    }.items():
        catalog.register_collector(name, function, version="1", options_model=NoOptions)
    for kind, rule, llm in [
        ("repository", agents.RepositoryAgentV2, agents.LLMRepositoryAgentV2),
        ("person", agents.PersonAgentV2, agents.LLMPersonAgentV2),
        ("organization", agents.OrganizationAgentV2, agents.LLMOrganizationAgentV2),
        ("article", agents.ArticleAgentV2, agents.LLMArticleAgentV2),
        ("membership", agents.MembershipAgentV2, agents.LLMMembershipAgentV2),
        ("contribution", agents.ContributionAgentV2, agents.LLMContributionAgentV2),
    ]:
        catalog.register_agent(
            f"rule_based.{kind}",
            _factory(rule, llm=False),
            version="1",
            runtime="rule_based",
            options_model=NoOptions,
        )
        catalog.register_agent(
            f"llm.{kind}",
            _factory(llm, llm=True),
            version="1",
            runtime="llm",
            options_model=NoOptions,
        )
    return catalog
