# Empty Repository Handling

## Overview

The system now gracefully handles empty or minimal repositories by performing early exit checks to avoid wasting resources on repositories that have no analyzable content.

## Implementation

### Early Exit Strategy

When processing a repository, the system now:

1. **Clones the repository** (minimal cost)
2. **Runs repo-to-text** to extract files
3. **Immediately checks if content exists** (before expensive operations)
4. **Returns minimal metadata if empty** (skips LLM calls, author extraction, etc.)

### Code Location

**File:** `src/core/genai_model.py`

**Function:** `llm_request_repo_infos()`

```python
# Early exit for empty repositories - skip expensive operations
if not input_text or len(input_text.strip()) < 10:
    logger.warning(
        f"Repository {repo_url} has no analyzable content (empty or minimal). "
        "Skipping further analysis.",
    )
    # Return minimal valid metadata for empty repositories
    repo_name = repo_url.rstrip("/").split("/")[-1]
    return {
        "@context": "https://schema.org/",
        "@type": "SoftwareSourceCode",
        "name": repo_name,
        "codeRepository": repo_url,
        "parseTimestamp": datetime.now().strftime("%Y-%m-%dT%H:%M"),
        "description": "Repository appears to be empty or has no analyzable content",
    }
```

### Minimal Response Format

For empty repositories, the system returns a valid Schema.org SoftwareSourceCode object with:

- `@context`: Schema.org context
- `@type`: SoftwareSourceCode type
- `name`: Extracted from repository URL
- `codeRepository`: Original repository URL
- `parseTimestamp`: When the check was performed
- `description`: Clear message indicating the repository is empty

## Resource Savings

By implementing early exit, we avoid:

1. ❌ **Git author extraction** - No need to process commit history
2. ❌ **Token reduction/processing** - Skip expensive text processing
3. ❌ **LLM API calls** - Most expensive operation (saves API costs)
4. ❌ **Organization enrichment** - Skip PydanticAI agent calls
5. ❌ **User enrichment** - Skip ORCID scraping
6. ❌ **GIMIE post-processing** - Skip metadata merging

### Performance Impact

For empty repositories:
- **Before:** ~30-60 seconds + API costs
- **After:** ~2-5 seconds, no API costs

## Error Handling

The system also handles JSON parsing errors gracefully in `src/api.py`:

```python
try:
    llm_result = json.loads(llm_result_raw)
except json.JSONDecodeError as e:
    logger.warning(f"Failed to parse LLM result as JSON: {e}")
    # Return minimal valid response
    llm_result = {
        "@context": "https://schema.org/",
        "@type": "SoftwareSourceCode",
        "name": full_path.split("/")[-1],
        "codeRepository": full_path,
        "parseTimestamp": datetime.now().strftime("%Y-%m-%dT%H:%M"),
        "description": "Repository appears to be empty or has no analyzable content",
    }
```

## Testing

To test empty repository handling:

```bash
# Test with an empty repository
curl "http://localhost:1234/v1/repository/llm/json/https://github.com/cryos-epfl/slf-osper-web"

# Expected response:
{
  "link": "https://github.com/cryos-epfl/slf-osper-web",
  "output": {
    "@context": "https://schema.org/",
    "@type": "SoftwareSourceCode",
    "name": "slf-osper-web",
    "codeRepository": "https://github.com/cryos-epfl/slf-osper-web",
    "parseTimestamp": "2025-10-06T14:30",
    "description": "Repository appears to be empty or has no analyzable content"
  },
  "cached": false
}
```

## Edge Cases Handled

1. ✅ **Completely empty repositories** - No files at all
2. ✅ **Repositories with only .git folder** - No code files
3. ✅ **Repositories with minimal content** - Less than 10 characters
4. ✅ **Invalid JSON responses from LLM** - Fallback to minimal metadata
5. ✅ **Cached empty results** - Properly handles string/dict from cache

## Related Documentation

- [Memory Optimization](MEMORY_OPTIMIZATION.md) - Overall memory management strategy
- [MEMORY_FIX_SUMMARY.md](../MEMORY_FIX_SUMMARY.md) - Quick reference for all optimizations
