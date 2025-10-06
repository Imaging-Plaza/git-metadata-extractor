# Memory Optimization Guide

## Overview

This document explains the memory optimizations implemented to prevent workers from being killed due to OOM (Out of Memory) errors.

## Problem Identified

Workers were consuming up to 10GB of RAM and being killed with `SIGKILL` due to memory exhaustion. The main causes were:

1. **Selenium WebDriver instances**: Each browser instance uses 500MB-1GB of RAM
2. **Unlimited cache growth**: SQLite cache had no size limits
3. **Large token limits**: Loading entire repositories (800K tokens) into memory
4. **No connection pooling limits**: OpenAI client creating too many connections
5. **No resource cleanup**: Resources not being released between requests

## Optimizations Implemented

### 1. Reduced Concurrent Selenium Sessions

**Before**: Up to 3 concurrent browser instances (1.5-3GB RAM)
**After**: Maximum 1 concurrent browser instance (500MB-1GB RAM)

```python
# src/core/user_enrichment.py
# src/core/organization_enrichment.py
_MAX_SELENIUM_SESSIONS = int(os.getenv("MAX_SELENIUM_SESSIONS", "1"))
```

**Impact**: ~60-70% reduction in Selenium memory usage

### 2. Cache Size Limiting

**Before**: Unlimited cache growth
**After**: Automatic cleanup when exceeding 10,000 entries (configurable)

```python
# src/core/cache.py
self.max_cache_entries = int(os.getenv("MAX_CACHE_ENTRIES", "10000"))
```

Features:
- Removes expired entries first
- Removes least recently used (LRU) entries if still over limit
- Runs automatically on cache writes
- Runs on application startup

**Impact**: Prevents cache from consuming gigabytes of memory

### 3. Reduced Token Limits

**Before**: 800,000 tokens per request (~3-4GB for large repos)
**After**: 400,000 tokens per request (~1.5-2GB for large repos)

```python
# src/core/genai_model.py
def reduce_input_size(input_text, max_tokens=400000, repo_url=None):
```

**Impact**: ~50% reduction in memory for large repository processing

### 4. OpenAI Client Optimization

**Before**: No connection limits, no cleanup
**After**: Limited retries and proper async cleanup

```python
# src/core/genai_model.py
async_openai_client = AsyncOpenAI(
    api_key=api_key,
    timeout=600.0,
    max_retries=2,  # Reduced from default
)
```

Added cleanup function:
```python
async def cleanup_async_openai_client():
    """Cleanup the async OpenAI client to free resources."""
    global async_openai_client
    if async_openai_client is not None:
        await async_openai_client.close()
        async_openai_client = None
```

**Impact**: Prevents connection pool memory leaks

### 5. FastAPI Lifecycle Hooks

Added startup/shutdown events for resource management:

```python
# src/api.py
@app.on_event("shutdown")
async def shutdown_event():
    """Cleanup resources on application shutdown"""
    await cleanup_async_openai_client()
    gc.collect()
```

**Impact**: Ensures proper cleanup when workers restart

### 6. Gunicorn Worker Recycling

Created `gunicorn_conf.py` with worker recycling:

```python
max_requests = 1000  # Recycle workers after N requests
max_requests_jitter = 100  # Randomness to prevent simultaneous recycling
```

**Impact**: Prevents memory leaks from accumulating over time

### 7. Reduced Default Workers

**Before**: 4 workers in Docker
**After**: 2 workers in Docker (configurable via `WORKERS` env var)

```dockerfile
ENV WORKERS=2
```

**Impact**: 50% reduction in baseline memory usage

## Environment Variables

Configure memory settings via environment variables:

```bash
# Selenium concurrency (default: 1)
MAX_SELENIUM_SESSIONS=1

# Cache size limit (default: 10000 entries)
MAX_CACHE_ENTRIES=10000

# Number of gunicorn workers (default: 2)
WORKERS=2

# Worker recycling (default: 1000 requests)
MAX_REQUESTS=1000
MAX_REQUESTS_JITTER=100

# Cache database location
CACHE_DB_PATH=/app/data/api_cache.db
```

## Expected Memory Usage

### Per Worker (Idle)
- Base Python + FastAPI: ~100-150MB
- Cache (with 10K entries): ~50-100MB
- **Total Idle**: ~150-250MB per worker

### Per Worker (Active - Processing Request)
- Base: ~150-250MB
- OpenAI client: ~50-100MB
- Selenium (when active): ~500MB-1GB
- Repository data (400K tokens): ~1-2GB
- **Peak during processing**: ~2-3GB per worker

### Total Application (2 Workers)
- **Idle**: ~300-500MB
- **Light load**: ~1-2GB
- **Heavy load (with Selenium)**: ~4-6GB
- **Absolute peak (both workers with Selenium + large repos)**: ~6-8GB

## Monitoring

Check cache statistics:
```bash
curl http://localhost:1234/v1/cache/stats
```

Monitor worker memory:
```bash
ps aux | grep gunicorn
```

Force cache cleanup:
```bash
curl -X POST http://localhost:1234/v1/cache/cleanup
```

## Recommendations

### For Production

1. **Set memory limits per worker**: Use Docker `--memory` flag or Kubernetes limits
   ```bash
   docker run --memory=4g --memory-swap=4g ...
   ```

2. **Monitor and alert**: Set up alerts for >80% memory usage

3. **Adjust workers based on RAM**:
   - 4GB RAM: 1 worker
   - 8GB RAM: 2 workers (default)
   - 16GB RAM: 3-4 workers
   - 32GB RAM: 4-6 workers

4. **Enable cache cleanup cron**: Run hourly cleanup
   ```bash
   0 * * * * curl -X POST http://localhost:1234/v1/cache/cleanup
   ```

### For Development

1. **Use 1 worker**: Reduces memory consumption
   ```bash
   WORKERS=1 gunicorn src.api:app --config gunicorn_conf.py
   ```

2. **Reduce cache size**:
   ```bash
   MAX_CACHE_ENTRIES=1000
   ```

3. **Clear cache regularly**:
   ```bash
   curl -X POST http://localhost:1234/v1/cache/clear
   ```

## Troubleshooting

### Worker still being killed?

1. **Check actual memory usage**:
   ```bash
   docker stats
   ```

2. **Reduce workers further**:
   ```bash
   WORKERS=1
   ```

3. **Reduce cache size**:
   ```bash
   MAX_CACHE_ENTRIES=5000
   ```

4. **Reduce token limit** (edit `src/core/genai_model.py`):
   ```python
   def reduce_input_size(input_text, max_tokens=200000, repo_url=None):
   ```

5. **Disable ORCID enrichment** to avoid Selenium:
   ```bash
   curl "http://localhost:1234/v1/extract/json/owner/repo?auto_enrich_orcid=false"
   ```

### Memory leak suspected?

1. **Enable worker recycling** (if not already):
   ```bash
   MAX_REQUESTS=500
   ```

2. **Check cache growth**:
   ```bash
   curl http://localhost:1234/v1/cache/stats
   ```

3. **Manual garbage collection** (requires code change):
   ```python
   import gc
   gc.collect()
   ```

## Performance Impact

The memory optimizations have minimal performance impact:

- **Token reduction (800K→400K)**: Affects only very large repositories (>1000 files)
- **Selenium sessions (3→1)**: Serializes ORCID/web scraping (acceptable for most use cases)
- **Cache limits**: No impact (old entries are least useful)
- **Worker recycling**: Transparent to users

## Future Improvements

1. **Streaming for large repositories**: Process files in chunks instead of loading all
2. **Redis cache**: Move cache to Redis for better memory management
3. **Celery workers**: Offload heavy tasks to separate worker pool
4. **Progressive enrichment**: Make enrichment truly optional and on-demand
5. **WebDriver pool**: Reuse browser instances instead of creating new ones

## References

- Gunicorn documentation: https://docs.gunicorn.org/
- FastAPI lifecycle: https://fastapi.tiangolo.com/advanced/events/
- Python garbage collection: https://docs.python.org/3/library/gc.html
