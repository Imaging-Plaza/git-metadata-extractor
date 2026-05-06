"""
API
"""

import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version as package_version
from typing import Optional


def _normalize_github_token_pool() -> None:
    """Split a comma-separated GITHUB_TOKEN into a per-process token pool.

    Runs before any v1/gimie import so module-level `os.environ["GITHUB_TOKEN"]`
    reads see a single valid token. The full list (deduped, order preserved) is
    exported as GITHUB_TOKEN_POOL for v2 REST hot paths to round-robin over.
    """
    raw = os.environ.get("GITHUB_TOKEN", "")
    if "," not in raw:
        return
    seen: set[str] = set()
    tokens: list[str] = []
    for piece in raw.split(","):
        token = piece.strip()
        if token and token not in seen:
            seen.add(token)
            tokens.append(token)
    if not tokens:
        return
    os.environ["GITHUB_TOKEN_POOL"] = ",".join(tokens)
    os.environ["GITHUB_TOKEN"] = tokens[0]


_normalize_github_token_pool()


def _resolve_package_version(name: str) -> str:
    try:
        return package_version(name)
    except PackageNotFoundError:
        return "unknown"

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Path,
    Query,
    Request,
    Response,
    status,
)
from fastapi.responses import HTMLResponse, JSONResponse

from src.v2.api import v2_router

from .v1.analysis import Organization, Repository, User
from .v1.cache import get_cache_manager
from .v1.data_models import (
    APIOutput,
    ResourceType,
)
from .v2.log_context import AsyncRequestContext, setup_logging
from .v1.utils.github_dependency import validate_github_token

# Setup enhanced logging with colors
# Allow LOG_LEVEL environment variable to override (DEBUG, INFO, WARNING, ERROR)
log_level_str = os.getenv("LOG_LEVEL", "INFO").upper()
log_level = getattr(logging, log_level_str, logging.INFO)
setup_logging(level=log_level, use_colors=True)


logger = logging.getLogger(__name__)


async def startup_event(app: FastAPI | None = None):
    """Initialize resources on application startup."""
    logger.info("🚀 Application startup - initializing resources")

    # Pre-warm the v2 provider cache so the SQLite file exists in WAL mode
    # before any request hits a worker. Without this, multi-worker uvicorn
    # against a cold .cache/ races on first-request to create+init the
    # file and the losing workers raise `database is locked`, surfacing
    # as 500s for the first 1-2 jobs of a batch run.
    if app is not None:
        try:
            from src.v2.dependencies import _resolve_provider_cache

            cache = _resolve_provider_cache(app.state)
            if cache is not None:
                logger.info("✅ v2 provider cache initialized")
        except Exception as exc:  # noqa: BLE001 — startup must not crash on cache issues
            logger.warning(f"v2 provider cache pre-warm skipped: {exc}")


async def shutdown_event():
    """Cleanup resources on application shutdown"""
    logger.info("🛑 Application shutdown - cleaning up resources")

    # Cleanup PydanticAI agents
    try:
        from .v1.agents.agents_management import cleanup_agents

        await cleanup_agents()
        logger.info("✅ Cleaned up PydanticAI agents")
    except Exception as e:
        logger.warning(f"Error cleaning up PydanticAI agents: {e}")

    # Cleanup user enrichment agents
    try:
        from .v1.agents.user_enrichment import cleanup_user_agents

        await cleanup_user_agents()
        logger.info("✅ Cleaned up user enrichment agents")
    except Exception as e:
        logger.warning(f"Error cleaning up user enrichment agents: {e}")

    # Cleanup organization enrichment agents
    try:
        from .v1.agents.organization_enrichment import cleanup_org_agents

        await cleanup_org_agents()
        logger.info("✅ Cleaned up organization enrichment agents")
    except Exception as e:
        logger.warning(f"Error cleaning up organization enrichment agents: {e}")

    # Run garbage collection
    import gc

    gc.collect()
    logger.info("✅ Garbage collection completed")


@asynccontextmanager
async def lifespan(app: FastAPI):
    await startup_event(app)
    try:
        yield
    finally:
        await shutdown_event()


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
    version="2.0.1",
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
    lifespan=lifespan,
    docs_url=None,
)


_SWAGGER_DARK_CSS = (
    "https://cdn.jsdelivr.net/gh/Amoenus/SwaggerDark@master/SwaggerDark.css"
)
_SWAGGER_LIGHT_CSS = "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui.css"
_SWAGGER_BUNDLE_JS = (
    "https://cdn.jsdelivr.net/npm/swagger-ui-dist@5/swagger-ui-bundle.js"
)
_FAVICON_URL = "https://fastapi.tiangolo.com/img/favicon.png"


@app.get("/docs", include_in_schema=False)
def custom_swagger_ui_html() -> HTMLResponse:
    title = f"{app.title} - Swagger UI"
    html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>{title}</title>
<link rel="shortcut icon" href="{_FAVICON_URL}">
<script>
  (function () {{
    // Resolve theme override before stylesheets are parsed to avoid FOUC.
    var override = null;
    try {{ override = localStorage.getItem('docs-theme'); }} catch (e) {{}}
    if (override === 'dark') document.documentElement.classList.add('dark');
    if (override === 'light') document.documentElement.classList.add('light');
    window.__docsThemeOverride = override;
  }})();
</script>
<link rel="stylesheet" href="{_SWAGGER_LIGHT_CSS}">
<link id="swagger-dark-css" rel="stylesheet" href="{_SWAGGER_DARK_CSS}"
      media="(prefers-color-scheme: dark)">
<style>
  #theme-toggle {{
    position: fixed; top: 12px; right: 16px; z-index: 9999;
    background: rgba(255,255,255,0.85); color: #222;
    border: 1px solid #ccc; border-radius: 999px;
    padding: 6px 12px; font-size: 14px; cursor: pointer;
    box-shadow: 0 1px 4px rgba(0,0,0,0.15); user-select: none;
  }}
  @media (prefers-color-scheme: dark) {{
    html:not(.light) #theme-toggle {{
      background: rgba(40,40,40,0.85); color: #eee; border-color: #555;
    }}
  }}
  html.dark #theme-toggle {{
    background: rgba(40,40,40,0.85); color: #eee; border-color: #555;
  }}
</style>
</head>
<body>
<button id="theme-toggle" type="button" aria-label="Toggle dark mode">Theme</button>
<div id="swagger-ui"></div>
<script src="{_SWAGGER_BUNDLE_JS}"></script>
<script>
  const ui = SwaggerUIBundle({{
    url: '/openapi.json',
    dom_id: '#swagger-ui',
    layout: 'BaseLayout',
    deepLinking: true,
    showExtensions: true,
    showCommonExtensions: true,
    oauth2RedirectUrl: window.location.origin + '/docs/oauth2-redirect',
    presets: [
      SwaggerUIBundle.presets.apis,
      SwaggerUIBundle.SwaggerUIStandalonePreset
    ],
  }});
  (function () {{
    var darkLink = document.getElementById('swagger-dark-css');
    var btn = document.getElementById('theme-toggle');
    var html = document.documentElement;
    var systemDarkQuery = window.matchMedia
      ? window.matchMedia('(prefers-color-scheme: dark)') : null;

    function activeTheme() {{
      if (html.classList.contains('dark')) return 'dark';
      if (html.classList.contains('light')) return 'light';
      return systemDarkQuery && systemDarkQuery.matches ? 'dark' : 'light';
    }}

    function apply(override) {{
      html.classList.remove('dark');
      html.classList.remove('light');
      if (override === 'dark') html.classList.add('dark');
      if (override === 'light') html.classList.add('light');

      // Force-on / force-off / system: tweak the media attribute on the
      // dark CSS so the override works without unloading the stylesheet.
      if (darkLink) {{
        if (override === 'dark') darkLink.media = 'all';
        else if (override === 'light') darkLink.media = 'not all';
        else darkLink.media = '(prefers-color-scheme: dark)';
      }}
      btn.textContent = activeTheme() === 'dark' ? '☀️ Light' : '🌙 Dark';
    }}

    apply(window.__docsThemeOverride);

    btn.addEventListener('click', function () {{
      var next = activeTheme() === 'dark' ? 'light' : 'dark';
      try {{ localStorage.setItem('docs-theme', next); }} catch (e) {{}}
      apply(next);
    }});

    if (systemDarkQuery && systemDarkQuery.addEventListener) {{
      systemDarkQuery.addEventListener('change', function () {{
        var stored = null;
        try {{ stored = localStorage.getItem('docs-theme'); }} catch (e) {{}}
        if (!stored) apply(null);
      }});
    }}
  }})();
</script>
</body>
</html>"""
    return HTMLResponse(html)

app.include_router(v2_router)


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

    Versions are resolved at runtime from installed package metadata
    (`git-metadata-extractor`, `gimie`). The LLM model line reports what the
    v2 runtime would resolve right now from `MODEL_CONFIGS`
    (`src/v1/llm/model_config.py`) given the credentials available in the
    environment.
    """
    from src.v2.agents.llm.runtime import LLMRuntimeConfigError, V2LLMRuntime

    try:
        resolved = V2LLMRuntime()._resolve_model_config()  # noqa: SLF001
        provider = resolved.get("provider", "unknown")
        model_name = resolved.get("model", "unknown")
        llm_model = f"{provider}:{model_name}"
    except LLMRuntimeConfigError as exc:
        llm_model = f"unconfigured ({exc})"

    return {
        "title": (
            "Hello, welcome to the Git Metadata Extractor "
            f"v{_resolve_package_version('git-metadata-extractor')}. "
            f"Gimie Version {_resolve_package_version('gimie')}. "
            f"LLM Model {llm_model}"
        ),
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


@app.get("/v1/org/llm/json/{full_path:path}", tags=["Organization"])
async def get_org_json(
    response: Response,
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
    github_info: dict = Depends(validate_github_token),
) -> APIOutput:
    """
    Retrieve and enrich GitHub organization metadata using atomic agents pipeline.

    Fetches organization profile from GitHub API and enriches it using a multi-stage
    atomic agents pipeline to extract structured metadata and relationships.

    **Atomic Agents Pipeline** (6 stages):
    1. **Context Compilation**: Gathers comprehensive organization information using tools:
       - Infoscience labs/orgunits search (EPFL organizational units)
       - Infoscience publications search (related publications)
       - Web search (DuckDuckGo) for additional context
       - Compiles all information into structured markdown
    2. **Structured Output**: Extracts basic identity fields (name, description) from compiled context
    3. **Classification**: Classifies organization type and scientific disciplines with justifications:
       - Organization type (Research Institute, University, Company, etc.)
       - Scientific disciplines (from closed list of valid disciplines)
    4. **Organization Identifier**: Identifies related organizations (parent, partner, affiliated organizations)
    5. **Linked Entities**: Searches academic catalogs (Infoscience) for:
       - Organizational units (orgunit) matching the organization
       - Publications related to the organization
       - Publications by organization members
    6. **EPFL Assessment**: Final holistic assessment of EPFL relationship with confidence scoring

    **Organization Enrichment** (optional):
    When `enrich_orgs=true`, performs ROR (Research Organization Registry) enrichment to:
    - Query ROR API for standardized organization names and IDs
    - Identify hierarchical relationships (departments, labs within universities)
    - Enrich organization metadata with type, country, website, etc.

    **Caching**: Results are cached with TTL of 365 days.

    **Parameters**:
    - **full_path**: GitHub organization URL or path (e.g., `https://github.com/organization`)
    - **force_refresh**: Set to `true` to bypass cache and fetch fresh data
    - **enrich_orgs**: Set to `true` to enable ROR-based organization enrichment

    **Returns**:
    - Organization link
    - Organization type
    - Parsing timestamp
    - Organization Object with enriched metadata
    - Usage statistics (token counts, timing, status)
    """
    org_name = full_path.split("/")[-1]

    # Ensure full_path is a valid URL
    if not full_path.startswith(("http://", "https://")):
        full_path = f"https://{full_path}"

    organization = Organization(org_name, force_refresh=force_refresh)

    await organization.run_analysis(
        run_llm=True,
        run_organization_enrichment=enrich_orgs,
    )

    output = organization.dump_results(output_type="pydantic")

    # Get usage statistics from the organization analysis
    usage_stats = organization.get_usage_stats()

    # Create APIStats with token usage data, timing, and status
    from .v1.data_models.api import APIStats

    stats = APIStats(
        agent_input_tokens=usage_stats["input_tokens"],
        agent_output_tokens=usage_stats["output_tokens"],
        estimated_input_tokens=usage_stats["estimated_input_tokens"],
        estimated_output_tokens=usage_stats["estimated_output_tokens"],
        duration=usage_stats["duration"],
        start_time=usage_stats["start_time"],
        end_time=usage_stats["end_time"],
        status_code=usage_stats["status_code"],
        github_rate_limit=github_info["rate_limit_limit"],
        github_rate_remaining=github_info["rate_limit_remaining"],
        github_rate_reset=github_info["rate_limit_reset"],
    )
    # Calculate total tokens (both official and estimated)
    stats.calculate_total_tokens()

    # Set rate limit response headers
    response.headers["X-RateLimit-Limit"] = str(github_info["rate_limit_limit"])
    response.headers["X-RateLimit-Remaining"] = str(github_info["rate_limit_remaining"])
    response.headers["X-RateLimit-Reset"] = github_info["rate_limit_reset"].isoformat()

    api_response = APIOutput(
        link=full_path,
        type=ResourceType.ORGANIZATION,
        parsedTimestamp=datetime.now(),
        output=output,
        stats=stats,
    )

    return api_response


@app.get("/v1/user/llm/json/{full_path:path}", tags=["User"])
async def get_user_json(
    response: Response,
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
    enrich_users: bool = Query(
        False,
        description="Enable user/author enrichment using PydanticAI agent to analyze affiliations, ORCID data, and provide detailed author information",
    ),
    github_info: dict = Depends(validate_github_token),
) -> APIOutput:
    """
    Retrieve and enrich GitHub user profile metadata.

    Uses a multi-stage atomic agent pipeline to extract and enrich user information:
    1. **Context Compiler**: Gathers user information using tools (ORCID, Infoscience authors/labs, web search) and compiles into markdown
    2. **Structured Output**: Extracts basic identity fields (name, fullname, githubHandle)
    3. **Discipline/Position Classifier**: Classifies user's discipline(s) and position(s) with justifications (using closed list of disciplines)
    4. **Organization Identifier**: Identifies related organizations (reuses repository's organization identification logic)
    5. **Linked Entities Searcher**: Searches Infoscience for persona (user) and orgunit (organizations) entities
    6. **EPFL Assessment**: Final holistic assessment of EPFL relationship (runs after all enrichments)

    **Context Compiler Tools**:
    - ORCID search for author information and affiliations
    - Infoscience author search (persona) for EPFL researchers
    - Infoscience lab search (orgunit) for EPFL labs and organizational units
    - Web search for additional context
    - Author publications retrieval from Infoscience

    **Linked Entities Enhancement**:
    - When searching for orgunit (labs), includes user's name in search queries
    - Some labs use GitHub user profiles, so searching with both lab name and user name helps find them
    - Searches both persona (user) and orgunit (organizations) in Infoscience

    **Organization Enrichment** (optional):
    When `enrich_orgs=true`, performs a second-pass agentic analysis using PydanticAI to:
    - Query ORCID for user affiliations
    - Query ROR (Research Organization Registry) for standardized organization names and IDs
    - Identify hierarchical relationships (departments, labs within universities)
    - Provide detailed EPFL relationship analysis with evidence
    - Enrich organization metadata with type, country, website, etc.

    **User Enrichment** (optional):
    When `enrich_users=true`, performs author/contributor enrichment to:
    - Analyze git author information and affiliations
    - Cross-reference with ORCID data
    - Provide comprehensive author profiles

    **Caching**: Results are cached with TTL of 365 days.

    **Parameters**:
    - **full_path**: GitHub user URL or path (e.g., `https://github.com/username`)
    - **force_refresh**: Set to `true` to bypass cache and fetch fresh data
    - **enrich_orgs**: Set to `true` to enable organization enrichment with PydanticAI agent
    - **enrich_users**: Set to `true` to enable user/author enrichment with PydanticAI agent

    **Returns**:
    - User profile link
    - User type
    - Parsing timestamp
    - User Object with enriched metadata (id field set to full GitHub profile URL)
    - Statistics (token usage, timing, and status)
    """
    username = full_path.split("/")[-1]

    # Ensure full_path is a valid URL
    if not full_path.startswith(("http://", "https://")):
        full_path = f"https://{full_path}"

    user = User(username, force_refresh=force_refresh)

    await user.run_analysis(
        run_organization_enrichment=enrich_orgs,
        run_user_enrichment=enrich_users,
    )

    output = user.dump_results(output_type="pydantic")

    # Get usage statistics from the user analysis
    usage_stats = user.get_usage_stats()

    # Create APIStats with token usage data, timing, and status
    from .v1.data_models.api import APIStats

    stats = APIStats(
        agent_input_tokens=usage_stats["input_tokens"],
        agent_output_tokens=usage_stats["output_tokens"],
        estimated_input_tokens=usage_stats["estimated_input_tokens"],
        estimated_output_tokens=usage_stats["estimated_output_tokens"],
        duration=usage_stats["duration"],
        start_time=usage_stats["start_time"],
        end_time=usage_stats["end_time"],
        status_code=usage_stats["status_code"],
        github_rate_limit=github_info["rate_limit_limit"],
        github_rate_remaining=github_info["rate_limit_remaining"],
        github_rate_reset=github_info["rate_limit_reset"],
    )
    # Calculate total tokens (both official and estimated)
    stats.calculate_total_tokens()

    # Set rate limit response headers
    response.headers["X-RateLimit-Limit"] = str(github_info["rate_limit_limit"])
    response.headers["X-RateLimit-Remaining"] = str(github_info["rate_limit_remaining"])
    response.headers["X-RateLimit-Reset"] = github_info["rate_limit_reset"].isoformat()

    api_response = APIOutput(
        link=full_path,
        type=ResourceType.USER,
        parsedTimestamp=datetime.now(),
        output=output,
        stats=stats,
    )

    return api_response


@app.get(
    "/v1/repository/gimie/json-ld/{full_path:path}",
    tags=["Repository"],
    responses={
        200: {
            "description": "Successful Response",
            "content": {
                "application/json": {
                    "example": {
                        "link": "https://github.com/sdsc-ordes/gimie",
                        "type": "repository",
                        "parsedTimestamp": "2024-01-15T10:30:00.000Z",
                        "output": {
                            "@context": {
                                "schema": "http://schema.org/",
                                "codemeta": "https://codemeta.github.io/terms/",
                            },
                            "@graph": [
                                {
                                    "@id": "https://github.com/sdsc-ordes/gimie",
                                    "@type": "schema:SoftwareSourceCode",
                                    "schema:name": "GIMIE",
                                    "schema:description": "Graph-based metadata extraction",
                                    "schema:codeRepository": "https://github.com/sdsc-ordes/gimie",
                                    "codemeta:dateCreated": "2023-01-15",
                                },
                            ],
                        },
                        "stats": {
                            "agent_input_tokens": 0,
                            "agent_output_tokens": 0,
                            "total_tokens": 0,
                            "estimated_input_tokens": 0,
                            "estimated_output_tokens": 0,
                            "estimated_total_tokens": 0,
                            "duration": 1.23,
                            "start_time": "2024-01-15T10:29:58.770Z",
                            "end_time": "2024-01-15T10:30:00.000Z",
                            "status_code": 200,
                        },
                    },
                },
            },
        },
    },
)
async def gimie(
    response: Response,
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
    github_info: dict = Depends(validate_github_token),
) -> APIOutput:
    """
    Extract repository metadata using GIMIE only.

    Returns raw GIMIE analysis without LLM enrichment. GIMIE provides
    basic repository metadata extracted from Git platforms in JSON-LD format.

    **Caching**: Results are cached with TTL of 1 day.

    **Parameters**:
    - **full_path**: Full repository URL (e.g., `https://github.com/user/repo`)
    - **force_refresh**: Set to `true` to bypass cache and fetch fresh data

    **Returns**:
    - Repository link
    - Repository type
    - Parsing timestamp
    - GIMIE metadata in JSON-LD format
    - Statistics (timing and status)
    """

    try:
        repository = Repository(full_path, force_refresh=force_refresh)

        await repository.run_analysis(
            run_gimie=True,
            run_llm=False,
            run_user_enrichment=False,
            run_organization_enrichment=False,
        )

        # Get raw gimie JSON-LD output (not the Pydantic model)
        gimie_output = repository.gimie

        # Get usage statistics from the repository (no tokens for gimie-only)
        usage_stats = repository.get_usage_stats()

        # Create APIStats with timing information (no token usage since no LLM)
        from .v1.data_models.api import APIStats

        stats = APIStats(
            agent_input_tokens=0,
            agent_output_tokens=0,
            estimated_input_tokens=0,
            estimated_output_tokens=0,
            duration=usage_stats["duration"],
            start_time=usage_stats["start_time"],
            end_time=usage_stats["end_time"],
            status_code=usage_stats["status_code"],
            github_rate_limit=github_info["rate_limit_limit"],
            github_rate_remaining=github_info["rate_limit_remaining"],
            github_rate_reset=github_info["rate_limit_reset"],
        )
        # Calculate total tokens (will be 0 for gimie-only)
        stats.calculate_total_tokens()

        # Set rate limit response headers
        response.headers["X-RateLimit-Limit"] = str(github_info["rate_limit_limit"])
        response.headers["X-RateLimit-Remaining"] = str(
            github_info["rate_limit_remaining"],
        )
        rate_reset = github_info["rate_limit_reset"]
        response.headers["X-RateLimit-Reset"] = (
            rate_reset.isoformat() if rate_reset is not None else ""
        )

        api_response = APIOutput(
            link=full_path,
            type=ResourceType.REPOSITORY,
            parsedTimestamp=datetime.now(),
            output=gimie_output,
            stats=stats,
        )

        return api_response
    except HTTPException:
        raise
    except ConnectionError as e:
        # GIMIE raises ConnectionError for GitHub REST/GraphQL failures (incl. secondary rate limits).
        msg = str(e)
        lower = msg.lower()
        logger.warning("GIMIE GitHub API error for %s: %s", full_path, msg)
        if (
            "secondary rate limit" in lower
            or "rate limit exceeded" in lower
            or "api rate limit exceeded" in lower
        ):
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=msg,
            ) from e
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=msg,
        ) from e
    except Exception as e:
        logger.exception("GIMIE JSON-LD failed for %s", full_path)
        raise HTTPException(
            status_code=502,
            detail=f"GIMIE extraction failed: {e!s}",
        ) from e


@app.get(
    "/v1/repository/llm/json-ld/{full_path:path}",
    tags=["Repository"],
    responses={
        200: {
            "description": "Successful Response",
            "content": {
                "application/json": {
                    "example": {
                        "link": "https://github.com/sdsc-ordes/gimie",
                        "type": "repository",
                        "parsedTimestamp": "2024-01-15T10:30:00.000Z",
                        "output": {
                            "@context": {
                                "schema": "http://schema.org/",
                                "sd": "https://w3id.org/okn/o/sd#",
                                "imag": "https://imaging-plaza.epfl.ch/ontology/",
                                "md4i": "https://w3id.org/md4i/",
                            },
                            "@graph": [
                                {
                                    "@id": "https://github.com/sdsc-ordes/gimie",
                                    "@type": "http://schema.org/SoftwareSourceCode",
                                    "schema:name": "GIMIE",
                                    "schema:description": "Graph-based metadata extraction tool",
                                    "schema:codeRepository": [
                                        {"@id": "https://github.com/sdsc-ordes/gimie"},
                                    ],
                                    "schema:programmingLanguage": ["Python"],
                                    "schema:author": [
                                        {
                                            "@type": "http://schema.org/Person",
                                            "schema:name": "John Doe",
                                            "md4i:orcidId": "0000-0001-2345-6789",
                                        },
                                    ],
                                },
                            ],
                        },
                        "stats": {
                            "agent_input_tokens": 1500,
                            "agent_output_tokens": 800,
                            "total_tokens": 2300,
                            "estimated_input_tokens": 1520,
                            "estimated_output_tokens": 810,
                            "estimated_total_tokens": 2330,
                            "duration": 3.45,
                            "start_time": "2024-01-15T10:29:56.555Z",
                            "end_time": "2024-01-15T10:30:00.000Z",
                            "status_code": 200,
                        },
                    },
                },
            },
        },
    },
)
async def llm_jsonld(
    response: Response,
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
    github_info: dict = Depends(validate_github_token),
) -> APIOutput:
    """
    Extract repository metadata using LLM with GIMIE context in JSON-LD format.

    Returns LLM-based analysis informed by GIMIE data in JSON-LD format.
    The Pydantic model is converted to JSON-LD with proper semantic URIs
    and JSON-LD structure (@context, @type, etc.).

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

    **Caching**: Results are cached with default TTL of 365 days (LLM) and 1 day (GIMIE).

    **Parameters**:
    - **full_path**: Full repository URL (e.g., `https://github.com/user/repo`)
    - **force_refresh**: Set to `true` to bypass cache and fetch fresh data
    - **enrich_orgs**: Set to `true` to enable organization enrichment with PydanticAI agent
    - **enrich_users**: Set to `true` to enable user/author enrichment with PydanticAI agent

    **Returns**:
    - Repository link
    - Repository type
    - Parsing timestamp
    - Repository metadata in JSON-LD format
    - Statistics (token usage, timing, and status)
    """

    repository = Repository(full_path, force_refresh=force_refresh)

    await repository.run_analysis(
        run_gimie=True,
        run_llm=True,
        run_user_enrichment=enrich_users,
        run_organization_enrichment=enrich_orgs,
    )

    # Check if analysis succeeded
    if repository.data is None:
        logger.error(f"Repository analysis failed for {full_path}: no data available")
        raise HTTPException(
            status_code=500,
            detail=f"Repository analysis failed: no data generated for {full_path}",
        )

    # Debug: Check what type repository.data is
    logger.info(f"Repository data type: {type(repository.data).__name__}")
    logger.info(
        f"Repository data model: {repository.data.__class__.__name__ if hasattr(repository.data, '__class__') else 'N/A'}",
    )

    # Get JSON-LD output using the new conversion method
    try:
        jsonld_output = repository.dump_results(output_type="json-ld")
        logger.info(f"JSON-LD output type: {type(jsonld_output)}")
        logger.info(
            f"JSON-LD output keys: {jsonld_output.keys() if isinstance(jsonld_output, dict) else 'Not a dict'}",
        )

        if jsonld_output is None:
            raise ValueError("JSON-LD conversion returned None")

        if not isinstance(jsonld_output, dict):
            raise ValueError(
                f"JSON-LD conversion returned unexpected type: {type(jsonld_output)}",
            )

        # Verify it has JSON-LD structure
        if "@context" not in jsonld_output or "@graph" not in jsonld_output:
            logger.error(f"Invalid JSON-LD structure. Output: {jsonld_output}")
            raise ValueError("Missing @context or @graph in JSON-LD output")

        # Debug: Check @graph content
        graph = jsonld_output.get("@graph", [])
        logger.info(f"JSON-LD @graph length: {len(graph)}")
        if len(graph) > 0:
            first_entity = graph[0]
            logger.info(f"First entity @type: {first_entity.get('@type', 'N/A')}")
            logger.info(
                f"First entity keys (first 10): {list(first_entity.keys())[:10]}",
            )
        else:
            logger.error("JSON-LD @graph is empty!")

    except Exception as e:
        logger.error(f"Failed to convert to JSON-LD: {e}", exc_info=True)
        raise HTTPException(
            status_code=500,
            detail=f"Failed to convert repository data to JSON-LD: {e!s}",
        )

    # Get usage statistics from the repository
    usage_stats = repository.get_usage_stats()

    # Create APIStats with token usage data, timing, and status
    from .v1.data_models.api import APIStats

    stats = APIStats(
        agent_input_tokens=usage_stats["input_tokens"],
        agent_output_tokens=usage_stats["output_tokens"],
        estimated_input_tokens=usage_stats["estimated_input_tokens"],
        estimated_output_tokens=usage_stats["estimated_output_tokens"],
        duration=usage_stats["duration"],
        start_time=usage_stats["start_time"],
        end_time=usage_stats["end_time"],
        status_code=usage_stats["status_code"],
        github_rate_limit=github_info["rate_limit_limit"],
        github_rate_remaining=github_info["rate_limit_remaining"],
        github_rate_reset=github_info["rate_limit_reset"],
    )
    # Calculate total tokens (both official and estimated)
    stats.calculate_total_tokens()

    # Set rate limit response headers
    response.headers["X-RateLimit-Limit"] = str(github_info["rate_limit_limit"])
    response.headers["X-RateLimit-Remaining"] = str(github_info["rate_limit_remaining"])
    response.headers["X-RateLimit-Reset"] = github_info["rate_limit_reset"].isoformat()

    api_response = APIOutput(
        link=full_path,
        type=ResourceType.REPOSITORY,
        parsedTimestamp=datetime.now(),
        output=jsonld_output,
        stats=stats,
    )

    # Debug: Log what we're about to return
    logger.info(f"Response output type before return: {type(api_response.output)}")
    if isinstance(api_response.output, dict):
        logger.info(f"Response output has keys: {list(api_response.output.keys())[:5]}")

    return api_response


@app.get("/v1/repository/llm/json/{full_path:path}", tags=["Repository"])
async def llm_json(
    response: Response,
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
    github_info: dict = Depends(validate_github_token),
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

    **Caching**: Results are cached with default TTL of 365 days (LLM) and 1 day (GIMIE).

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

    # Get usage statistics from the repository
    usage_stats = repository.get_usage_stats()

    # Create APIStats with token usage data, timing, and status
    from .v1.data_models.api import APIStats

    stats = APIStats(
        agent_input_tokens=usage_stats["input_tokens"],
        agent_output_tokens=usage_stats["output_tokens"],
        estimated_input_tokens=usage_stats["estimated_input_tokens"],
        estimated_output_tokens=usage_stats["estimated_output_tokens"],
        duration=usage_stats["duration"],
        start_time=usage_stats["start_time"],
        end_time=usage_stats["end_time"],
        status_code=usage_stats["status_code"],
        github_rate_limit=github_info["rate_limit_limit"],
        github_rate_remaining=github_info["rate_limit_remaining"],
        github_rate_reset=github_info["rate_limit_reset"],
    )
    # Calculate total tokens (both official and estimated)
    stats.calculate_total_tokens()

    # Set rate limit response headers
    response.headers["X-RateLimit-Limit"] = str(github_info["rate_limit_limit"])
    response.headers["X-RateLimit-Remaining"] = str(github_info["rate_limit_remaining"])
    response.headers["X-RateLimit-Reset"] = github_info["rate_limit_reset"].isoformat()

    api_response = APIOutput(
        link=full_path,
        type=ResourceType.REPOSITORY,
        parsedTimestamp=datetime.now(),
        output=output,
        stats=stats,
    )

    return api_response


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
