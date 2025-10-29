# Recent Updates Summary

## Date: October 29, 2025

### 1. Cache Configuration Changes ✅

**File:** `src/cache/cache_config.py`

**Changes:**
- **All cache TTLs increased from short durations to 365 days** (essentially permanent storage)
- Cache only refreshes when explicitly using `force_refresh=true`

**Before:**
```python
"gimie": 1 day          # Was expiring too quickly!
"llm": 30 days
"github_user": 7 days
"github_org": 7 days
"orcid": 14 days
"llm_user": 7 days
"llm_org": 7 days
```

**After:**
```python
"gimie": 365 days       # ✅ Essentially permanent
"llm": 365 days         # ✅ Essentially permanent
"github_user": 365 days
"github_org": 365 days
"orcid": 365 days
"llm_user": 365 days
"llm_org": 365 days
```

**Benefits:**
- Cache persists across restarts
- No unexpected cache expiration
- Reduces API calls significantly
- Only refreshes when you explicitly request it

---

### 2. Infoscience API Integration ✅

**New Files:**
- `src/data_models/infoscience.py` - Pydantic models for Infoscience entities
- `src/context/infoscience.py` - API client and PydanticAI tool functions
- `INFOSCIENCE_INTEGRATION.md` - Comprehensive integration documentation

**Modified Files:**
- `src/agents/repository.py` - Registered Infoscience tools
- `src/agents/user.py` - Registered author search tools
- `src/agents/organization_enrichment.py` - Registered lab/publication tools
- `src/agents/repository_prompts.py` - Updated with tool usage guidelines
- `src/agents/prompts.py` - Updated user agent prompts
- `src/agents/organization_prompts.py` - Updated org agent prompts
- `src/context/__init__.py` - Exported Infoscience tools
- `src/data_models/__init__.py` - Exported Infoscience models

#### New Tool Functions

**1. `search_infoscience_publications_tool(query: str, max_results: int = 10)`**
- Search publications by title, DOI, keywords
- Returns markdown-formatted results
- In-memory caching to prevent duplicate searches

**2. `search_infoscience_authors_tool(name: str, max_results: int = 10)`**
- Search for EPFL authors/researchers
- Returns author profiles with publications count

**3. `search_infoscience_labs_tool(name: str, max_results: int = 10)`**
- Search for labs/organizational units
- Returns community/collection information

**4. `get_author_publications_tool(author_name: str, max_results: int = 10)`**
- Get all publications by a specific author
- Includes metadata (DOI, date, abstract)

#### Features Implemented

✅ **API Integration**
- Base URL: `https://infoscience.epfl.ch/server/api`
- DSpace 7.6 API compatible
- Async HTTP with `httpx`
- Optional authentication via `INFOSCIENCE_TOKEN`

✅ **In-Memory Caching**
- Prevents duplicate API calls within a session
- Caches both successful results and empty results
- Automatic cache key generation

✅ **Pydantic Models**
- `InfosciencePublication` - Publication metadata with DOI, authors, abstract
- `InfoscienceAuthor` - Author profiles with affiliations
- `InfoscienceLab` - Lab/organizational unit metadata
- `InfoscienceSearchResult` - Wrapper with pagination info
- All models include `to_markdown()` methods

✅ **Strategic Tool Usage**
- Agents instructed to search for repository/tool name FIRST
- ONE search per subject to avoid repetition
- Maximum 2 attempts per subject
- Accept when information is not found

✅ **Error Handling**
- Graceful handling of HTTP errors (404, timeouts)
- Informative error messages in markdown format
- Comprehensive logging with debug/info/error levels

#### Agent Integration

**Repository Agent:**
- Searches for publications about the repository/tool itself
- Example: Repository "gimie" → searches "gimie" in Infoscience

**User Enrichment Agent:**
- Searches for authors by name
- Gets their publication lists from Infoscience

**Organization Enrichment Agent:**
- Searches for labs/organizational units
- Finds affiliated publications
- Can search by repository name to find related research

---

### 3. Documentation Updates ✅

**Updated Files:**
- `.cursor/rules/ai-agents.mdc` - Added Infoscience tools section
- `.cursor/rules/project-architecture.mdc` - Added Infoscience integration details

#### Changes in `ai-agents.mdc`

**Added Section: "Infoscience Tools"**
- Complete tool function documentation
- Usage guidelines and strategic patterns
- Integration details for each agent type
- Caching behavior explanation

**Updated Section: "Data Sources"**
- Added Infoscience as a primary data source
- Documented API endpoints and authentication

#### Changes in `project-architecture.mdc`

**Updated Directory Structure:**
- Added `context/infoscience.py` reference
- Added `data_models/infoscience.py` reference

**New Module Documentation:**
- `context/` module purpose and patterns
- Infoscience integration architecture

**Updated External Services:**
- Added Infoscience API details
- Documented DSpace 7.6 endpoints
- Added authentication requirements

**Updated Environment Variables:**
- Added `INFOSCIENCE_TOKEN` (optional)
- Added all cache TTL configuration options
- Documented 365-day default TTL

**Updated Cache Configuration:**
- Detailed TTL settings for all cache types
- Explained permanent storage behavior
- Documented `force_refresh` behavior

---

## Environment Variables Reference

### New/Updated Variables

```bash
# Infoscience API (Optional)
INFOSCIENCE_TOKEN=your_token_here

# Cache TTL Configuration (All default to 365 days)
CACHE_DEFAULT_TTL_DAYS=365
CACHE_GIMIE_TTL_DAYS=365
CACHE_LLM_TTL_DAYS=365
CACHE_GITHUB_USER_TTL_DAYS=365
CACHE_GITHUB_ORG_TTL_DAYS=365
CACHE_ORCID_TTL_DAYS=365
CACHE_LLM_USER_TTL_DAYS=365
CACHE_LLM_ORG_TTL_DAYS=365
```

---

## Testing the Changes

### Test Cache TTL Changes
```bash
# Run API request
curl "http://localhost:1234/v1/repository/llm/json/https%3A//github.com/user/repo"

# Check logs - should show "expires in 365 days"
# Second request should use cached data
```

### Test Infoscience Tools
```bash
# Run analysis with org enrichment (uses Infoscience tools)
curl "http://localhost:1234/v1/repository/llm/json/https%3A//github.com/sdsc-ordes/gimie?enrich_orgs=true"

# Check logs for:
# - "🔍 Agent tool called: search_infoscience_publications_tool"
# - "⚡ Returning cached result" (on second call)
```

---

## Benefits Summary

### Cache Changes
✅ Cache persists essentially forever (365 days)  
✅ Significantly reduced API calls  
✅ Faster response times on repeated requests  
✅ Only refreshes when explicitly requested  

### Infoscience Integration
✅ Rich EPFL research context for repositories  
✅ Author publication history integration  
✅ Lab/organization affiliation data  
✅ Strategic tool usage prevents excessive API calls  
✅ In-memory caching for efficient agent behavior  

### Documentation
✅ Comprehensive rule files for future reference  
✅ Clear integration patterns documented  
✅ Environment variable reference updated  
✅ Tool usage guidelines for AI agents  

---

## Next Steps (Optional)

1. **Set Infoscience Token** (if needed for protected endpoints):
   ```bash
   export INFOSCIENCE_TOKEN=your_token_here
   ```

2. **Monitor Agent Behavior**:
   - Check logs for tool usage patterns
   - Verify caching is working (look for "⚡ Returning cached result")
   - Ensure agents don't make repetitive searches

3. **Adjust Cache TTL** (if needed):
   - Default 365 days should work for most cases
   - Can increase to 3650 days (10 years) if desired
   - Or set per-API-type using environment variables

4. **Review Infoscience Results**:
   - Check quality of publication searches
   - Verify author/lab searches return relevant data
   - Monitor API response times and errors

---

**All updates completed successfully!** 🎉

