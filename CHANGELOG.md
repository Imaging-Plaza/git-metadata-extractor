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

### Changed
- API version updated to 2.0.0 across all endpoints
- All data endpoints now support caching with `force_refresh` parameter
- Response format includes `cached` status indicator

### Documentation
- Added comprehensive cache documentation in `docs/CACHE_README.md`
- Updated API endpoint documentation with caching information
- Added cache configuration examples and environment variables reference


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