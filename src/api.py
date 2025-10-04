import logging
import os
from datetime import datetime

from fastapi import FastAPI, HTTPException, Path, Query, Request
from fastapi.responses import JSONResponse

from .core.cache_manager import get_cache_manager
from .core.genai_model import llm_request_repo_infos, llm_request_userorg_infos
from .core.gimie_methods import extract_gimie
from .core.models import convert_jsonld_to_pydantic, convert_pydantic_to_zod_form_dict
from .core.organization_enrichment import enrich_organizations_from_dict
from .core.orgs_parser import parse_github_organization
from .core.users_parser import parse_github_user
from .utils.utils import enrich_author_with_orcid, merge_jsonld

logger = logging.getLogger(__name__)


def enrich_authors_with_orcid(metadata_dict: dict, force_refresh: bool = False) -> dict:
    """
    Enrich author objects with ORCID affiliations if orcidId is present.

    Args:
        metadata_dict: Metadata dictionary containing 'schema:author' or 'author' field
        force_refresh: If True, bypass ORCID cache and fetch fresh data

    Returns:
        Metadata dictionary with enriched author affiliations
    """
    # Handle both Zod format (schema:author) and plain format (author)
    author_key = None
    if "schema:author" in metadata_dict:
        author_key = "schema:author"
    elif "author" in metadata_dict:
        author_key = "author"

    if not author_key:
        return metadata_dict

    authors = metadata_dict.get(author_key)
    if not authors or not isinstance(authors, list):
        return metadata_dict

    # Enrich each author with ORCID affiliations
    enriched_authors = []
    for i, author in enumerate(authors):
        if isinstance(author, dict):
            author_name = author.get("name") or author.get(
                "schema:name",
                f"Author {i+1}",
            )
            orcid_id = author.get("orcidId") or author.get("md4i:orcidId", "")

            logger.info(f"Enriching author {i+1}: {author_name} (ORCID: {orcid_id})")

            try:
                enriched_author = enrich_author_with_orcid(
                    author,
                    use_cache=not force_refresh,
                )
                enriched_authors.append(enriched_author)

                # Log the result
                affiliations = enriched_author.get(
                    "affiliation",
                ) or enriched_author.get("schema:affiliation", [])
                logger.info(
                    f"  Result: {len(affiliations)} affiliations: {affiliations}",
                )

            except Exception as e:
                logger.error(f"  Error enriching {author_name}: {e}")
                enriched_authors.append(author)  # Keep original on error
        else:
            enriched_authors.append(author)

    metadata_dict[author_key] = enriched_authors
    logger.info(f"ORCID enrichment completed. Updated {len(enriched_authors)} authors")
    return metadata_dict


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


@app.get("/", tags=["System"])
def index():
    """
    Get API welcome message and system information.

    Returns basic information about the API version, GIMIE version, and configured LLM model.
    """
    return {
        "title": f"Hello, welcome to the Git Metadata Extractor v2.0.0. Gimie Version 0.7.2. LLM Model {os.environ['MODEL']}",
    }


@app.get("/v1/extract/json/{full_path:path}", tags=["Repository"])
async def extract(
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
):
    """
    Extract and enrich repository metadata in JSON format.

    Combines GIMIE repository analysis with LLM-based enrichment to provide
    comprehensive metadata about a Git repository. The output is converted to
    a Zod-compatible format for easy frontend integration.

    **Caching**: Results are cached with default TTL of 30 days (LLM) and 1 day (GIMIE).

    **Parameters**:
    - **full_path**: Full repository URL (e.g., `https://github.com/user/repo`)
    - **force_refresh**: Set to `true` to bypass cache and fetch fresh data

    **Returns**:
    - Repository link
    - Enriched metadata in Zod-compatible format
    - Cache status indicator
    """

    cache_manager = get_cache_manager()

    # Create cache parameters
    cache_params = {"full_path": full_path, "format": "json-ld", "max_tokens": 30000}

    def fetch_gimie_data():
        return extract_gimie(full_path, format="json-ld")

    async def fetch_llm_data():
        return await llm_request_repo_infos(
            str(full_path),
            output_format="json-ld",
            max_tokens=30000,
        )

    # Get GIMIE data (cached)
    jsonld_gimie_data = cache_manager.get_cached_or_fetch(
        api_type="gimie",
        params={"full_path": full_path, "format": "json-ld"},
        fetch_func=fetch_gimie_data,
        force_refresh=force_refresh,
    )

    try:
        # Get LLM data (cached or fetched) - automatically handles coroutines
        llm_result = await cache_manager.get_cached_or_fetch_async(
            api_type="llm",
            params=cache_params,
            fetch_func=fetch_llm_data,
            force_refresh=force_refresh,
        )

        merged_results = merge_jsonld(jsonld_gimie_data, llm_result)
        pydantic_data = convert_jsonld_to_pydantic(merged_results["@graph"])

    except Exception as e:
        pydantic_data = convert_jsonld_to_pydantic(jsonld_gimie_data["@graph"])
        logger.warning(f"{full_path} :: LLM service failed, using fallback data: {e}")

    zod_data = convert_pydantic_to_zod_form_dict(pydantic_data)

    # Enrich authors with ORCID affiliations
    logger.info(
        f"Starting ORCID enrichment for {full_path} (force_refresh={force_refresh})",
    )
    authors_before = len(zod_data.get("schema:author", []))
    logger.info(f"Found {authors_before} authors before enrichment")

    zod_data = enrich_authors_with_orcid(zod_data, force_refresh=force_refresh)

    authors_after = len(zod_data.get("schema:author", []))
    logger.info(f"ORCID enrichment completed. Authors after: {authors_after}")

    return {"link": full_path, "output": zod_data, "cached": not force_refresh}


@app.get("/v1/extract/json-ld/{full_path:path}", tags=["Repository"])
async def extract_jsonld(
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
):
    """
    Extract and enrich repository metadata in JSON-LD format.

    Combines GIMIE repository analysis with LLM-based enrichment to provide
    comprehensive metadata in JSON-LD format, aligned with the Imaging Plaza
    softwareSourceCode schema.

    **Caching**: Results are cached with default TTL of 30 days (LLM) and 1 day (GIMIE).

    **Parameters**:
    - **full_path**: Full repository URL (e.g., `https://github.com/user/repo`)
    - **force_refresh**: Set to `true` to bypass cache and fetch fresh data

    **Returns**:
    - Repository link
    - Merged metadata in JSON-LD format
    - Cache status indicator
    """

    cache_manager = get_cache_manager()

    def fetch_gimie_data():
        return extract_gimie(full_path, format="json-ld")

    async def fetch_llm_data():
        return await llm_request_repo_infos(str(full_path), max_tokens=20000)

    # Get GIMIE data (cached)
    jsonld_gimie_data = cache_manager.get_cached_or_fetch(
        api_type="gimie",
        params={"full_path": full_path, "format": "json-ld"},
        fetch_func=fetch_gimie_data,
        force_refresh=force_refresh,
    )

    try:
        # Get LLM data (cached or fetched) - automatically handles coroutines
        cache_params = {"full_path": full_path, "max_tokens": 20000}
        llm_result = await cache_manager.get_cached_or_fetch_async(
            api_type="llm",
            params=cache_params,
            fetch_func=fetch_llm_data,
            force_refresh=force_refresh,
        )
    except Exception as e:
        raise HTTPException(status_code=424, detail=f"Error from LLM service: {e}")

    merged_results = merge_jsonld(jsonld_gimie_data, llm_result)

    return {"link": full_path, "output": merged_results, "cached": not force_refresh}


@app.get("/v1/org/llm/json/{full_path:path}", tags=["Organization"])
async def get_org_json(
    full_path: str = Path(
        ...,
        description="GitHub organization URL or path",
        openapi_examples={
            "sdsc": {"summary": "SDSC Organization", "value": "github.com/sdsc-ordes"},
        },
    ),
    force_refresh: bool = Query(
        False,
        description="Force refresh from external APIs, bypassing cache",
    ),
    enrich_orgs: bool = Query(
        False,
        description="Enable organization enrichment using PydanticAI agent to analyze and standardize organization information using ROR API",
    ),
):
    """
    Retrieve and enrich GitHub organization metadata.

    Fetches organization profile from GitHub API and enriches it using LLM
    to extract additional insights and structured information.

    **Organization Enrichment** (optional):
    When `enrich_orgs=true`, performs a second-pass agentic analysis using PydanticAI to:
    - Query ROR (Research Organization Registry) for standardized organization names and IDs
    - Identify hierarchical relationships (departments, labs within universities)
    - Provide detailed EPFL relationship analysis with evidence
    - Enrich organization metadata with type, country, website, etc.

    **Caching**: Results are cached with TTL of 7 days.

    **Parameters**:
    - **full_path**: GitHub organization URL or path (e.g., `https://github.com/organization`)
    - **force_refresh**: Set to `true` to bypass cache and fetch fresh data
    - **enrich_orgs**: Set to `true` to enable organization enrichment with PydanticAI agent

    **Returns**:
    - Organization link
    - Enriched organization metadata
    - Organization enrichment results (if `enrich_orgs=true`)
    """

    cache_manager = get_cache_manager()
    org_name = full_path.split("/")[-1]

    def fetch_org_metadata():
        return parse_github_organization(org_name)

    def fetch_llm_metadata():
        org_metadata = parse_github_organization(org_name)
        return llm_request_userorg_infos(org_metadata, item_type="org")

    try:
        # Get GitHub organization metadata (cached)
        org_metadata = cache_manager.get_cached_or_fetch(
            api_type="github_org",
            params={"org_name": org_name},
            fetch_func=fetch_org_metadata,
            force_refresh=force_refresh,
        )

        # Get LLM processed metadata (cached) - automatically handles coroutines
        parsed_org_metadata = await cache_manager.get_cached_or_fetch_async(
            api_type="llm_org",
            params={"org_name": org_name, "item_type": "org"},
            fetch_func=fetch_llm_metadata,
            force_refresh=force_refresh,
        )

        org_metadata_dict = org_metadata.model_dump()
        org_metadata_dict["parseTimestamp"] = datetime.now().strftime("%Y-%m-%dT%H:%M")
        org_metadata_dict.update(parsed_org_metadata)

    except Exception as e:
        raise HTTPException(
            status_code=424,
            detail=f"Error from Organization JSON service: {e}",
        )

    # Perform organization enrichment if requested
    response = {"link": full_path, "output": org_metadata_dict}

    if enrich_orgs:
        logger.info(f"Starting organization enrichment for org {org_name}")
        try:
            organization_enrichment = await enrich_organizations_from_dict(
                org_metadata_dict,
                full_path,
            )
            logger.info(
                f"Organization enrichment completed for org. Found {len(organization_enrichment.get('organizations', []))} organizations",
            )

            # Update the main output with enriched organization data
            # Keep relatedToOrganizations as list of strings (backwards compatible)
            # Add relatedToOrganizationsROR as list of Organization objects (new field)
            enriched_orgs = organization_enrichment.get("organizations", [])
            if enriched_orgs:
                org_metadata_dict["relatedToOrganizations"] = [
                    org.get("legalName")
                    for org in enriched_orgs
                    if org.get("legalName")
                ]
                org_metadata_dict["relatedToOrganizationsROR"] = enriched_orgs

            # Update EPFL relationship with enriched analysis
            org_metadata_dict["relatedToEPFL"] = organization_enrichment.get(
                "relatedToEPFL",
                org_metadata_dict.get("relatedToEPFL"),
            )
            org_metadata_dict[
                "relatedToEPFLJustification"
            ] = organization_enrichment.get(
                "relatedToEPFLJustification",
                org_metadata_dict.get("relatedToEPFLJustification"),
            )

        except Exception as e:
            logger.error(
                f"Error during organization enrichment for org: {e}",
                exc_info=True,
            )
            # Don't fail the entire request or expose error in response, just log it

    return response


@app.get("/v1/user/llm/json/{full_path:path}", tags=["User"])
async def get_user_json(
    full_path: str = Path(
        ...,
        description="GitHub user URL or path",
        openapi_examples={
            "caviri": {"summary": "User Example", "value": "github.com/caviri"},
        },
    ),
    force_refresh: bool = Query(
        False,
        description="Force refresh from external APIs, bypassing cache",
    ),
    enrich_orgs: bool = Query(
        False,
        description="Enable organization enrichment using PydanticAI agent to analyze and standardize organization information from ORCID affiliations and ROR API",
    ),
):
    """
    Retrieve and enrich GitHub user profile metadata.

    Fetches user profile from GitHub API and enriches it using LLM
    to extract additional insights, research interests, and structured information.

    **Organization Enrichment** (optional):
    When `enrich_orgs=true`, performs a second-pass agentic analysis using PydanticAI to:
    - Query ORCID for user affiliations
    - Query ROR (Research Organization Registry) for standardized organization names and IDs
    - Identify hierarchical relationships (departments, labs within universities)
    - Provide detailed EPFL relationship analysis with evidence
    - Enrich organization metadata with type, country, website, etc.

    **Caching**: Results are cached with TTL of 7 days.

    **Parameters**:
    - **full_path**: GitHub user URL or path (e.g., `https://github.com/username`)
    - **force_refresh**: Set to `true` to bypass cache and fetch fresh data
    - **enrich_orgs**: Set to `true` to enable organization enrichment with PydanticAI agent

    **Returns**:
    - User profile link
    - Enriched user metadata
    - Organization enrichment results (if `enrich_orgs=true`)
    """

    cache_manager = get_cache_manager()
    username = full_path.split("/")[-1]

    def fetch_user_metadata():
        return parse_github_user(username)

    def fetch_llm_metadata():
        user_metadata = parse_github_user(username)
        return llm_request_userorg_infos(user_metadata, item_type="user")

    try:
        # Get GitHub user metadata (cached)
        user_metadata = cache_manager.get_cached_or_fetch(
            api_type="github_user",
            params={"username": username},
            fetch_func=fetch_user_metadata,
            force_refresh=force_refresh,
        )

        # Get LLM processed metadata (cached) - automatically handles coroutines
        parsed_user_metadata = await cache_manager.get_cached_or_fetch_async(
            api_type="llm_user",
            params={"username": username, "item_type": "user"},
            fetch_func=fetch_llm_metadata,
            force_refresh=force_refresh,
        )

        user_metadata_dict = user_metadata.model_dump()
        user_metadata_dict["parseTimestamp"] = datetime.now().strftime("%Y-%m-%dT%H:%M")
        user_metadata_dict.update(parsed_user_metadata)

    except Exception as e:
        raise HTTPException(status_code=424, detail=f"Error from Get User service: {e}")

    # Perform organization enrichment if requested
    response = {"link": full_path, "output": user_metadata_dict}

    if enrich_orgs:
        logger.info(f"Starting organization enrichment for user {username}")
        try:
            organization_enrichment = await enrich_organizations_from_dict(
                user_metadata_dict,
                full_path,
            )
            logger.info(
                f"Organization enrichment completed for user. Found {len(organization_enrichment.get('organizations', []))} organizations",
            )

            # Update the main output with enriched organization data
            # Keep relatedToOrganizations as list of strings (backwards compatible)
            # Add relatedToOrganizationsROR as list of Organization objects (new field)
            enriched_orgs = organization_enrichment.get("organizations", [])
            if enriched_orgs:
                user_metadata_dict["relatedToOrganizations"] = [
                    org.get("legalName")
                    for org in enriched_orgs
                    if org.get("legalName")
                ]
                user_metadata_dict["relatedToOrganizationsROR"] = enriched_orgs

            # Update EPFL relationship with enriched analysis
            user_metadata_dict["relatedToEPFL"] = organization_enrichment.get(
                "relatedToEPFL",
                user_metadata_dict.get("relatedToEPFL"),
            )
            user_metadata_dict[
                "relatedToEPFLJustification"
            ] = organization_enrichment.get(
                "relatedToEPFLJustification",
                user_metadata_dict.get("relatedToEPFLJustification"),
            )

        except Exception as e:
            logger.error(
                f"Error during organization enrichment for user: {e}",
                exc_info=True,
            )
            # Don't fail the entire request or expose error in response, just log it

    return response


@app.get("/v1/repository/gimie/json-ld/{full_path:path}", tags=["Repository"])
async def gimie(
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
):
    """
    Extract repository metadata using GIMIE only.

    Returns raw GIMIE analysis without LLM enrichment. GIMIE provides
    basic repository metadata extracted from Git platforms.

    **Caching**: Results are cached with TTL of 1 day.

    **Parameters**:
    - **full_path**: Full repository URL (e.g., `https://github.com/user/repo`)
    - **force_refresh**: Set to `true` to bypass cache and fetch fresh data

    **Returns**:
    - Repository link
    - GIMIE metadata in JSON-LD format
    - Cache status indicator
    """

    cache_manager = get_cache_manager()

    def fetch_gimie_data():
        return extract_gimie(full_path, format="json-ld")

    try:
        gimie_output = cache_manager.get_cached_or_fetch(
            api_type="gimie",
            params={"full_path": full_path, "format": "json-ld"},
            fetch_func=fetch_gimie_data,
            force_refresh=force_refresh,
        )
    except Exception as e:
        raise HTTPException(status_code=424, detail=f"Error from Gimie service: {e}")

    return {"link": full_path, "output": gimie_output, "cached": not force_refresh}


@app.get("/v1/repository/llm/json-ld/{full_path:path}", tags=["Repository"])
async def llm_jsonld(
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
):
    """
    Extract repository metadata using LLM only.

    Returns LLM-based analysis without GIMIE data. This provides AI-generated
    insights and structured metadata about the repository.

    **Caching**: Results are cached with default TTL of 30 days.

    **Parameters**:
    - **full_path**: Full repository URL (e.g., `https://github.com/user/repo`)
    - **force_refresh**: Set to `true` to bypass cache and fetch fresh data

    **Returns**:
    - Repository link
    - LLM-generated metadata in JSON-LD format
    - Cache status indicator
    """

    cache_manager = get_cache_manager()

    async def fetch_llm_data():
        return await llm_request_repo_infos(str(full_path), max_tokens=20000)

    try:
        # Get LLM data (cached or fetched) - automatically handles coroutines
        cache_params = {"full_path": full_path, "max_tokens": 20000}
        llm_result = await cache_manager.get_cached_or_fetch_async(
            api_type="llm",
            params=cache_params,
            fetch_func=fetch_llm_data,
            force_refresh=force_refresh,
        )
    except Exception as e:
        raise HTTPException(status_code=424, detail=f"Error from LLM service: {e}")

    return {"link": full_path, "output": llm_result, "cached": not force_refresh}


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
):
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

    **Caching**: Results are cached with default TTL of 30 days (LLM) and 1 day (GIMIE).

    **Parameters**:
    - **full_path**: Full repository URL (e.g., `https://github.com/user/repo`)
    - **force_refresh**: Set to `true` to bypass cache and fetch fresh data
    - **enrich_orgs**: Set to `true` to enable organization enrichment with PydanticAI agent

    **Returns**:
    - Repository link
    - LLM-generated metadata in JSON format with timestamp
    - Organization enrichment results (if `enrich_orgs=true`)
    - Cache status indicator
    """

    cache_manager = get_cache_manager()

    def fetch_gimie_data():
        return extract_gimie(full_path, format="json-ld")

    # Get GIMIE data (cached)
    jsonld_gimie_data = cache_manager.get_cached_or_fetch(
        api_type="gimie",
        params={"full_path": full_path, "format": "json-ld"},
        fetch_func=fetch_gimie_data,
        force_refresh=force_refresh,
    )

    async def fetch_llm_data():
        return await llm_request_repo_infos(
            str(full_path),
            gimie_output=jsonld_gimie_data,
            output_format="json",
            max_tokens=20000,
        )

    try:
        # Get LLM data (cached or fetched) - automatically handles coroutines
        cache_params = {
            "full_path": full_path,
            "output_format": "json",
            "max_tokens": 20000,
        }
        llm_result_raw = await cache_manager.get_cached_or_fetch_async(
            api_type="llm",
            params=cache_params,
            fetch_func=fetch_llm_data,
            force_refresh=force_refresh,
        )

        # Make a copy to avoid modifying cached data
        if isinstance(llm_result_raw, dict):
            llm_result = llm_result_raw.copy()
            llm_result["parseTimestamp"] = datetime.now().strftime("%Y-%m-%dT%H:%M")
        else:
            raise ValueError(
                f"Expected dict from LLM, got {type(llm_result_raw).__name__}",
            )
    except Exception as e:
        raise HTTPException(status_code=424, detail=f"Error from LLM service: {e}")

    # Enrich authors with ORCID affiliations
    logger.info(
        f"Starting ORCID enrichment for LLM JSON endpoint {full_path} (force_refresh={force_refresh})",
    )
    authors_before = len(llm_result.get("author", []))
    logger.info(f"Found {authors_before} authors before enrichment")

    llm_result = enrich_authors_with_orcid(llm_result, force_refresh=force_refresh)

    authors_after = len(llm_result.get("author", []))
    logger.info(
        f"ORCID enrichment completed for LLM JSON. Authors after: {authors_after}",
    )

    # Perform organization enrichment if requested
    response = {"link": full_path, "output": llm_result}

    if enrich_orgs:
        logger.info(f"Starting organization enrichment for {full_path}")
        try:
            organization_enrichment = await enrich_organizations_from_dict(
                llm_result,
                full_path,
            )
            logger.info(
                f"Organization enrichment completed. Found {len(organization_enrichment.get('organizations', []))} organizations",
            )

            # Update the main output with enriched organization data
            # Keep relatedToOrganizations as list of strings (backwards compatible)
            # Add relatedToOrganizationsROR as list of Organization objects (new field)
            enriched_orgs = organization_enrichment.get("organizations", [])
            if enriched_orgs:
                llm_result["relatedToOrganizations"] = [
                    org.get("legalName")
                    for org in enriched_orgs
                    if org.get("legalName")
                ]
                llm_result["relatedToOrganizationsROR"] = enriched_orgs

            # Update EPFL relationship with enriched analysis
            llm_result["relatedToEPFL"] = organization_enrichment.get(
                "relatedToEPFL",
                llm_result.get("relatedToEPFL"),
            )
            llm_result["relatedToEPFLJustification"] = organization_enrichment.get(
                "relatedToEPFLJustification",
                llm_result.get("relatedToEPFLJustification"),
            )

        except Exception as e:
            logger.error(f"Error during organization enrichment: {e}", exc_info=True)
            # Don't fail the entire request or expose error in response, just log it

    return response


# Cache Management Endpoints
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
