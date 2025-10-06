# Cache Fix Implementation Plan

## Problem Summary

Currently, the `/v1/repository/llm/json` endpoint:
1. ❌ Caches only the base LLM result
2. ❌ Runs ORCID, org, and user enrichments EVERY request
3. ❌ Different enrichment combinations return from same cache entry (cache poisoning)

## Proposed Solution

Restructure the endpoint to cache the **fully enriched result** with enrichment flags in the cache key.

## Implementation Steps

### Step 1: Restructure Cache Logic

**Current Flow:**
```python
# Cache base LLM result
llm_result = cache_manager.get(api_type="llm", ...)

# Run enrichments (NOT CACHED!)
llm_result = enrich_orcid(llm_result)
if enrich_orgs:
    org_data = enrich_orgs(llm_result)
if enrich_users:
    user_data = enrich_users(llm_result)

return llm_result
```

**New Flow:**
```python
async def fetch_and_enrich_all():
    # Get base LLM result
    llm_result = await llm_request_repo_infos(...)

    # Run all enrichments
    llm_result = enrich_orcid(llm_result)
    if enrich_orgs:
        org_data = await enrich_organizations(...)
        llm_result.update(org_data)
    if enrich_users:
        user_data = await enrich_users(...)
        llm_result.update(user_data)

    return llm_result

# Cache the FULL enriched result
cache_params = {
    "full_path": full_path,
    "enrich_orgs": enrich_orgs,  # Include in key!
    "enrich_users": enrich_users,  # Include in key!
}
llm_result = cache_manager.get(
    api_type="llm_enriched",
    params=cache_params,
    fetch_func=fetch_and_enrich_all,
)
```

### Step 2: Update Cache Keys

**Before:**
- `llm:{"full_path": "repo", "output_format": "json"}` - Always the same

**After:**
- `llm_enriched:{"full_path": "repo", "enrich_orgs": false, "enrich_users": false}` - Basic
- `llm_enriched:{"full_path": "repo", "enrich_orgs": true, "enrich_users": false}` - With orgs
- `llm_enriched:{"full_path": "repo", "enrich_orgs": false, "enrich_users": true}` - With users
- `llm_enriched:{"full_path": "repo", "enrich_orgs": true, "enrich_users": true}` - Full enrichment

### Step 3: Migration Strategy

**Option A: Clean Break (Recommended)**
- Use new api_type `"llm_enriched"` instead of `"llm"`
- Old cache entries naturally expire (30 days TTL)
- No cache migration needed

**Option B: Invalidate Old Cache**
- Add script to clear old `"llm"` entries
- Switch to `"llm_enriched"`

### Step 4: Code Changes Required

**File: `src/api.py`**

Lines to modify: ~1095-1240 (llm_json endpoint)

```python
@app.get("/v1/repository/llm/json/{full_path:path}", tags=["Repository"])
async def llm_json(
    full_path: str,
    force_refresh: bool = False,
    enrich_orgs: bool = False,
    enrich_users: bool = False,
):
    """..."""

    cache_manager = get_cache_manager()

    # Get GIMIE data (still cached separately with shorter TTL)
    def fetch_gimie_data():
        return extract_gimie(full_path, format="json-ld")

    jsonld_gimie_data = cache_manager.get_cached_or_fetch(
        api_type="gimie",
        params={"full_path": full_path, "format": "json-ld"},
        fetch_func=fetch_gimie_data,
        force_refresh=force_refresh,
    )

    # Define function to fetch and enrich ALL data
    async def fetch_and_enrich_all():
        """Fetch LLM data and perform all requested enrichments."""
        # Get base LLM data
        llm_data = await llm_request_repo_infos(
            str(full_path),
            gimie_output=jsonld_gimie_data,
            output_format="json",
            max_tokens=20000,
        )

        # Handle JSON parsing
        if isinstance(llm_data, str):
            try:
                llm_result = json.loads(llm_data)
            except json.JSONDecodeError:
                llm_result = {
                    "@context": "https://schema.org/",
                    "@type": "SoftwareSourceCode",
                    "name": full_path.split("/")[-1],
                    "codeRepository": full_path,
                    "description": "Empty or invalid repository",
                }
        else:
            llm_result = llm_data.copy() if isinstance(llm_data, dict) else {}

        # Add timestamp
        llm_result["parseTimestamp"] = datetime.now().strftime("%Y-%m-%dT%H:%M")

        # ORCID enrichment (always runs)
        llm_result = enrich_authors_with_orcid(llm_result, force_refresh=force_refresh)

        # Organization enrichment (conditional)
        if enrich_orgs:
            try:
                org_enrichment = await enrich_organizations_from_dict(llm_result, full_path)

                enriched_orgs = org_enrichment.get("organizations", [])
                if enriched_orgs:
                    llm_result["relatedToOrganizations"] = [
                        org.get("legalName") for org in enriched_orgs if org.get("legalName")
                    ]
                    llm_result["relatedToOrganizationsROR"] = enriched_orgs

                llm_result["relatedToEPFL"] = org_enrichment.get("relatedToEPFL", llm_result.get("relatedToEPFL"))
                llm_result["relatedToEPFLJustification"] = org_enrichment.get("relatedToEPFLJustification", llm_result.get("relatedToEPFLJustification"))
            except Exception as e:
                logger.error(f"Org enrichment error: {e}")

        # User enrichment (conditional)
        if enrich_users:
            try:
                git_authors_data = llm_result.get("gitAuthors", [])
                existing_authors_data = llm_result.get("author", [])

                user_enrichment = await enrich_users_from_dict(
                    git_authors_data=git_authors_data,
                    existing_authors_data=existing_authors_data,
                    repository_url=full_path,
                )

                llm_result["enrichedAuthors"] = user_enrichment.get("enrichedAuthors", [])
                llm_result["authorEnrichmentSummary"] = user_enrichment.get("summary", "")
            except Exception as e:
                logger.error(f"User enrichment error: {e}")

        return llm_result

    # Cache the FULLY ENRICHED result
    cache_params = {
        "full_path": full_path,
        "enrich_orgs": enrich_orgs,
        "enrich_users": enrich_users,
    }

    try:
        llm_result = await cache_manager.get_cached_or_fetch_async(
            api_type="llm_enriched",  # New api_type!
            params=cache_params,
            fetch_func=fetch_and_enrich_all,
            force_refresh=force_refresh,
        )
    except Exception as e:
        raise HTTPException(status_code=424, detail=f"Error from LLM service: {e}")

    return {"link": full_path, "output": llm_result}
```

## Testing Plan

### Test 1: Basic Caching
```bash
# Clear cache
curl -X POST http://localhost:1234/v1/cache/cleanup

# First request (cold - should take ~30s)
time curl "http://localhost:1234/v1/repository/llm/json/https://github.com/user/repo"

# Second request (warm - should take <1s)
time curl "http://localhost:1234/v1/repository/llm/json/https://github.com/user/repo"
```

### Test 2: Enrichment Caching
```bash
# Request with org enrichment (cold - should take ~40s)
time curl "http://localhost:1234/v1/repository/llm/json/https://github.com/user/repo?enrich_orgs=true"

# Same request (warm - should take <1s!)
time curl "http://localhost:1234/v1/repository/llm/json/https://github.com/user/repo?enrich_orgs=true"
```

### Test 3: Cache Isolation
```bash
# Request without enrichment
curl "http://localhost:1234/v1/repository/llm/json/https://github.com/user/repo"

# Request with enrichment (should NOT return cached basic result)
curl "http://localhost:1234/v1/repository/llm/json/https://github.com/user/repo?enrich_orgs=true"
# Should have org data!
```

### Test 4: Cache Stats
```bash
# Check cache entries
curl "http://localhost:1234/v1/cache/stats"
# Should show separate entries for each enrichment combination
```

## Performance Impact

**Before Fix:**
- Cold request (no enrichment): 30s
- Warm request (no enrichment): 0.1s ✅
- Cold request (with enrichment): 60s
- Warm request (with enrichment): **30s** ❌ (re-runs enrichment!)

**After Fix:**
- Cold request (no enrichment): 30s
- Warm request (no enrichment): 0.1s ✅
- Cold request (with enrichment): 60s
- Warm request (with enrichment): **0.1s** ✅ (cached!)

**Savings:** 30-50 seconds per cached enriched request!

## Rollback Plan

If issues arise:
1. Revert `api.py` changes
2. Cache will work as before (basic LLM only)
3. Old cache entries (`"llm"`) still valid
4. New entries (`"llm_enriched"`) ignored

## Migration Impact

- **Database**: No schema changes needed
- **API**: No breaking changes (same endpoints/params)
- **Cache**: Old entries naturally expire, new entries use new key format
- **Memory**: Slight increase (caching enriched results), but within limits (see MEMORY_OPTIMIZATION.md)

##Success Criteria

1. ✅ Enriched requests cache properly
2. ✅ Different enrichment combinations have different cache entries
3. ✅ No cache poisoning (basic vs enriched)
4. ✅ Response times for warm enriched requests < 1s
5. ✅ Memory usage stays under 6GB peak
