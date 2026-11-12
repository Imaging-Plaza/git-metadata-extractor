"""One registration point for production tools and reusable agent bundles."""

from __future__ import annotations

from git_metadata_extractor.agents.llm import agent_tools as existing
from git_metadata_extractor.agents.llm.agent_tools.epfl_graph_rag import (
    make_epfl_graph_rag_search_tool,
)
from git_metadata_extractor.agents.llm.agent_tools.github_repository import (
    RepositoryToolOptions,
    make_repository_tool,
)
from git_metadata_extractor.agents.llm.agent_tools.selenium_fetch import (
    make_fetch_link_content_tool,
)
from git_metadata_extractor.agents.llm.agent_tools.sources_service import (
    make_sources_manifest_tool,
    make_sources_search_tool,
)
from git_metadata_extractor.agents.tool_catalog import ToolCatalog


def _provider_factory(factory, provider):
    return lambda ctx, _options: factory(getattr(ctx.providers, provider))


def _identity(ctx, _options):
    if ctx.providers.ror is not None and ctx.providers.infoscience is not None:
        return existing.make_organization_identity_search_tool(
            ctx.providers.ror, ctx.providers.infoscience,
        )
    if ctx.providers.ror is not None:
        return existing.make_ror_organization_search_tool(ctx.providers.ror)
    if ctx.providers.infoscience is not None:
        return existing.make_infoscience_orgunit_tool(ctx.providers.infoscience)
    return []


def default_tool_catalog() -> ToolCatalog:
    catalog = ToolCatalog()
    catalog.register("sources.search", make_sources_search_tool, version="1", requires=("sources",))
    catalog.register("sources.manifest", make_sources_manifest_tool, version="1", requires=("sources",))
    catalog.register_bundle("sources.remote", ["sources.manifest", "sources.search"])
    catalog.register(
        "web.fetch",
        lambda ctx, _options: make_fetch_link_content_tool(ctx.cache),
        version="1",
    )
    catalog.register(
        "web.search",
        lambda ctx, _options: existing.make_duckduckgo_search_tool(cache=ctx.cache),
        version="1",
    )
    catalog.register(
        "corpus.grep",
        lambda ctx, _options: existing.make_repository_corpus_grep_tool(
            ctx.context.get("corpus_documents", []),
        ),
        version="1",
    )
    catalog.register(
        "discipline.list",
        lambda _ctx, _options: existing.list_disciplines_tool,
        version="1",
    )
    catalog.register(
        "email.hash", lambda _ctx, _options: existing.hash_user_email_tool, version="1",
    )
    catalog.register(
        "organization.identity",
        _identity,
        version="1",
        requires_any=("ror", "infoscience"),
    )
    catalog.register(
        "orcid.query",
        lambda ctx, _options: existing.make_query_orcid_tool(
            ctx.providers.orcid, orcid_rag_provider=ctx.providers.orcid_rag,
        ),
        version="1",
        requires=("orcid",),
    )
    catalog.register(
        "github.repository",
        make_repository_tool,
        version="1",
        requires=("github",),
        options_model=RepositoryToolOptions,
    )
    for name, provider, factory in [
        ("github.dependencies", "github", existing.make_query_dependencies_tool),
        (
            "github.organization",
            "github",
            existing.make_github_organization_metadata_tool,
        ),
        ("orcid.person", "orcid", existing.make_orcid_person_tool),
        ("ror.organization", "ror", existing.make_ror_organization_search_tool),
        ("infoscience.person", "infoscience", existing.make_infoscience_search_tool),
        (
            "infoscience.organization",
            "infoscience",
            existing.make_infoscience_orgunit_tool,
        ),
        (
            "infoscience.publications",
            "infoscience",
            existing.make_infoscience_publications_search_tool,
        ),
        (
            "infoscience.search",
            "infoscience_rag",
            existing.make_infoscience_rag_search_tool,
        ),
        (
            "infoscience.chunks",
            "infoscience_rag",
            existing.make_infoscience_rag_fetch_chunks_tool,
        ),
        (
            "infoscience.records",
            "infoscience_rag",
            existing.make_infoscience_rag_fetch_records_tool,
        ),
        (
            "ethz.search",
            "ethz_research_collection_rag",
            existing.make_ethz_research_collection_rag_search_tool,
        ),
        (
            "ethz.chunks",
            "ethz_research_collection_rag",
            existing.make_ethz_research_collection_rag_fetch_chunks_tool,
        ),
        (
            "ethz.records",
            "ethz_research_collection_rag",
            existing.make_ethz_research_collection_rag_fetch_records_tool,
        ),
        (
            "huggingface.search",
            "huggingface_rag",
            existing.make_huggingface_rag_search_tool,
        ),
        (
            "huggingface.lineage",
            "huggingface_rag",
            existing.make_huggingface_lineage_tool,
        ),
        ("openalex.search", "openalex_rag", existing.make_openalex_rag_search_tool),
        ("zenodo.search", "zenodo_rag", existing.make_zenodo_rag_search_tool),
        ("zenodo.records", "zenodo_rag", existing.make_zenodo_rag_fetch_records_tool),
        ("oamonitor.search", "oamonitor_rag", existing.make_oamonitor_rag_search_tool),
        (
            "oamonitor.records",
            "oamonitor_rag",
            existing.make_oamonitor_rag_fetch_records_tool,
        ),
        ("orcid.search", "orcid_rag", existing.make_orcid_rag_search_tool),
        ("ror.search", "ror_rag", existing.make_ror_rag_search_tool),
        ("snsf.search", "snsf_rag", existing.make_snsf_rag_search_tool),
        (
            "swissubase.search",
            "swissubase_rag",
            existing.make_swissubase_rag_search_tool,
        ),
        ("renkulab.search", "renkulab_rag", existing.make_renkulab_rag_search_tool),
        ("github.search", "github_rag", existing.make_github_rag_search_tool),
        ("disciplines.search", "epfl_graph_rag", make_epfl_graph_rag_search_tool),
        ("federated.search", "federated_rag", existing.make_federated_rag_search_tool),
        ("federated.lookup", "federated_rag", existing.make_federated_rag_lookup_tool),
    ]:
        catalog.register(
            name,
            _provider_factory(factory, provider),
            version="1",
            requires=(provider,),
        )
    catalog.register_bundle(
        "infoscience.rag",
        ["infoscience.search", "infoscience.chunks", "infoscience.records"],
    )
    catalog.register_bundle("ethz.rag", ["ethz.search", "ethz.chunks", "ethz.records"])
    catalog.register_bundle("federated.rag", ["federated.search", "federated.lookup"])
    catalog.register_bundle(
        "publications.search",
        [
            "infoscience.search",
            "ethz.search",
            "openalex.search",
            "zenodo.search",
            "oamonitor.search",
        ],
    )
    catalog.set_defaults(
        "repo_agent",
        [
            "discipline.list",
            "web.fetch",
            "github.dependencies",
            "huggingface.search",
            "huggingface.lineage",
            "renkulab.search",
            "github.search",
            "disciplines.search",
            "federated.rag",
        ],
    )
    catalog.set_defaults(
        "person_agent",
        [
            "web.fetch",
            "email.hash",
            "infoscience.person",
            "orcid.person",
            "orcid.query",
            "infoscience.rag",
            "ethz.rag",
            "orcid.search",
            "renkulab.search",
            "snsf.search",
            "disciplines.search",
        ],
    )
    catalog.set_defaults(
        "org_agent",
        [
            "web.fetch",
            "github.organization",
            "organization.identity",
            "infoscience.rag",
            "ethz.rag",
            "ror.search",
            "snsf.search",
            "huggingface.search",
            "openalex.search",
            "renkulab.search",
            "federated.rag",
            "disciplines.search",
        ],
    )
    catalog.set_defaults(
        "article_agent",
        [
            "web.fetch",
            "infoscience.publications",
            "infoscience.rag",
            "ethz.rag",
            "openalex.search",
            "zenodo.search",
            "zenodo.records",
            "oamonitor.search",
            "oamonitor.records",
            "renkulab.search",
            "swissubase.search",
            "disciplines.search",
        ],
    )
    catalog.set_defaults(
        "membership_agent", ["web.fetch", "orcid.person", "orcid.query"],
    )
    catalog.set_defaults("contribution_agent", ["web.fetch"])
    catalog.set_defaults("context_summary_agent", ["corpus.grep", "web.search"])
    catalog.set_defaults(
        "context_scout",
        [
            "corpus.grep",
            "web.search",
            "web.fetch",
            "orcid.search",
            "ror.search",
            "infoscience.search",
            "openalex.search",
            "zenodo.search",
            "oamonitor.search",
            "ethz.search",
            "huggingface.search",
            "renkulab.search",
            "snsf.search",
            "disciplines.search",
        ],
    )
    for role, defaults in catalog.defaults.items():
        catalog.set_defaults(role, [*defaults, "sources.remote"])
    return catalog
