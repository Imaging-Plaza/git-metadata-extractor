# Cache Usage Analysis - LLM/JSON Endpoints

## Executive Summary

🚨 **CRITICAL ISSUES FOUND** - The cache is **NOT working properly** for enriched requests!

## Problems Identified

### 1. ❌ Cache Keys Don't Include Enrichment Flags

**Current Behavior:**
```python
cache_params = {
    "full_path": full_path,
    "output_format": "json",
    "max_tokens": 20000,
}
```

**Problem:**
The cache key is the same whether you request:
- `/v1/repository/llm/json/repo`
- `/v1/repository/llm/json/repo?enrich_orgs=true`
- `/v1/repository/llm/json/repo?enrich_users=true`
- `/v1/repository/llm/json/repo?enrich_orgs=true&enrich_users=true`

**Impact:**
- ⚠️ **Cache Poisoning**: First request without enrichment caches basic result
- ⚠️ **Wrong Data**: Subsequent enriched requests get basic result from cache
- ⚠️ **Wasted Compute**: Enrichment runs but result never gets cached
- ⚠️ **Inconsistent Results**: Same URL returns different data depending on cache state

### Example Failure Scenario

```bash
# User 1: Request without enrichment
curl "http://localhost:1234/v1/repository/llm/json/https://github.com/user/repo"
# Response: Basic metadata (cached as "llm:repo:json:20000")

# User 2: Request WITH enrichment (same cache key!)
curl "http://localhost:1234/v1/repository/llm/json/https://github.com/user/repo?enrich_orgs=true"
# Response: Basic metadata from cache (WRONG! Should have org enrichment)
# The system runs enrichment but never caches the enriched result

# User 2: Request again with force_refresh
curl "http://localhost:1234/v1/repository/llm/json/https://github.com/user/repo?enrich_orgs=true&force_refresh=true"
# Response: Full enrichment (correct, but expensive - shouldn't need force_refresh)
```

### 2. ⚠️ LLM Base Data Gets Cached, Enrichment Does Not

**Current Flow:**
```python
# Step 1: Get LLM base data (CACHED ✅)
llm_result_raw = await cache_manager.get_cached_or_fetch_async(
    api_type="llm",
    params=cache_params,  # Missing enrich flags!
    fetch_func=fetch_llm_data,
)

# Step 2: Run ORCID enrichment (NOT CACHED ❌)
llm_result = enrich_authors_with_orcid(llm_result, force_refresh=force_refresh)

# Step 3: Run org enrichment if requested (NOT CACHED ❌)
if enrich_orgs:
    organization_enrichment = await enrich_organizations_from_dict(...)
    # Merge into llm_result

# Step 4: Run user enrichment if requested (NOT CACHED ❌)
if enrich_users:
    user_enrichment = await enrich_users_from_dict(...)
    # Merge into llm_result

# Return combined result (ONLY BASE LLM PART IS CACHED!)
return {"link": full_path, "output": llm_result}
```

**Problem:**
- Only the base LLM call is cached
- ORCID enrichment runs every time (web scraping!)
- Organization enrichment runs every time (PydanticAI + Selenium!)
- User enrichment runs every time (PydanticAI + Selenium!)

### 3. ⚠️ GIMIE Data Gets Different Cache Treatment

**GIMIE caching** (works correctly):
```python
jsonld_gimie_data = cache_manager.get_cached_or_fetch(
    api_type="gimie",
    params={"full_path": full_path, "format": "json-ld"},
    fetch_func=fetch_gimie_data,
    force_refresh=force_refresh,
)
```

**LLM caching** (missing enrichment params):
```python
cache_params = {
    "full_path": full_path,
    "output_format": "json",
    "max_tokens": 20000,
    # MISSING: enrich_orgs, enrich_users!
}
```

## Performance Impact

### Without Proper Caching

**Per Request with `enrich_orgs=true&enrich_users=true`:**
- ✅ LLM base call: **CACHED** (~30s + API cost saved)
- ❌ ORCID enrichment: **NOT CACHED** (~5-10s per author + web scraping)
- ❌ Org enrichment: **NOT CACHED** (~10-20s + Selenium + PydanticAI)
- ❌ User enrichment: **NOT CACHED** (~10-20s + Selenium + PydanticAI)

**Total waste per cached request:** 25-50 seconds + expensive operations!

### With Proper Caching

**Per Request with `enrich_orgs=true&enrich_users=true`:**
- ✅ Full enriched result: **CACHED** (~0.1s)

**Savings:** 25-50 seconds + all expensive operations!

## Recommendations

### Priority 1: Fix Cache Keys (CRITICAL)

Include enrichment flags in cache parameters:

```python
cache_params = {
    "full_path": full_path,
    "output_format": "json",
    "max_tokens": 20000,
    "enrich_orgs": enrich_orgs,      # ADD THIS
    "enrich_users": enrich_users,    # ADD THIS
}
```

This ensures:
- Different cache entries for different enrichment combinations
- Cached enriched results are returned correctly
- No cache poisoning

### Priority 2: Cache the Final Enriched Result

**Option A: Cache After All Enrichment (Recommended)**

Move the cache logic to cache the final, fully-enriched result:

```python
# Check cache first with all params
cache_params = {
    "full_path": full_path,
    "output_format": "json",
    "max_tokens": 20000,
    "enrich_orgs": enrich_orgs,
    "enrich_users": enrich_users,
}

async def fetch_and_enrich():
    # Get GIMIE
    gimie_data = ...

    # Get LLM base
    llm_result = await llm_request_repo_infos(...)

    # Enrich ORCID
    llm_result = enrich_authors_with_orcid(...)

    # Enrich orgs if requested
    if enrich_orgs:
        org_enrichment = await enrich_organizations_from_dict(...)
        # merge...

    # Enrich users if requested
    if enrich_users:
        user_enrichment = await enrich_users_from_dict(...)
        # merge...

    return llm_result

# Get fully enriched result from cache or fetch
final_result = await cache_manager.get_cached_or_fetch_async(
    api_type="llm_enriched",  # Different api_type!
    params=cache_params,
    fetch_func=fetch_and_enrich,
    force_refresh=force_refresh,
)
```

**Option B: Separate Cache Layers (More Complex)**

Keep separate cache entries for:
1. LLM base result
2. ORCID enrichment per repo
3. Org enrichment per repo
4. User enrichment per repo

This allows reusing partial results but adds complexity.

### Priority 3: Add Cache Invalidation for Enrichment

When enrichment flags are different, we need different cache entries:

```python
# These should all have DIFFERENT cache keys:
GET /v1/repository/llm/json/repo
GET /v1/repository/llm/json/repo?enrich_orgs=true
GET /v1/repository/llm/json/repo?enrich_users=true
GET /v1/repository/llm/json/repo?enrich_orgs=true&enrich_users=true
```

## Testing Cache Behavior

### Test 1: Basic Caching
```bash
# First call (should fetch)
time curl "http://localhost:1234/v1/repository/llm/json/https://github.com/user/repo"

# Second call (should be instant from cache)
time curl "http://localhost:1234/v1/repository/llm/json/https://github.com/user/repo"
```

### Test 2: Enrichment Caching (CURRENTLY BROKEN)
```bash
# Call with enrichment (should fetch + enrich)
time curl "http://localhost:1234/v1/repository/llm/json/https://github.com/user/repo?enrich_orgs=true"

# Same call again (should be instant from cache, but currently re-runs enrichment!)
time curl "http://localhost:1234/v1/repository/llm/json/https://github.com/user/repo?enrich_orgs=true"
```

### Test 3: Cache Poisoning (CURRENTLY BROKEN)
```bash
# Step 1: Basic request (caches basic result)
curl "http://localhost:1234/v1/repository/llm/json/https://github.com/user/repo"

# Step 2: Enriched request (should return enriched, but returns basic from cache!)
curl "http://localhost:1234/v1/repository/llm/json/https://github.com/user/repo?enrich_orgs=true"
# BUG: Returns basic result without orgs
```

## Related Files

- `src/api.py` - LLM JSON endpoint (lines 1030-1230)
- `src/core/cache_manager.py` - Cache management logic
- `src/core/cache.py` - SQLite cache implementation

## Estimated Impact

**Without Fix:**
- ❌ Cache hit rate for enriched requests: **0%**
- ❌ Wasted compute per enriched request: **25-50 seconds**
- ❌ Unnecessary Selenium sessions: **1-2 per request**
- ❌ Unnecessary API calls: **Multiple per request**

**With Fix:**
- ✅ Cache hit rate for enriched requests: **~90%** (same as base)
- ✅ Response time for cached enriched: **~0.1s** (vs 30-50s)
- ✅ Selenium sessions saved: **All of them**
- ✅ API calls saved: **All of them**

**Memory Impact:**
- Each full enriched result: ~50-100KB
- 10,000 cached entries: ~500MB-1GB (acceptable)
- Current memory waste from re-computation: **Much higher**

## Next Steps

1. **Immediate**: Add `enrich_orgs` and `enrich_users` to cache_params
2. **Short-term**: Restructure to cache final enriched result
3. **Medium-term**: Add cache monitoring and stats for enrichment
4. **Long-term**: Consider partial caching strategy for large repos
