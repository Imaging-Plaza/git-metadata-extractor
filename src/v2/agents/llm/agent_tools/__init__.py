from __future__ import annotations

from src.v2.agents.llm.agent_tools.disciplines import list_disciplines_tool
from src.v2.agents.llm.agent_tools.duckduckgo_search import (
    make_duckduckgo_search_tool,
)
from src.v2.agents.llm.agent_tools.email_hash import (
    hash_user_email,
    hash_user_email_tool,
)
from src.v2.agents.llm.agent_tools.ethz_research_collection_rag import (
    make_ethz_research_collection_rag_fetch_chunks_tool,
    make_ethz_research_collection_rag_fetch_records_tool,
    make_ethz_research_collection_rag_search_tool,
)
from src.v2.agents.llm.agent_tools.federated_rag import (
    make_federated_rag_lookup_tool,
    make_federated_rag_search_tool,
)
from src.v2.agents.llm.agent_tools.github_organization import (
    make_github_organization_metadata_tool,
)
from src.v2.agents.llm.agent_tools.huggingface_lineage import (
    make_huggingface_lineage_tool,
)
from src.v2.agents.llm.agent_tools.huggingface_rag import (
    make_huggingface_rag_search_tool,
)
from src.v2.agents.llm.agent_tools.infoscience_orgunit import (
    make_infoscience_orgunit_tool,
)
from src.v2.agents.llm.agent_tools.infoscience_publications import (
    make_infoscience_publications_search_tool,
)
from src.v2.agents.llm.agent_tools.infoscience_rag import (
    make_infoscience_rag_fetch_chunks_tool,
    make_infoscience_rag_fetch_records_tool,
    make_infoscience_rag_search_tool,
)
from src.v2.agents.llm.agent_tools.infoscience_search import (
    make_infoscience_search_tool,
)
from src.v2.agents.llm.agent_tools.openalex_rag import (
    make_openalex_rag_search_tool,
)
from src.v2.agents.llm.agent_tools.orcid_person import make_orcid_person_tool
from src.v2.agents.llm.agent_tools.orcid_rag import (
    make_orcid_rag_search_tool,
)
from src.v2.agents.llm.agent_tools.renkulab_rag import (
    make_renkulab_rag_search_tool,
)
from src.v2.agents.llm.agent_tools.organization_identity import (
    make_organization_identity_search_tool,
)
from src.v2.agents.llm.agent_tools.query_dependencies import (
    make_query_dependencies_tool,
)
from src.v2.agents.llm.agent_tools.query_orcid import make_query_orcid_tool
from src.v2.agents.llm.agent_tools.repository_corpus_grep import (
    make_repository_corpus_grep_tool,
)
from src.v2.agents.llm.agent_tools.ror_organization import (
    make_ror_organization_search_tool,
)
from src.v2.agents.llm.agent_tools.ror_rag import (
    make_ror_rag_search_tool,
)
from src.v2.agents.llm.agent_tools.selenium_fetch import (
    fetch_link_content_via_selenium_tool,
)
from src.v2.agents.llm.agent_tools.snsf_rag import (
    make_snsf_rag_search_tool,
)
from src.v2.agents.llm.agent_tools.swissubase_rag import (
    make_swissubase_rag_search_tool,
)
from src.v2.agents.llm.agent_tools.uuid import (
    generate_uuid_v4_batch_tool,
    generate_uuid_v4_tool,
)
from src.v2.agents.llm.agent_tools.oamonitor_rag import (
    make_oamonitor_rag_fetch_records_tool,
    make_oamonitor_rag_search_tool,
)
from src.v2.agents.llm.agent_tools.zenodo_rag import (
    make_zenodo_rag_fetch_records_tool,
    make_zenodo_rag_search_tool,
)
from src.v2.agents.llm.agent_tools.github_rag import (
    make_github_rag_search_tool,
)

__all__ = [
    "fetch_link_content_via_selenium_tool",
    "generate_uuid_v4_batch_tool",
    "generate_uuid_v4_tool",
    "hash_user_email",
    "hash_user_email_tool",
    "list_disciplines_tool",
    "make_duckduckgo_search_tool",
    "make_ethz_research_collection_rag_fetch_chunks_tool",
    "make_ethz_research_collection_rag_fetch_records_tool",
    "make_ethz_research_collection_rag_search_tool",
    "make_federated_rag_lookup_tool",
    "make_federated_rag_search_tool",
    "make_github_organization_metadata_tool",
    "make_github_rag_search_tool",
    "make_huggingface_lineage_tool",
    "make_huggingface_rag_search_tool",
    "make_infoscience_orgunit_tool",
    "make_infoscience_publications_search_tool",
    "make_infoscience_rag_fetch_chunks_tool",
    "make_infoscience_rag_fetch_records_tool",
    "make_infoscience_rag_search_tool",
    "make_infoscience_search_tool",
    "make_oamonitor_rag_fetch_records_tool",
    "make_oamonitor_rag_search_tool",
    "make_openalex_rag_search_tool",
    "make_orcid_person_tool",
    "make_orcid_rag_search_tool",
    "make_organization_identity_search_tool",
    "make_query_dependencies_tool",
    "make_query_orcid_tool",
    "make_renkulab_rag_search_tool",
    "make_repository_corpus_grep_tool",
    "make_ror_organization_search_tool",
    "make_ror_rag_search_tool",
    "make_snsf_rag_search_tool",
    "make_swissubase_rag_search_tool",
    "make_zenodo_rag_fetch_records_tool",
    "make_zenodo_rag_search_tool",
]
