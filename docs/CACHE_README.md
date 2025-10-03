# API Caching System

This document describes the caching system implemented to reduce external API calls to GitHub, ORCID, and GIMIE services.

## Overview

The caching system uses SQLite as the backend storage with configurable TTL (Time To Live) settings for different API types. This significantly reduces the number of external API requests while maintaining data freshness.

## Features

- **Automatic TTL expiration** - Default 30 days, configurable per API type
- **Force refresh capability** - Bypass cache when needed
- **Cache statistics and management** - Monitor cache performance
- **Thread-safe operations** - Safe for concurrent access
- **JSON storage** - Efficient storage of complex API responses
- **Automatic cleanup** - Remove expired entries automatically

## API Types and TTL Settings

| API Type | Default TTL | Description |
|----------|-------------|-------------|
| `github_user` | 7 days | GitHub user metadata |
| `github_org` | 7 days | GitHub organization metadata |
| `orcid` | 14 days | ORCID profile data |
| `gimie` | 1 day | GIMIE repository analysis |
| `llm` | 30 days | LLM-generated content |
| `llm_user` | 7 days | LLM-processed user data |
| `llm_org` | 7 days | LLM-processed org data |

## Usage

### Basic API Calls

All API endpoints now support a `force_refresh` query parameter:

```bash
# Use cached data (default)
GET /v1/extract/json/https://github.com/user/repo

# Force refresh from external APIs
GET /v1/extract/json/https://github.com/user/repo?force_refresh=true
```

### Cache Management Endpoints

```bash
# Get cache statistics
GET /v1/cache/stats

# Clean up expired entries
POST /v1/cache/cleanup

# Clear all cache
POST /v1/cache/clear

# Enable/disable caching
POST /v1/cache/enable
POST /v1/cache/disable

# Invalidate specific cache entries
DELETE /v1/cache/invalidate/github_user?username=example
```

## Configuration

### Environment Variables

| Variable | Default | Description |
|----------|---------|-------------|
| `CACHE_ENABLED` | `true` | Enable/disable caching |
| `CACHE_DEFAULT_TTL_DAYS` | `30` | Default TTL in days |
| `CACHE_DB_PATH` | `api_cache.db` | SQLite database path |
| `CACHE_GITHUB_USER_TTL_DAYS` | `7` | GitHub user TTL |
| `CACHE_GITHUB_ORG_TTL_DAYS` | `7` | GitHub org TTL |
| `CACHE_ORCID_TTL_DAYS` | `14` | ORCID TTL |
| `CACHE_GIMIE_TTL_DAYS` | `1` | GIMIE TTL |
| `CACHE_LLM_TTL_DAYS` | `30` | LLM TTL |
| `CACHE_AUTO_CLEANUP` | `true` | Enable automatic cleanup |
| `CACHE_CLEANUP_INTERVAL_HOURS` | `24` | Cleanup interval |
| `CACHE_MAX_SIZE_MB` | `1000` | Maximum cache size |
| `CACHE_MAX_ENTRIES_PER_API` | `10000` | Max entries per API type |

### Example Configuration

```bash
# Set custom TTL for GitHub data
export CACHE_GITHUB_USER_TTL_DAYS=14
export CACHE_GITHUB_ORG_TTL_DAYS=14

# Disable caching for development
export CACHE_ENABLED=false

# Use custom cache database location
export CACHE_DB_PATH=/tmp/api_cache.db
```

## Cache Statistics

The cache system provides detailed statistics:

```json
{
  "total_entries": 1250,
  "active_entries": 1100,
  "expired_entries": 150,
  "entries_by_type": {
    "github_user": 500,
    "github_org": 200,
    "orcid": 100,
    "gimie": 300,
    "llm": 0
  },
  "total_hits": 5000,
  "database_size_bytes": 52428800,
  "database_size_mb": 50.0,
  "config": {
    "cache_enabled": true,
    "default_ttl_days": 30,
    "api_ttl_overrides": {...}
  }
}
```

## Performance Benefits

- **Reduced API calls**: Up to 90% reduction in external API requests
- **Faster response times**: Cached responses are served instantly
- **Rate limit protection**: Avoid hitting GitHub/ORCID rate limits
- **Cost savings**: Reduced LLM API costs for repeated requests
- **Reliability**: Fallback to cached data when external APIs are down

## Cache Key Generation

Cache keys are generated using SHA-256 hashes of:
- API type
- Request parameters (sorted for consistency)

Example: `github_user:{"username": "example"}` → `a1b2c3d4...`

## Database Schema

```sql
CREATE TABLE cache_entries (
    cache_key TEXT PRIMARY KEY,
    api_type TEXT NOT NULL,
    request_params TEXT NOT NULL,
    response_data TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    expires_at TIMESTAMP NOT NULL,
    hit_count INTEGER DEFAULT 0,
    last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);
```

## Monitoring and Maintenance

### Regular Maintenance

1. **Monitor cache statistics** via `/v1/cache/stats`
2. **Clean up expired entries** via `/v1/cache/cleanup`
3. **Check database size** and clear if needed
4. **Review hit rates** and adjust TTL settings

### Troubleshooting

- **High memory usage**: Check database size, consider cleanup
- **Low hit rates**: Review TTL settings, check cache key generation
- **Stale data**: Use `force_refresh=true` parameter
- **Cache not working**: Check `CACHE_ENABLED` environment variable

## Migration from Non-Cached Version

The caching system is backward compatible. Existing API calls will work without changes, but will benefit from caching. To force refresh specific calls, add the `force_refresh=true` parameter.

## Future Enhancements

- **Redis backend** for distributed caching
- **Cache warming** strategies
- **Advanced invalidation** patterns
- **Cache compression** for large responses
- **Metrics and alerting** integration

