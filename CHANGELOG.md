# Changelog

All notable changes to this project will be documented in this file.


## [2.0.0] - 2025-10-03

### Added
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
  - Cached ORCID data with 14-day TTL to avoid repeated scraping
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

### Changed
- API version updated to 2.0.0 across all endpoints
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
