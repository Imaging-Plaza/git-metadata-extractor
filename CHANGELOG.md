# Changelog

All notable changes to this project will be documented in this file.

## [Unpublished]

### Added
- **V2 Phase 0 strict schema promotion (`P0-01`)**:
  - Promoted 6 strict JSON Schemas from `dev/ontology-v2-json-response/a-001/json-schema/strict/` to `src/v2/schemas/strict/`:
    - `person.schema.json`
    - `repository.schema.json`
    - `organization.schema.json`
    - `membership.schema.json`
    - `contribution.schema.json`
    - `article.schema.json`
  - Added new v2 package markers:
    - `src/v2/__init__.py`
    - `src/v2/schemas/__init__.py`
- **V2 Phase 0 agent schema promotion (`P0-02`)**:
  - Promoted 6 agent JSON Schemas from `dev/ontology-v2-json-response/a-001/json-schema/agent/` to `src/v2/schemas/agent/`:
    - `person.schema.json`
    - `repository.schema.json`
    - `organization.schema.json`
    - `membership.schema.json`
    - `contribution.schema.json`
    - `article.schema.json`
- **V2 Phase 0 test infrastructure (`P0-03`)**:
  - Added `tests/v2/conftest.py` with shared session fixtures:
    - `v2_test_config`
    - `load_schema()`
    - `load_fixture()`
    - `load_golden()`
  - Added v2 fixture/golden scaffold directories:
    - `tests/v2/fixtures/schema/{strict,agent}/`
    - `tests/v2/fixtures/providers/{github,orcid,infoscience,ror}/`
    - `tests/v2/fixtures/scenarios/`
    - `tests/v2/golden/{extract,graph}/`
  - Added schema fixture copies in:
    - `tests/v2/fixtures/schema/strict/*.schema.json`
    - `tests/v2/fixtures/schema/agent/*.schema.json`
  - Added infrastructure smoke tests:
    - `tests/v2/test_test_infrastructure.py`

### Changed
- **Agent workflow documentation**:
  - Updated `AGENTS.md` with a dedicated `V2 Phase 0 TDD Track` section.
  - Advanced the phase entry task to `P0-03-test-infrastructure.md` after completing `P0-02`.
  - Added explicit validation commands for promoted schemas:
    - `python -m json.tool src/v2/schemas/strict/*.json`
    - `python -m json.tool src/v2/schemas/agent/*.json`
    - `just test-file tests/v2/test_promoted_strict_schemas.py`
    - `just test-file tests/v2/test_promoted_agent_schemas.py`
- **Pytest configuration**:
  - Registered the `v2` marker in `pyproject.toml` under `[tool.pytest.ini_options]`.
- **Agent workflow documentation**:
  - Advanced the phase entry task to `P0-04-strict-schema-valid-tests.md` after completing `P0-03`.
  - Added explicit v2 infrastructure check commands:
    - `pytest tests/v2/ --collect-only`
    - `pytest tests/v2 -m v2 --collect-only`
    - `just test-file tests/v2/test_test_infrastructure.py`

### Testing
- Added `tests/v2/test_promoted_strict_schemas.py` to verify:
  - promoted schema files exist and parse as JSON,
  - promoted files are byte-identical to source artifacts in `dev/`,
  - each promoted schema passes `jsonschema` meta-schema validation.
- Added `tests/v2/test_promoted_agent_schemas.py` to verify:
  - promoted agent schema files exist and parse as JSON,
  - promoted files are byte-identical to source artifacts in `dev/`,
  - each promoted schema passes `jsonschema` meta-schema validation,
  - each agent schema preserves all property names present in its strict counterpart.
- Added `tests/v2/test_test_infrastructure.py` to verify:
  - `load_schema("strict", "person")` returns a parsed JSON object,
  - `load_fixture("schema/strict", "person.schema")` resolves nested fixture groups,
  - `v2_test_config` points to expected test fixture/golden roots.


## [2.0.1] - 2026-02-16

### Added
- Documentation and CI for github-pages

### Changed
- Bumped project version to `2.0.1`.
- Updated API version metadata and root welcome message to `v2.0.1`.



## [2.0.0] - 2025-10-07

### Added
- **Project restructuring** for improved maintainability and modularity:
  - Reorganized `src/core/` monolithic directory into categorized subdirectories under `src/`:
    - `src/agents/` - PydanticAI agents for organization and user enrichment
    - `src/cache/` - Caching infrastructure and SQLite cache manager
    - `src/data_models/` - Pydantic models and schemas (Person, Organization, SoftwareSourceCode, etc.)
    - `src/gimie/` - GIMIE integration methods for repository metadata extraction
    - `src/llm/` - LLM processing and GenAI model wrapper
    - `src/parsers/` - Organization and user parsers for structured data extraction
    - `src/validation/` - Verification and validation logic
  - Created proper `__init__.py` files with explicit exports for all modules
  - Improved import paths throughout the codebase (e.g., `from src.agents import...` instead of `from src.core.organization_enrichment import...`)
  - Enhanced code organization and discoverability
- **SQLite-based caching system** for external API calls (GitHub, ORCID, GIMIE, LLM)
  - Automatic TTL (Time To Live) expiration with configurable settings per API type
  - Default TTL: 30 days (LLM), 7 days (GitHub users/orgs), 14 days (ORCID), 1 day (GIMIE)
  - Thread-safe operations for concurrent access
  - JSON storage for complex API responses
- **Force refresh capability** via `force_refresh` query parameter on all data endpoints
- **Cache management endpoints**:
  - `GET /v1/cache/stats` - View comprehensive cache statistics
  - `POST /v1/cache/cleanup` - Remove expired cache entries
  - `POST /v1/cache/clear` - Clear all cache entries
  - `POST /v1/cache/enable` - Enable caching system
  - `POST /v1/cache/disable` - Disable caching system
  - `DELETE /v1/cache/invalidate/{api_type}` - Invalidate specific cache entries
- **Environment-based cache configuration**:
  - `CACHE_ENABLED` - Enable/disable caching
  - `CACHE_DEFAULT_TTL_DAYS` - Default TTL in days
  - `CACHE_DB_PATH` - Custom database location
  - API-specific TTL overrides (e.g., `CACHE_GITHUB_USER_TTL_DAYS`)
  - Cache size and cleanup settings
- **Enhanced FastAPI documentation**:
  - Comprehensive API metadata (title, description, version, contact, license)
  - Detailed endpoint docstrings with parameter and return descriptions
  - Organized API endpoints with tags (Repository, User, Organization, Cache Management, System)
  - OpenAPI schema improvements for better interactive documentation
- **Cache statistics and monitoring**:
  - Total entries and active/expired counts
  - Entries breakdown by API type
  - Hit counts for cache effectiveness analysis
  - Database size reporting
- **Performance benefits**:
  - Up to 90% reduction in external API requests
  - Faster response times with instant cache retrieval
  - Rate limit protection for GitHub/ORCID APIs
  - Cost savings on LLM API calls
- **ORCID affiliation enrichment**:
  - Automatic extraction of ORCID IDs from author metadata
  - Selenium-based scraping of ORCID profiles for employment and education history
  - Smart affiliation merging that preserves existing affiliations and adds ORCID data
  - Support for both Zod format (`schema:author`, `md4i:orcidId`) and plain format (`author`, `orcidId`)
  - Integration with both main extraction and LLM JSON endpoints
- **Enhanced logging system**:
  - Comprehensive logging for ORCID enrichment process
  - Detailed error handling and debugging information
  - Cache operation logging for monitoring and troubleshooting
  - Selenium operation logging for ORCID scraping
- **GPT-5 model support** - Full support for GPT-5 and reasoning models
  - Support for GPT-5, GPT-5 variants (gpt-5-mini, gpt-5-nano), o3-mini, and o4-mini models
  - Proper model detection logic to handle GPT-5 and reasoning models
  - Uses `beta.chat.completions.parse()` with structured outputs for all models
  - Lazy initialization for async OpenAI client to prevent API key issues at module load
  - Comprehensive error logging with error type and detailed debugging information
  - Retry logic with exponential backoff for handling connection errors
  - Unified response parsing for all OpenAI models using `.parsed` attribute
- **Organization Enrichment System** using PydanticAI for agentic analysis
  - Second-pass analysis to refine and enrich organization information
  - PydanticAI agent with intelligent tool usage for:
    - ROR (Research Organization Registry) API queries for standardized org data
    - Web search integration (DuckDuckGo) for additional context
    - Email domain analysis for institutional affiliation detection
  - Enhanced `Organization` model with new fields:
    - `alternateNames` - Other names the organization is known by
    - `organizationType` - Type classification (university, lab, company, etc.)
    - `parentOrganization` - Parent organization for hierarchical relationships
    - `country` - Country location
    - `website` - Official website URL
  - Optional `enrich_orgs=true` parameter on existing `/v1/repository/llm/json` endpoint
    - Non-breaking change - enrichment only runs when explicitly requested
    - Analyzes git author emails, ORCID affiliations, and existing metadata
    - Provides detailed EPFL relationship analysis with evidence
    - Graceful error handling - errors don't break the main request
  - Comprehensive documentation in `docs/ORGANIZATION_ENRICHMENT.md`
  - Example script: `examples/example_organization_enrichment.py`
  - Test suite: `tests/test_organization_enrichment.py`
- **Organization enrichment for User and Organization endpoints**
  - Added `enrich_orgs=true` query parameter to `/v1/user/llm/json/{full_path:path}` endpoint
  - Added `enrich_orgs=true` query parameter to `/v1/org/llm/json/{full_path:path}` endpoint
  - Both endpoints now support ROR (Research Organization Registry) enrichment
  - Consistent enrichment functionality across repository, user, and organization endpoints
  - Enhanced organization metadata with ROR IDs, types, countries, websites, and hierarchical relationships
  - Detailed EPFL relationship analysis for user and organization profiles
- **Git commit temporal tracking**:
  - Added `Commits` model with `firstCommitDate` and `lastCommitDate` fields per author
  - Enhanced `extract_git_authors()` to extract first and last commit dates using git log
  - Dates stored in ISO format (YYYY-MM-DD) for consistency
  - JSON-LD context mappings added for `imag:firstCommitDate` and `imag:lastCommitDate`
- **Organization confidence scoring system**:
  - Added `confidenceOfAttribution` field to `Organization` model (0.0-1.0 scale)
  - Added `relatedToEPFLConfidence` field to `OrganizationEnrichmentResult` model
  - Enhanced PydanticAI agent with detailed confidence scoring guidelines:
    - 0.9-1.0: Strong evidence (verified affiliations, official emails, ORCID data)
    - 0.7-0.89: Good evidence (domain match, indirect affiliation)
    - 0.5-0.69: Moderate evidence (collaborations, shared projects)
    - 0.3-0.49: Weak evidence (geographical proximity, field similarity)
    - 0.0-0.29: Minimal or no evidence
  - Confidence assessment considers temporal alignment between commit dates and affiliation dates
  - JSON-LD context mapping for `imag:confidenceOfAttribution`
- **ORCID parser overhaul** - Complete rewrite for reliability and data completeness:
  - Fixed employment extraction to parse line-by-line text content instead of unreliable HTML containers
  - Fixed education extraction with same line-by-line parsing approach
  - Enhanced date extraction to support multiple formats:
    - Full dates: `YYYY-MM-DD to YYYY-MM-DD`
    - Year ranges: `YYYY to YYYY`
    - Ongoing: `YYYY-MM-DD to present`
  - Fixed role extraction to recognize ORCID's `|` separator format (e.g., "Institut Pasteur | PhD Student")
  - Fixed degree extraction for education entries (MSc, BSc, PhD, etc.)
  - Enhanced duration calculation to handle full date formats with decimal precision (e.g., 3.2 years)
  - Fixed location parsing to eliminate double commas and clean formatting
  - All fields now reliably extracted: dates, roles, degrees, locations, durations
  - Validated with real ORCID profiles (e.g., 0000-0002-1126-1535)
- **Dependencies**: Added `httpx` for async HTTP requests in organization enrichment
- **Docker volume mounting** for persistent cache storage:
  - Support for mounting `./data` directory to `/app/data` in container
  - Environment variable `CACHE_DB_PATH` for custom cache database location
  - Enables cache persistence across container restarts
- **Environment-based log level configuration**:
  - Added `LOG_LEVEL` environment variable support (DEBUG, INFO, WARNING, ERROR)
  - Allows dynamic logging configuration without code changes
  - New `serve-dev-debug` justfile recipe for easy debug mode startup
  - Enhanced subprocess logging with full stderr/stdout output (no truncation)
- **Enhanced debugging capabilities** for repository processing:
  - Comprehensive debug logging for git clone operations with directory contents
  - Full error output from repo-to-text subprocess (complete tracebacks)
  - Directory existence checks and file listing for troubleshooting
  - Detailed diagnostics when no .txt files are found after repo-to-text
- **ORCID validation and normalization**:
  - Added `normalize_orcid_to_url()` function to convert ORCID IDs to standard URL format
  - ORCID validation now accepts both ID format (0000-0002-1234-5678) and URL format (https://orcid.org/0000-0002-1234-5678)
  - Automatic normalization to URL format before enrichment and scraping
  - Enhanced validation in both scraping flow and enrichment flow
- **Auto-enrichment flag** for conditional ORCID enrichment:
  - Added `auto_enrich_orcid` query parameter (default: `true`) to repository endpoints
  - Allows users to disable automatic ORCID enrichment when not needed
  - Reduces API calls and processing time for use cases that don't require affiliation data
- **GitHub API authentication** to avoid rate limits:
  - Added GitHub token authentication to `is_github_repo_public()` function
  - Uses `GITHUB_TOKEN` environment variable for authenticated requests
  - Increased rate limit from 60/hour (unauthenticated) to 5000/hour (authenticated)
  - Detailed rate limit logging for monitoring
- **Google Search integration** via Selenium for organization enrichment:
  - Replaced DuckDuckGo Instant Answer API with Selenium-based Google search
  - Extracts top 5 search results with title, link, and snippet
  - Reuses existing Selenium infrastructure (shared with ORCID scraping)
  - Comprehensive error handling with multiple CSS selector fallbacks
  - Improved search result quality and coverage for organization queries
  - Documentation in `docs/GOOGLE_SEARCH_IMPLEMENTATION.md`
- **Comprehensive logging** for organization enrichment:
  - Added detailed logging to all PydanticAI agent tools (search_ror, search_web, extract_domain_from_email)
  - Emoji indicators for visual scanning (🔍 calls, ✓ success, ✗ errors, 🤖 agent, 📍 results)
  - Logging for main enrichment functions (enrich_organizations, enrich_organizations_from_dict)
  - Enhanced observability into agent operations and decision-making
- **Unknown domain detection** with automatic search suggestions:
  - Enhanced `extract_domain_from_email` tool to detect unknown email domains
  - Automatically suggests ROR and web searches for organizations not in known domains dictionary
  - Updated system prompt to instruct agent to follow search suggestions
  - Improved organization discovery coverage beyond pre-configured domains
  - Known domains include: EPFL, ETH Zürich, Institut Pasteur, UNIL, Swiss Data Science Center
- **Enhanced colored logging with request tracking**:
  - ANSI color-coded logs with emojis for different log levels (🔵 DEBUG, ✅ INFO, ⚠️ WARNING, ❌ ERROR)
  - Request ID tracking across all async operations using AsyncRequestContext
  - Automatic request context via FastAPI middleware for all endpoints
  - Request IDs formatted with endpoint prefix (org-, user-, repo-, cache-) + worker PID + unique ID
  - Incoming request logging with 📥 emoji showing method, path, and query parameters
  - Response logging with 📤 emoji showing status code
  - All logs include request ID in brackets for easy correlation (e.g., [repo-8-6479])
- **User enrichment system** using PydanticAI for comprehensive author analysis:
  - Second-pass analysis to refine and enrich author/contributor information
  - PydanticAI agent with intelligent analysis of:
    - Git commit author data (names, emails, commit history)
    - ORCID profile data (affiliations, publications)
    - Email domain analysis for institutional connections
  - Enhanced author metadata with enriched affiliations and profile data
  - Available via `enrich_users=true` parameter on user and repository endpoints
  - Graceful error handling - errors don't break the main request
- **Complete enrichment coverage for all repository endpoints**:
  - `/v1/extract/json/{full_path:path}` now supports:
    - ✅ ORCID enrichment with `auto_enrich_orcid` parameter
    - ✅ Organization enrichment with `enrich_orgs` parameter
    - ✅ User enrichment with `enrich_users` parameter
  - `/v1/extract/json-ld/{full_path:path}` now supports:
    - ✅ ORCID enrichment with `auto_enrich_orcid` parameter
    - ✅ Organization enrichment with `enrich_orgs` parameter
    - ✅ User enrichment with `enrich_users` parameter
  - `/v1/repository/llm/json/{full_path:path}` now supports:
    - ✅ ORCID enrichment (existing)
    - ✅ Organization enrichment with `enrich_orgs` parameter (existing)
    - ✅ User enrichment with `enrich_users` parameter (new)
  - All three main repository endpoints now have consistent, comprehensive enrichment capabilities

### Changed
- **Project structure modernization**:
  - Removed monolithic `src/core/` directory in favor of feature-based modules
  - All imports updated from `.core.*` pattern to direct module imports (`.agents`, `.cache`, `.data_models`, etc.)
  - Improved separation of concerns with dedicated modules for each functional area
- API version updated to 2.0.0 across all endpoints
- **Upgraded pydantic-ai to version 1.0.15**:
  - Migrated from deprecated `result_type` parameter to new `output_type` parameter
  - Updated both organization enrichment and user enrichment agents
  - Changed all `result.data` references to `result.output` for compatibility with new API
  - Ensures compatibility with latest pydantic-ai features and improvements
- **Improved repo-to-text error handling** for more resilient repository processing:
  - Changed from strict failure on non-zero exit codes to lenient handling
  - Now continues processing if .txt files are created despite exit code 1
  - Handles cases where repo-to-text writes warnings to stderr but still succeeds
  - Prevents data loss from repositories that process successfully but return error codes
  - Added warning logs instead of immediate failure for better observability
- **Fixed token parameter handling** for OpenAI reasoning models:
  - o3-mini and o4-mini now correctly use `max_completion_tokens` instead of `max_tokens`
  - Standard models (gpt-4o-mini, gpt-5) continue to use `max_tokens`
  - Prevents token limit errors with reasoning models
- **Replaced DuckDuckGo with Google Search** for organization enrichment:
  - DuckDuckGo Instant Answer API was returning empty results for many queries
  - Google search via Selenium provides comprehensive, reliable results
  - No additional infrastructure needed (reuses existing Selenium instance)
- All data endpoints now support caching with `force_refresh` parameter
- Response format includes `cached` status indicator
- Author metadata now automatically enriched with ORCID affiliations
- Both `/v1/extract/json/` and `/v1/repository/llm/json/` endpoints include ORCID enrichment
- Selenium configuration now uses environment variable `SELENIUM_REMOTE_URL`
- Updated OpenAI Python SDK dependency to version 2.1.0 for better GPT-5 support
- Refactored `genai_model.py` to use consistent structured output handling across all models
- Enhanced logging to show model configuration and API call progress
- Fixed logger initialization order to prevent undefined variable errors
- Removed `cached` field from user and organization endpoint responses
- Updated response structure to match repository endpoint: `{"link": ..., "output": ...}`
- User and organization endpoints now return `relatedToOrganizationsROR` with full ROR metadata when `enrich_orgs=true`
- Improved consistency across all LLM-based endpoints

### Fixed

- **Removed duplicate validation summary printing** in verification module:
  - Validation issues now appear only once in logs (as ERROR/WARNING with request IDs)
  - Removed redundant formatted print statements from `summary()` method
  - Cleaner log output without duplicate validation summaries
- **Fixed 400 Bad Request error** for cached LLM results in `/v1/repository/llm/json`:
  - Added JSON parsing for cached responses that may be stored as JSON strings
  - Handles both dict and JSON string responses from cache
  - Prevents parsing errors when retrieving cached LLM data

### Documentation

- Added comprehensive cache documentation in `docs/CACHE_README.md`
- Updated API endpoint documentation with caching information
- Added cache configuration examples and environment variables reference
- Added ORCID affiliations documentation in `docs/ORCID_AFFILIATIONS.md`
- Created ORCID implementation summary with technical details


## [1.0.0] - 2025-08-06

### Added

- Users and Organization compatibility
- Endpoints refactoring
- Parallel calling
- Multiworkers entrypoint

## [0.1.0] - 2025-06-25

### Added

- Initial project setup.
- Dockerfile for containerization.
- GitHub Actions workflow for automated publishing and releases.
