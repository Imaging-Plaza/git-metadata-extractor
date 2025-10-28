"""
API
"""

import logging
import os
from datetime import datetime
from typing import Optional

from fastapi import FastAPI, Path, Query, Request
from fastapi.responses import JSONResponse

from .analysis import Repository
from .cache import get_cache_manager
from .data_models import (
    APIOutput,
    ResourceType,
)
from .utils.enhanced_logging import AsyncRequestContext, setup_logging

# Setup enhanced logging with colors
# Allow LOG_LEVEL environment variable to override (DEBUG, INFO, WARNING, ERROR)
log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, log_level_str, logging.INFO)
setup_logging(level=log_level, use_colors=True)


logger = logging.getLogger(__name__)


app = FastAPI(
    title="Git Metadata Extractor API",
    description="""
This API has been developed by the **Swiss Data Science Center (SDSC)** in collaboration with the **EPFL Center for Imaging** for use on [imaging-plaza.epfl.ch](https://imaging-plaza.epfl.ch) and in collaboration with the **EPFL Open Science Office** for the **Open Pulse** project.

Extract and enrich repository metadata from Git platforms using GIMIE and AI models.

## Features

- **Repository Analysis**: Extract comprehensive metadata from Git repositories
- **User & Organization Data**: Retrieve and enrich GitHub user and organization profiles
- **AI-Powered Enrichment**: Enhance metadata using LLM models (GPT, Gemini)
- **ORCID Affiliations**: Automatically enrich author metadata with affiliations from ORCID profiles
- **Intelligent Caching**: SQLite-based caching with configurable TTL to reduce API calls
- **JSON-LD Support**: Output aligned with Imaging Plaza softwareSourceCode schema
- **Force Refresh**: Bypass cache when fresh data is needed

## Caching

All endpoints support intelligent caching to reduce external API calls and improve performance.
Use the `force_refresh=true` query parameter to bypass cache and fetch fresh data.

Cache management endpoints are available under the `/v1/cache/` prefix.

## API Types

- **Repository Endpoints**: Extract and analyze repository metadata
- **User Endpoints**: Process GitHub user profiles
- **Organization Endpoints**: Process GitHub organization data
- **Cache Management**: Monitor and control the caching system
    """,
    version="2.0.0",
    contact={
        "name": "EPFL Center for Imaging / SDSC",
        "url": "https://imaging-plaza.epfl.ch",
    },
    license_info={
        "name": "MIT License",
        "url": "https://github.com/Imaging-Plaza/git-metadata-extractor/blob/main/LICENSE",
    },
    openapi_tags=[
        {
            "name": "Repository",
            "description": "Extract and analyze repository metadata from Git platforms",
        },
        {
            "name": "User",
            "description": "Retrieve and enrich GitHub user profile information",
        },
        {
            "name": "Organization",
            "description": "Retrieve and enrich GitHub organization data",
        },
        {
            "name": "Cache Management",
            "description": "Monitor, control, and manage the API caching system",
        },
        {"name": "System", "description": "System information and health checks"},
    ],
)


# Startup and shutdown events for resource management
@app.on_event("startup")
async def startup_event():
    """Initialize resources on application startup"""
    logger.info("🚀 Application startup - initializing resources")


@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup resources on application shutdown"""
    logger.info("🛑 Application shutdown - cleaning up resources")

    # Cleanup PydanticAI agents
    try:
        from .agents.agents_management import cleanup_agents

        await cleanup_agents()
        logger.info("✅ Cleaned up PydanticAI agents")
    except Exception as e:
        logger.warning(f"Error cleaning up PydanticAI agents: {e}")

    # Cleanup user enrichment agents
    try:
        from .agents.user_enrichment import cleanup_user_agents

        await cleanup_user_agents()
        logger.info("✅ Cleaned up user enrichment agents")
    except Exception as e:
        logger.warning(f"Error cleaning up user enrichment agents: {e}")

    # Cleanup organization enrichment agents
    try:
        from .agents.organization_enrichment import cleanup_org_agents

        await cleanup_org_agents()
        logger.info("✅ Cleaned up organization enrichment agents")
    except Exception as e:
        logger.warning(f"Error cleaning up organization enrichment agents: {e}")

    # Run garbage collection
    import gc

    gc.collect()
    logger.info("✅ Garbage collection completed")


# Add middleware to automatically set request context for all endpoints
@app.middleware("http")
async def add_request_context(request: Request, call_next):
    """Middleware to add request ID to all endpoint logs"""
    # Determine prefix based on endpoint path
    path = request.url.path
    if "/org/" in path:
        prefix = "org"
    elif "/user/" in path:
        prefix = "user"
    elif "/repository/" in path or "/extract/" in path:
        prefix = "repo"
    elif "/cache/" in path:
        prefix = "cache"
    else:
        prefix = "api"

    async with AsyncRequestContext(prefix=prefix):
        # Log the incoming request with method, path, and query params
        query_string = f"?{request.url.query}" if request.url.query else ""
        logger.info(f"📥 {request.method} {path}{query_string}")

        response = await call_next(request)

        # Log the response status
        logger.info(f"📤 Response: {response.status_code}")
        return response


@app.get("/", tags=["System"])
def index():
    """
    Get API welcome message and system information.

    Returns basic information about the API version, GIMIE version, and configured LLM model.
    """
    return {
        "title": f"Hello, welcome to the Git Metadata Extractor v2.0.0. Gimie Version 0.7.2. LLM Model {os.environ['MODEL']}",
    }


# @app.get("/v1/extract/json/{full_path:path}", tags=["Repository"])
# async def extract(
#     full_path: str = Path(
#         ...,
#         description="Full repository URL",
#         openapi_examples={
#             "gimie": {
#                 "summary": "GIMIE Repository",
#                 "value": "https://github.com/sdsc-ordes/gimie",
#             },
#         },
#     ),
#     force_refresh: bool = Query(
#         False,
#         description="Force refresh from external APIs, bypassing cache",
#     ),
#     auto_enrich_orcid: bool = Query(
#         True,
#         description="Automatically enrich authors with ORCID affiliations if they have ORCID IDs but no affiliations",
#     ),
#     enrich_orgs: bool = Query(
#         False,
#         description="Enable organization enrichment using PydanticAI agent to analyze and standardize organization information from git author emails, ORCID affiliations, and ROR API",
#     ),
#     enrich_users: bool = Query(
#         False,
#         description="Enable user/author enrichment using PydanticAI agent to analyze affiliations, ORCID data, and provide detailed author information",
#     ),
# ):
#     """
#     Extract and enrich repository metadata in JSON format.

#     Combines GIMIE repository analysis with LLM-based enrichment to provide
#     comprehensive metadata about a Git repository. The output is converted to
#     a Zod-compatible format for easy frontend integration.

#     **Organization Enrichment** (optional):
#     When `enrich_orgs=true`, performs a second-pass agentic analysis using PydanticAI to:
#     - Query ROR (Research Organization Registry) for standardized organization names and IDs
#     - Identify hierarchical relationships (departments, labs within universities)
#     - Provide detailed EPFL relationship analysis with evidence
#     - Enrich organization metadata with type, country, website, etc.

#     **User Enrichment** (optional):
#     When `enrich_users=true`, performs author/contributor enrichment to:
#     - Analyze git author information and affiliations
#     - Cross-reference with ORCID data
#     - Provide comprehensive author profiles

#     **Caching**: Results are cached with default TTL of 30 days (LLM) and 1 day (GIMIE).

#     **Parameters**:
#     - **full_path**: Full repository URL (e.g., `https://github.com/user/repo`)
#     - **force_refresh**: Set to `true` to bypass cache and fetch fresh data
#     - **auto_enrich_orcid**: Set to `true` to automatically enrich authors with ORCID data if they have ORCID IDs but no affiliations (default: `true`)
#     - **enrich_orgs**: Set to `true` to enable organization enrichment with PydanticAI agent
#     - **enrich_users**: Set to `true` to enable user/author enrichment with PydanticAI agent

#     **Returns**:
#     - Repository link
#     - Enriched metadata in Zod-compatible format
#     - Cache status indicator
#     """

#     cache_manager = get_cache_manager()

#     # Create cache parameters
#     cache_params = {"full_path": full_path, "format": "json-ld", "max_tokens": 30000}

#     def fetch_gimie_data():
#         return extract_gimie(full_path, format="json-ld")

#     async def fetch_llm_data():
#         return await llm_request_repo_infos(
#             str(full_path),
#             output_format="json-ld",
#             max_tokens=30000,
#         )

#     # Get GIMIE data (cached)
#     jsonld_gimie_data = cache_manager.get_cached_or_fetch(
#         api_type="gimie",
#         params={"full_path": full_path, "format": "json-ld"},
#         fetch_func=fetch_gimie_data,
#         force_refresh=force_refresh,
#     )

#     try:
#         # Get LLM data (cached or fetched) - automatically handles coroutines
#         llm_result = await cache_manager.get_cached_or_fetch_async(
#             api_type="llm",
#             params=cache_params,
#             fetch_func=fetch_llm_data,
#             force_refresh=force_refresh,
#         )

#         merged_results = merge_jsonld(jsonld_gimie_data, llm_result)
#         pydantic_data = convert_jsonld_to_pydantic(merged_results["@graph"])

#     except Exception as e:
#         pydantic_data = convert_jsonld_to_pydantic(jsonld_gimie_data["@graph"])
#         logger.warning(f"{full_path} :: LLM service failed, using fallback data: {e}")

#     zod_data = convert_pydantic_to_zod_form_dict(pydantic_data)

#     # Enrich authors with ORCID affiliations
#     logger.info(
#         f"Starting ORCID enrichment for {full_path} (force_refresh={force_refresh}, auto_enrich={auto_enrich_orcid})",
#     )
#     authors_before = len(zod_data.get("schema:author", []))
#     logger.info(f"Found {authors_before} authors before enrichment")

#     zod_data = enrich_authors_with_orcid(
#         zod_data,
#         force_refresh=force_refresh,
#         auto_enrich=auto_enrich_orcid,
#     )

#     authors_after = len(zod_data.get("schema:author", []))
#     logger.info(f"ORCID enrichment completed. Authors after: {authors_after}")

#     # Perform organization enrichment if requested
#     if enrich_orgs:
#         logger.info(f"Starting organization enrichment for {full_path}")
#         try:
#             organization_enrichment = await enrich_organizations_from_dict(
#                 zod_data,
#                 full_path,
#             )
#             logger.info(
#                 f"Organization enrichment completed. Found {len(organization_enrichment.get('organizations', []))} organizations",
#             )

#             # Update the main output with enriched organization data
#             enriched_orgs = organization_enrichment.get("organizations", [])
#             if enriched_orgs:
#                 zod_data["relatedToOrganizations"] = [
#                     org.get("legalName")
#                     for org in enriched_orgs
#                     if org.get("legalName")
#                 ]
#                 zod_data["relatedToOrganizationsROR"] = enriched_orgs

#             # Update EPFL relationship with enriched analysis
#             zod_data["relatedToEPFL"] = organization_enrichment.get(
#                 "relatedToEPFL",
#                 zod_data.get("relatedToEPFL"),
#             )
#             zod_data["relatedToEPFLJustification"] = organization_enrichment.get(
#                 "relatedToEPFLJustification",
#                 zod_data.get("relatedToEPFLJustification"),
#             )

#         except Exception as e:
#             logger.error(f"Error during organization enrichment: {e}", exc_info=True)

#     # Perform user enrichment if requested
#     if enrich_users:
#         logger.info(f"Starting user enrichment for {full_path}")
#         try:
#             # Extract git authors and existing authors from metadata
#             git_authors_data = zod_data.get("gitAuthors", [])
#             existing_authors_data = zod_data.get("schema:author", [])

#             user_enrichment = await enrich_users_from_dict(
#                 git_authors_data=git_authors_data,
#                 existing_authors_data=existing_authors_data,
#                 repository_url=full_path,
#             )
#             logger.info(
#                 f"User enrichment completed. Enriched {len(user_enrichment.get('enrichedAuthors', []))} authors",
#             )

#             # Add enriched user data to output
#             zod_data["enrichedAuthors"] = user_enrichment.get("enrichedAuthors", [])
#             zod_data["authorEnrichmentSummary"] = user_enrichment.get("summary", "")

#         except Exception as e:
#             logger.error(f"Error during user enrichment: {e}", exc_info=True)

#     return {"link": full_path, "output": zod_data, "cached": not force_refresh}


# @app.get("/v1/extract/json-ld/{full_path:path}", tags=["Repository"])
# async def extract_jsonld(
#     full_path: str = Path(
#         ...,
#         description="Full repository URL",
#         openapi_examples={
#             "gimie": {
#                 "summary": "GIMIE Repository",
#                 "value": "https://github.com/sdsc-ordes/gimie",
#             },
#         },
#     ),
#     force_refresh: bool = Query(
#         False,
#         description="Force refresh from external APIs, bypassing cache",
#     ),
#     auto_enrich_orcid: bool = Query(
#         True,
#         description="Automatically enrich authors with ORCID affiliations if they have ORCID IDs but no affiliations",
#     ),
#     enrich_orgs: bool = Query(
#         False,
#         description="Enable organization enrichment using PydanticAI agent to analyze and standardize organization information from git author emails, ORCID affiliations, and ROR API",
#     ),
#     enrich_users: bool = Query(
#         False,
#         description="Enable user/author enrichment using PydanticAI agent to analyze affiliations, ORCID data, and provide detailed author information",
#     ),
# ):
#     """
#     Extract and enrich repository metadata in JSON-LD format.

#     Combines GIMIE repository analysis with LLM-based enrichment to provide
#     comprehensive metadata in JSON-LD format, aligned with the Imaging Plaza
#     softwareSourceCode schema.

#     **Organization Enrichment** (optional):
#     When `enrich_orgs=true`, performs a second-pass agentic analysis using PydanticAI to:
#     - Query ROR (Research Organization Registry) for standardized organization names and IDs
#     - Identify hierarchical relationships (departments, labs within universities)
#     - Provide detailed EPFL relationship analysis with evidence
#     - Enrich organization metadata with type, country, website, etc.

#     **User Enrichment** (optional):
#     When `enrich_users=true`, performs author/contributor enrichment to:
#     - Analyze git author information and affiliations
#     - Cross-reference with ORCID data
#     - Provide comprehensive author profiles

#     **Caching**: Results are cached with default TTL of 30 days (LLM) and 1 day (GIMIE).

#     **Parameters**:
#     - **full_path**: Full repository URL (e.g., `https://github.com/user/repo`)
#     - **force_refresh**: Set to `true` to bypass cache and fetch fresh data
#     - **auto_enrich_orcid**: Set to `true` to automatically enrich authors with ORCID data if they have ORCID IDs but no affiliations (default: `true`)
#     - **enrich_orgs**: Set to `true` to enable organization enrichment with PydanticAI agent
#     - **enrich_users**: Set to `true` to enable user/author enrichment with PydanticAI agent

#     **Returns**:
#     - Repository link
#     - Merged metadata in JSON-LD format
#     - Cache status indicator
#     """

#     cache_manager = get_cache_manager()

#     def fetch_gimie_data():
#         return extract_gimie(full_path, format="json-ld")

#     async def fetch_llm_data():
#         return await llm_request_repo_infos(str(full_path), max_tokens=20000)

#     # Get GIMIE data (cached)
#     jsonld_gimie_data = cache_manager.get_cached_or_fetch(
#         api_type="gimie",
#         params={"full_path": full_path, "format": "json-ld"},
#         fetch_func=fetch_gimie_data,
#         force_refresh=force_refresh,
#     )

#     try:
#         # Get LLM data (cached or fetched) - automatically handles coroutines
#         cache_params = {"full_path": full_path, "max_tokens": 20000}
#         llm_result = await cache_manager.get_cached_or_fetch_async(
#             api_type="llm",
#             params=cache_params,
#             fetch_func=fetch_llm_data,
#             force_refresh=force_refresh,
#         )
#     except Exception as e:
#         raise HTTPException(status_code=424, detail=f"Error from LLM service: {e}")

#     merged_results = merge_jsonld(jsonld_gimie_data, llm_result)

#     # Enrich authors with ORCID affiliations
#     logger.info(
#         f"Starting ORCID enrichment for {full_path} (force_refresh={force_refresh}, auto_enrich={auto_enrich_orcid})",
#     )
#     merged_results = enrich_authors_with_orcid(
#         merged_results,
#         force_refresh=force_refresh,
#         auto_enrich=auto_enrich_orcid,
#     )
#     logger.info("ORCID enrichment completed for JSON-LD endpoint")

#     # Perform organization enrichment if requested
#     if enrich_orgs:
#         logger.info(f"Starting organization enrichment for {full_path}")
#         try:
#             organization_enrichment = await enrich_organizations_from_dict(
#                 merged_results,
#                 full_path,
#             )
#             logger.info(
#                 f"Organization enrichment completed. Found {len(organization_enrichment.get('organizations', []))} organizations",
#             )

#             # Update the main output with enriched organization data
#             enriched_orgs = organization_enrichment.get("organizations", [])
#             if enriched_orgs:
#                 merged_results["relatedToOrganizations"] = [
#                     org.get("legalName")
#                     for org in enriched_orgs
#                     if org.get("legalName")
#                 ]
#                 merged_results["relatedToOrganizationsROR"] = enriched_orgs

#             # Update EPFL relationship with enriched analysis
#             merged_results["relatedToEPFL"] = organization_enrichment.get(
#                 "relatedToEPFL",
#                 merged_results.get("relatedToEPFL"),
#             )
#             merged_results["relatedToEPFLJustification"] = organization_enrichment.get(
#                 "relatedToEPFLJustification",
#                 merged_results.get("relatedToEPFLJustification"),
#             )

#         except Exception as e:
#             logger.error(f"Error during organization enrichment: {e}", exc_info=True)

#     # Perform user enrichment if requested
#     if enrich_users:
#         logger.info(f"Starting user enrichment for {full_path}")
#         try:
#             # Extract git authors and existing authors from metadata
#             git_authors_data = merged_results.get("gitAuthors", [])
#             existing_authors_data = merged_results.get("author", [])

#             user_enrichment = await enrich_users_from_dict(
#                 git_authors_data=git_authors_data,
#                 existing_authors_data=existing_authors_data,
#                 repository_url=full_path,
#             )
#             logger.info(
#                 f"User enrichment completed. Enriched {len(user_enrichment.get('enrichedAuthors', []))} authors",
#             )

#             # Add enriched user data to output
#             merged_results["enrichedAuthors"] = user_enrichment.get(
#                 "enrichedAuthors",
#                 [],
#             )
#             merged_results["authorEnrichmentSummary"] = user_enrichment.get(
#                 "summary",
#                 "",
#             )

#         except Exception as e:
#             logger.error(f"Error during user enrichment: {e}", exc_info=True)

#     return {"link": full_path, "output": merged_results, "cached": not force_refresh}


# @app.get("/v1/org/llm/json/{full_path:path}", tags=["Organization"])
# async def get_org_json(
#     full_path: str = Path(
#         ...,
#         description="GitHub organization URL or path",
#         openapi_examples={
#             "sdsc": {"summary": "SDSC Organization", "value": "github.com/sdsc-ordes"},
#         },
#     ),
#     force_refresh: bool = Query(
#         False,
#         description="Force refresh from external APIs, bypassing cache",
#     ),
#     enrich_orgs: bool = Query(
#         False,
#         description="Enable organization enrichment using PydanticAI agent to analyze and standardize organization information using ROR API",
#     ),
# ):
#     """
#     Retrieve and enrich GitHub organization metadata.

#     Fetches organization profile from GitHub API and enriches it using LLM
#     to extract additional insights and structured information.

#     **Organization Enrichment** (optional):
#     When `enrich_orgs=true`, performs a second-pass agentic analysis using PydanticAI to:
#     - Query ROR (Research Organization Registry) for standardized organization names and IDs
#     - Identify hierarchical relationships (departments, labs within universities)
#     - Provide detailed EPFL relationship analysis with evidence
#     - Enrich organization metadata with type, country, website, etc.

#     **Caching**: Results are cached with TTL of 7 days.

#     **Parameters**:
#     - **full_path**: GitHub organization URL or path (e.g., `https://github.com/organization`)
#     - **force_refresh**: Set to `true` to bypass cache and fetch fresh data
#     - **enrich_orgs**: Set to `true` to enable organization enrichment with PydanticAI agent

#     **Returns**:
#     - Organization link
#     - Enriched organization metadata
#     - Organization enrichment results (if `enrich_orgs=true`)
#     """
#     cache_manager = get_cache_manager()
#     org_name = full_path.split("/")[-1]

#     def fetch_org_metadata():
#         return parse_github_organization(org_name)

#     def fetch_llm_metadata():
#         # This endpoint is deprecated - use the new agent-based enrichment
#         # For now, return the basic organization metadata
#         return parse_github_organization(org_name)

#     try:
#         # Get GitHub organization metadata (cached)
#         org_metadata = cache_manager.get_cached_or_fetch(
#             api_type="github_org",
#             params={"org_name": org_name},
#             fetch_func=fetch_org_metadata,
#             force_refresh=force_refresh,
#         )

#         # Get LLM processed metadata (cached) - automatically handles coroutines
#         parsed_org_metadata = await cache_manager.get_cached_or_fetch_async(
#             api_type="llm_org",
#             params={"org_name": org_name, "item_type": "org"},
#             fetch_func=fetch_llm_metadata,
#             force_refresh=force_refresh,
#         )

#         org_metadata_dict = org_metadata.model_dump()
#         org_metadata_dict["parseTimestamp"] = datetime.now().strftime(
#             "%Y-%m-%dT%H:%M",
#         )
#         org_metadata_dict.update(parsed_org_metadata)

#     except Exception as e:
#         raise HTTPException(
#             status_code=424,
#             detail=f"Error from Organization JSON service: {e}",
#         )

#     # Perform organization enrichment if requested
#     response = {"link": full_path, "output": org_metadata_dict}

#     if enrich_orgs:
#         logger.info(f"Starting organization enrichment for org {org_name}")
#         try:
#             organization_enrichment = await enrich_organizations_from_dict(
#                 org_metadata_dict,
#                 full_path,
#             )
#             logger.info(
#                 f"Organization enrichment completed for org. Found {len(organization_enrichment.get('organizations', []))} organizations",
#             )

#             # Update the main output with enriched organization data
#             # Keep relatedToOrganizations as list of strings (backwards compatible)
#             # Add relatedToOrganizationsROR as list of Organization objects (new field)
#             enriched_orgs = organization_enrichment.get("organizations", [])
#             if enriched_orgs:
#                 org_metadata_dict["relatedToOrganizations"] = [
#                     org.get("legalName")
#                     for org in enriched_orgs
#                     if org.get("legalName")
#                 ]
#                 org_metadata_dict["relatedToOrganizationsROR"] = enriched_orgs

#             # Update EPFL relationship with enriched analysis
#             org_metadata_dict["relatedToEPFL"] = organization_enrichment.get(
#                 "relatedToEPFL",
#                 org_metadata_dict.get("relatedToEPFL"),
#             )
#             org_metadata_dict[
#                 "relatedToEPFLJustification"
#             ] = organization_enrichment.get(
#                 "relatedToEPFLJustification",
#                 org_metadata_dict.get("relatedToEPFLJustification"),
#             )

#         except Exception as e:
#             logger.error(
#                 f"Error during organization enrichment for org: {e}",
#                 exc_info=True,
#             )
#             # Don't fail the entire request or expose error in response, just log it

#     return response


# @app.get("/v1/user/llm/json/{full_path:path}", tags=["User"])
# async def get_user_json(
#     full_path: str = Path(
#         ...,
#         description="GitHub user URL or path",
#         openapi_examples={
#             "caviri": {"summary": "User Example", "value": "github.com/caviri"},
#         },
#     ),
#     force_refresh: bool = Query(
#         False,
#         description="Force refresh from external APIs, bypassing cache",
#     ),
#     enrich_orgs: bool = Query(
#         False,
#         description="Enable organization enrichment using PydanticAI agent to analyze and standardize organization information from ORCID affiliations and ROR API",
#     ),
#     enrich_users: bool = Query(
#         False,
#         description="Enable user/author enrichment using PydanticAI agent to analyze affiliations, ORCID data, and provide detailed author information",
#     ),
# ) -> APIOutput:
#     """
#     Retrieve and enrich GitHub user profile metadata.

#     Fetches user profile from GitHub API and enriches it using LLM
#     to extract additional insights, research interests, and structured information.

#     **Organization Enrichment** (optional):
#     When `enrich_orgs=true`, performs a second-pass agentic analysis using PydanticAI to:
#     - Query ORCID for user affiliations
#     - Query ROR (Research Organization Registry) for standardized organization names and IDs
#     - Identify hierarchical relationships (departments, labs within universities)
#     - Provide detailed EPFL relationship analysis with evidence
#     - Enrich organization metadata with type, country, website, etc.

#     **User Enrichment** (optional):
#     When `enrich_users=true`, performs author/contributor enrichment to:
#     - Analyze git author information and affiliations
#     - Cross-reference with ORCID data
#     - Provide comprehensive author profiles

#     **Caching**: Results are cached with TTL of 30 days.

#     **Parameters**:
#     - **full_path**: GitHub user URL or path (e.g., `https://github.com/username`)
#     - **force_refresh**: Set to `true` to bypass cache and fetch fresh data
#     - **enrich_orgs**: Set to `true` to enable organization enrichment with PydanticAI agent
#     - **enrich_users**: Set to `true` to enable user/author enrichment with PydanticAI agent

#     **Returns**:
#     - User profile link
#     - User type
#     - Parsing timestamp
#     - User Object with enriched metadata
#     """
#     username = full_path.split("/")[-1]

#     # Ensure full_path is a valid URL
#     if not full_path.startswith(("http://", "https://")):
#         full_path = f"https://{full_path}"

#     user = User(username, force_refresh=force_refresh)

#     await user.run_analysis(
#         run_organization_enrichment=enrich_orgs,
#         run_user_enrichment=enrich_users,
#     )

#     output = user.dump_results(output_type="pydantic")

#     response = APIOutput(
#         link=full_path,
#         type=ResourceType.USER,
#         parsedTimestamp=datetime.now(),
#         output=output,
#     )

#     return response


# @app.get("/v1/repository/gimie/json-ld/{full_path:path}", tags=["Repository"])
# async def gimie(
#     full_path: str = Path(
#         ...,
#         description="Full repository URL",
#         openapi_examples={
#             "gimie": {
#                 "summary": "GIMIE Repository",
#                 "value": "https://github.com/sdsc-ordes/gimie",
#             },
#         },
#     ),
#     force_refresh: bool = Query(
#         False,
#         description="Force refresh from external APIs, bypassing cache",
#     ),
# ):
#     """
#     Extract repository metadata using GIMIE only.

#     Returns raw GIMIE analysis without LLM enrichment. GIMIE provides
#     basic repository metadata extracted from Git platforms.

#     **Caching**: Results are cached with TTL of 1 day.

#     **Parameters**:
#     - **full_path**: Full repository URL (e.g., `https://github.com/user/repo`)
#     - **force_refresh**: Set to `true` to bypass cache and fetch fresh data

#     **Returns**:
#     - Repository link
#     - GIMIE metadata in JSON-LD format
#     - Cache status indicator
#     """

#     cache_manager = get_cache_manager()

#     def fetch_gimie_data():
#         return extract_gimie(full_path, format="json-ld")

#     try:
#         gimie_output = cache_manager.get_cached_or_fetch(
#             api_type="gimie",
#             params={"full_path": full_path, "format": "json-ld"},
#             fetch_func=fetch_gimie_data,
#             force_refresh=force_refresh,
#         )
#     except Exception as e:
#         raise HTTPException(status_code=424, detail=f"Error from Gimie service: {e}")

#     return {"link": full_path, "output": gimie_output, "cached": not force_refresh}


# @app.get("/v1/repository/llm/json-ld/{full_path:path}", tags=["Repository"])
# async def llm_jsonld(
#     full_path: str = Path(
#         ...,
#         description="Full repository URL",
#         openapi_examples={
#             "gimie": {
#                 "summary": "GIMIE Repository",
#                 "value": "https://github.com/sdsc-ordes/gimie",
#             },
#         },
#     ),
#     force_refresh: bool = Query(
#         False,
#         description="Force refresh from external APIs, bypassing cache",
#     ),
# ):
#     """
#     Extract repository metadata using LLM only.

#     Returns LLM-based analysis without GIMIE data. This provides AI-generated
#     insights and structured metadata about the repository.

#     **Caching**: Results are cached with default TTL of 30 days.

#     **Parameters**:
#     - **full_path**: Full repository URL (e.g., `https://github.com/user/repo`)
#     - **force_refresh**: Set to `true` to bypass cache and fetch fresh data

#     **Returns**:
#     - Repository link
#     - LLM-generated metadata in JSON-LD format
#     - Cache status indicator
#     """

#     cache_manager = get_cache_manager()

#     async def fetch_llm_data():
#         return await llm_request_repo_infos(str(full_path), max_tokens=20000)

#     try:
#         # Get LLM data (cached or fetched) - automatically handles coroutines
#         cache_params = {"full_path": full_path, "max_tokens": 20000}
#         llm_result = await cache_manager.get_cached_or_fetch_async(
#             api_type="llm",
#             params=cache_params,
#             fetch_func=fetch_llm_data,
#             force_refresh=force_refresh,
#         )
#     except Exception as e:
#         raise HTTPException(status_code=424, detail=f"Error from LLM service: {e}")

#     return {"link": full_path, "output": llm_result, "cached": not force_refresh}


@app.get("/v1/repository/llm/json/{full_path:path}", tags=["Repository"])
async def llm_json(
    full_path: str = Path(
        ...,
        description="Full repository URL",
        openapi_examples={
            "gimie": {
                "summary": "GIMIE Repository",
                "value": "https://github.com/sdsc-ordes/gimie",
            },
        },
    ),
    force_refresh: bool = Query(
        False,
        description="Force refresh from external APIs, bypassing cache",
    ),
    enrich_orgs: bool = Query(
        False,
        description="Enable organization enrichment using PydanticAI agent to analyze and standardize organization information from git author emails, ORCID affiliations, and ROR API",
    ),
    enrich_users: bool = Query(
        False,
        description="Enable user/author enrichment using PydanticAI agent to analyze affiliations, ORCID data, and provide detailed author information",
    ),
) -> APIOutput:
    """
    Extract repository metadata using LLM with GIMIE context.

    Returns LLM-based analysis informed by GIMIE data. The LLM uses GIMIE
    output as context to generate more accurate and comprehensive metadata.

    **Organization Enrichment** (optional):
    When `enrich_orgs=true`, performs a second-pass agentic analysis using PydanticAI to:
    - Analyze git author emails to identify institutional affiliations
    - Query ROR (Research Organization Registry) for standardized organization names and IDs
    - Identify hierarchical relationships (departments, labs within universities)
    - Provide detailed EPFL relationship analysis with evidence
    - Enrich organization metadata with type, country, website, etc.

    **User Enrichment** (optional):
    When `enrich_users=true`, performs author/contributor enrichment to:
    - Analyze git author information and affiliations
    - Cross-reference with ORCID data
    - Provide comprehensive author profiles

    **Caching**: Results are cached with default TTL of 30 days (LLM) and 1 day (GIMIE).

    **Parameters**:
    - **full_path**: Full repository URL (e.g., `https://github.com/user/repo`)
    - **force_refresh**: Set to `true` to bypass cache and fetch fresh data
    - **enrich_orgs**: Set to `true` to enable organization enrichment with PydanticAI agent
    - **enrich_users**: Set to `true` to enable user/author enrichment with PydanticAI agent

    **Returns**:
    - Repository link
    - Repository type
    - Parsing timestamp
    - Repository Object with enriched metadata
    """

    repository = Repository(full_path, force_refresh=force_refresh)

    await repository.run_analysis(
        run_gimie=True,
        run_llm=True,
        run_user_enrichment=enrich_users,
        run_organization_enrichment=enrich_orgs,
    )

    output = repository.dump_results(output_type="pydantic")

    response = APIOutput(
        link=full_path,
        type=ResourceType.REPOSITORY,
        parsedTimestamp=datetime.now(),
        output=output,
    )

    return response


###########################################################
# Cache Management Endpoints
###########################################################


@app.get("/v1/cache/stats", tags=["Cache Management"])
async def get_cache_stats():
    """
    Get comprehensive cache statistics.

    Returns detailed information about cache performance, including:
    - Total entries and active/expired counts
    - Entries per API type
    - Hit counts and cache effectiveness
    - Database size and configuration

    Use this endpoint to monitor cache health and optimize TTL settings.

    **Returns**:
    - Detailed cache statistics and configuration
    """
    cache_manager = get_cache_manager()
    return cache_manager.get_cache_stats()


@app.get("/v1/cache/entries", tags=["Cache Management"])
async def list_cache_entries(
    api_type: Optional[str] = Query(
        None,
        description="Filter by API type (llm, gimie, orcid, github_user, github_org)",
    ),
    limit: int = Query(
        100,
        description="Maximum number of entries to return",
        ge=1,
        le=1000,
    ),
    offset: int = Query(
        0,
        description="Number of entries to skip (for pagination)",
        ge=0,
    ),
    include_expired: bool = Query(
        False,
        description="Include expired entries in results",
    ),
):
    """
    List cached entries with details.

    Returns a paginated list of cache entries showing:
    - Repository URL
    - API type (llm, gimie, orcid, etc.)
    - Enrichment type (orgs, users, or none)
    - Creation and expiration timestamps
    - Hit count and last access time

    **Filters**:
    - `api_type`: Show only specific API type (e.g., "llm" for LLM results)
    - `include_expired`: Include entries that have expired

    **Pagination**:
    - `limit`: Number of entries per page (default 100, max 1000)
    - `offset`: Skip N entries (for page 2, use offset=100 with limit=100)

    **Example**: List all cached LLM results:
    ```
    GET /v1/cache/entries?api_type=llm&limit=50
    ```

    **Returns**:
    - List of cache entries with metadata
    - Pagination information
    """
    cache_manager = get_cache_manager()
    return cache_manager.list_cache_entries(api_type, limit, offset, include_expired)


@app.post("/v1/cache/cleanup", tags=["Cache Management"])
async def cleanup_cache():
    """
    Clean up expired cache entries.

    Removes all cache entries that have passed their TTL expiration time.
    This helps maintain database size and performance.

    **Note**: Active (non-expired) entries are preserved.

    **Returns**:
    - Number of expired entries removed
    """
    cache_manager = get_cache_manager()
    removed_count = cache_manager.cleanup_expired()
    return {"message": f"Cleaned up {removed_count} expired cache entries"}


@app.post("/v1/cache/clear", tags=["Cache Management"])
async def clear_all_cache():
    """
    Clear all cache entries.

    Removes ALL cache entries, both active and expired. Use this when you need
    to completely reset the cache or when troubleshooting cache-related issues.

    **Warning**: This action cannot be undone. All cached data will be lost.

    **Returns**:
    - Total number of entries cleared
    """
    cache_manager = get_cache_manager()
    removed_count = cache_manager.clear_all_cache()
    return {"message": f"Cleared {removed_count} cache entries"}


@app.post("/v1/cache/enable", tags=["Cache Management"])
async def enable_cache():
    """
    Enable the caching system.

    Activates caching for all API endpoints. Subsequent requests will use
    cached data when available and within TTL.

    **Returns**:
    - Success message
    """
    cache_manager = get_cache_manager()
    cache_manager.enable_cache()
    return {"message": "Cache enabled"}


@app.post("/v1/cache/disable", tags=["Cache Management"])
async def disable_cache():
    """
    Disable the caching system.

    Deactivates caching for all API endpoints. All requests will fetch
    fresh data from external APIs, bypassing any cached entries.

    **Note**: Existing cache entries are preserved but not used.

    **Returns**:
    - Success message
    """
    cache_manager = get_cache_manager()
    cache_manager.disable_cache()
    return {"message": "Cache disabled"}


@app.delete("/v1/cache/invalidate/{api_type}", tags=["Cache Management"])
async def invalidate_cache(api_type: str, params: dict = None):
    """
    Invalidate specific cache entries.

    Removes cache entries for a specific API type and optional parameters.
    Useful when you know certain cached data has become stale.

    **Parameters**:
    - **api_type**: Type of API (e.g., `github_user`, `github_org`, `gimie`, `llm`)
    - **params**: Optional dictionary of parameters to match specific entries

    **Returns**:
    - Success or not found message
    """
    cache_manager = get_cache_manager()
    if params is None:
        params = {}

    success = cache_manager.invalidate_api_cache(api_type, params)
    if success:
        return {"message": f"Invalidated cache entries for {api_type}"}
    return {
        "message": f"No cache entries found for {api_type} with given parameters",
    }


@app.exception_handler(ValueError)
async def value_error_exception_handler(request: Request, exc: ValueError):
    return JSONResponse(
        status_code=400,
        content={"message": str(exc)},
    )
