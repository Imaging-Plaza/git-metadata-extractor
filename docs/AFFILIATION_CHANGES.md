# Enhanced Affiliation Tracking - Implementation Summary

## Overview
Replaced simple string-based `affiliations: List[str]` with structured `affiliations: List[Affiliation]` throughout the codebase to track organization identifiers and data provenance.

## Breaking Changes ⚠️

This is a **breaking change**. API responses and cached data have changed format:

### Before (Old Format)
```json
{
  "affiliations": ["EPFL", "Swiss Data Science Center", "Hackuarium"]
}
```

### After (New Format)
```json
{
  "affiliations": [
    {
      "name": "EPFL",
      "organizationId": "https://ror.org/02s376052",
      "source": "orcid"
    },
    {
      "name": "Swiss Data Science Center",
      "organizationId": "SwissDataScienceCenter",
      "source": "github_profile"
    },
    {
      "name": "Hackuarium",
      "organizationId": null,
      "source": "agent_user_enrichment"
    }
  ]
}
```

## New Data Model

### Affiliation Model
Location: `src/data_models/models.py`

```python
class Affiliation(BaseModel):
    """Structured affiliation with provenance tracking"""

    name: str = Field(
        description="Organization name (e.g., 'Swiss Data Science Center', 'EPFL')"
    )
    organizationId: Optional[str] = Field(
        default=None,
        description="Organization identifier: ROR ID, GitHub handle, or internal ID"
    )
    source: str = Field(
        description="Data source: 'gimie', 'orcid', 'agent_org_enrichment', 'agent_user_enrichment', 'github_profile', 'email_domain'"
    )
```

### Source Types
- `orcid` - From ORCID employment records
- `github_profile` - From GitHub organization memberships
- `email_domain` - Inferred from email domains (@epfl.ch, etc.)
- `agent_user_enrichment` - From user enrichment AI agent
- `agent_org_enrichment` - From organization enrichment AI agent
- `gimie` - From GIMIE repository metadata

### Organization ID Types
- **ROR ID**: Full URL format (e.g., `https://ror.org/02s376052`)
- **GitHub Handle**: Organization handle (e.g., `SwissDataScienceCenter`)
- **Internal ID**: Any internal identifier from source systems
- **null**: When no identifier is available

## Files Modified

### 1. Core Data Models
- ✅ `src/data_models/models.py` - Added Affiliation model, updated Person.affiliations
- ✅ `src/data_models/user.py` - Updated EnrichedAuthor.affiliations
- ✅ `src/data_models/__init__.py` - Exported Affiliation model

### 2. Utilities
- ✅ `src/utils/utils.py`
  - Updated `get_orcid_affiliations()` to return `List[Affiliation]`
  - Updated `enrich_author_with_orcid()` to handle Affiliation objects
  - Merging now uses name-based deduplication

### 3. Repository Analysis
- ✅ `src/analysis/repositories.py`
  - Updated GIMIE affiliation extraction to create Affiliation objects
  - Updated affiliation merging logic in `_convert_simplified_to_full()`
  - Handles dict and Affiliation object formats

### 4. Agent Prompts
- ✅ `src/agents/user_prompts.py`
  - Updated system prompt to explain Affiliation structure
  - Formatted affiliation display in prompts as structured objects
- ✅ `src/agents/organization_prompts.py`
  - Updated affiliation display for ORCID authors (2 locations)
  - Shows name, organizationId, and source in prompts
- ✅ `src/agents/organization_enrichment.py`
  - Updated `_pre_search_ror_for_organizations()` to handle Affiliation objects
  - Handles dict, object, and legacy string formats

### 5. JSON-LD Conversion
- ✅ `src/data_models/conversion.py`
  - Added Affiliation to `PYDANTIC_TO_ZOD_MAPPING`
  - Added Affiliation to type_mapping
  - Mapped fields: name → schema:name, organizationId → schema:identifier, source → imag:source

### 6. Simplified Models
- ✅ `src/data_models/repository.py`
  - Updated `to_simplified_schema()` to extract names from Affiliation objects
  - Converts Affiliation objects to simple strings for atomic agents

## Benefits

### 1. Provenance Tracking
Now you can see exactly where each affiliation came from:
```python
for aff in person.affiliations:
    print(f"{aff.name} - Source: {aff.source}")
```

### 2. Organization Linking
Can track organization identifiers (ROR, GitHub handles):
```python
epfl_affs = [aff for aff in person.affiliations if aff.organizationId == "https://ror.org/02s376052"]
```

### 3. Common Organization Detection
Can now identify when authors share organizations:
```python
# Find all authors affiliated with SwissCat+
swisscat_authors = []
for author in repository.author:
    for aff in author.affiliations:
        if "SwissCat" in aff.name or aff.organizationId == "SwissCat+":
            swisscat_authors.append(author)
```

### 4. Multi-Source Enrichment
Same organization from multiple sources is properly tracked:
```python
# EPFL from ORCID
Affiliation(name="EPFL", organizationId="https://ror.org/02s376052", source="orcid")
# EPFL from email
Affiliation(name="EPFL", organizationId=None, source="email_domain")
```

### 5. Deduplication
Smart merging prevents duplicates based on organization name (case-insensitive):
```python
existing_names = {aff.name.lower(): aff for aff in person.affiliations}
# Only adds if name doesn't already exist
```

## Migration Notes

### Cache Impact
- **All cached data will be in old format** (List[str])
- **New analysis will return new format** (List[Affiliation])
- Recommendation: Clear cache after deployment or add version check

### API Consumers
API consumers will need to update to handle the new structure:

**Old code:**
```python
affiliations = person["affiliations"]  # List of strings
print(affiliations[0])  # "EPFL"
```

**New code:**
```python
affiliations = person["affiliations"]  # List of Affiliation objects
print(affiliations[0]["name"])  # "EPFL"
print(affiliations[0]["organizationId"])  # "https://ror.org/02s376052"
print(affiliations[0]["source"])  # "orcid"
```

### Backward Compatibility
**None.** This is an intentional breaking change for better data quality.

## Testing

To test the implementation with a real repository:

```bash
# Test with Carlos Vivar Rios' profile
curl "http://0.0.0.0:1234/v1/user/llm/json/github.com/caviri?force_refresh=true"

# Look for the affiliations field in the response
# Each affiliation should have: name, organizationId, source
```

Expected result:
- Affiliations will be objects with provenance information
- GitHub organizations will have their handles as organizationId
- ORCID affiliations will have ROR IDs (when available)
- Source field will indicate where each affiliation came from

## Future Enhancements

Potential improvements:
- [ ] Add confidence scores to Affiliation model
- [ ] Add temporal information (start/end dates)
- [ ] Automatic ROR ID lookup for all affiliations
- [ ] Affiliation validation and normalization
- [ ] Affiliation history tracking (separate from affiliations list)
- [ ] Cross-reference with other catalogs (OpenAlex, EPFL Graph)

## Rollback Plan

If issues arise, to rollback:
1. Revert changes to `src/data_models/models.py` (Affiliation model and Person.affiliations)
2. Revert changes to `src/utils/utils.py`
3. Revert changes to agent prompts
4. Clear cache to remove mixed-format data
5. Restart server

## Fixes Applied

### Issue 1: Nested Organization Objects in Affiliation.name
**Problem**: GIMIE extraction was passing full organization dicts to `Affiliation.name` instead of just the organization name string.

**Fix** (lines 703-705, 863-892 in `src/analysis/repositories.py`):
- Extract name string from organization dicts: `org_data.get("legalName") or org_data.get("name")`
- Add validation to ensure `name` is always a string
- Recursively extract name if nested dict is encountered
- Log warnings when unexpected data types are found

### Issue 2: Affiliation Objects Not JSON Serializable
**Problem**: When passing GIMIE data to the atomic LLM pipeline, `json.dumps()` failed because Affiliation (Pydantic) objects aren't directly JSON serializable.

**Error**: `TypeError: Object of type Affiliation is not JSON serializable`

**Fix** (lines 119-136 in `src/analysis/repositories.py`):
- Convert Person objects to dicts using `model_dump()` before JSON serialization
- Convert Organization objects to dicts using `model_dump()` before JSON serialization
- Added `default=str` fallback to handle any other non-serializable objects
- This ensures all Pydantic models (including nested Affiliation objects) are properly serialized

## Questions or Issues?

If you encounter problems with the new affiliation tracking:
1. Check that all Affiliation objects have required fields (name, source)
2. Verify organizationId is either a string or null (not empty string)
3. Ensure source is one of the valid source types
4. Check logs for validation errors during model creation
5. If you see "Affiliation name is not a string" warnings, check GIMIE extraction logic
