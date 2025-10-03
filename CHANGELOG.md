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

### Changed
- API version updated to 2.0.0 across all endpoints
- All data endpoints now support caching with `force_refresh` parameter
- Response format includes `cached` status indicator
- Author metadata now automatically enriched with ORCID affiliations
- Both `/v1/extract/json/` and `/v1/repository/llm/json/` endpoints include ORCID enrichment
- Selenium configuration now uses environment variable `SELENIUM_REMOTE_URL`

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
