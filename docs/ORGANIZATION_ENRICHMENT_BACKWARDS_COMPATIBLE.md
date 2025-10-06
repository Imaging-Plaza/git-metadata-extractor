# Organization Enrichment - Backwards Compatible Implementation

## Overview

The organization enrichment feature has been implemented with **full backwards compatibility** by maintaining two separate fields for organization data.

## Data Structure

### Two Organization Fields

#### 1. `relatedToOrganizations` (List of Strings)
- **Type**: `List[str]`
- **Purpose**: Backwards compatible field
- **Contains**: Simple organization names
- **Example**:
```json
"relatedToOrganizations": [
  "École Polytechnique Fédérale de Lausanne",
  "Swiss Data Science Center",
  "Institut Pasteur"
]
```

#### 2. `relatedToOrganizationsROR` (List of Organization Objects)
- **Type**: `List[Organization]`
- **Purpose**: New enriched field with full metadata
- **Contains**: Complete Organization objects with ROR data
- **Example**:
```json
"relatedToOrganizationsROR": [
  {
    "legalName": "École Polytechnique Fédérale de Lausanne",
    "hasRorId": "https://ror.org/02s376052",
    "alternateNames": ["EPFL", "Ecole Polytechnique Federale de Lausanne"],
    "organizationType": "University",
    "parentOrganization": null,
    "country": "Switzerland",
    "website": "https://www.epfl.ch/"
  },
  {
    "legalName": "Swiss Data Science Center",
    "hasRorId": "https://ror.org/02hdt9m26",
    "alternateNames": ["SDSC"],
    "organizationType": "Facility",
    "parentOrganization": "Joint center of EPFL and ETH Zürich",
    "country": "Switzerland",
    "website": "https://www.datascience.ch/"
  }
]
```

## Behavior

### Without Organization Enrichment (Default)
```bash
curl "http://localhost:1234/v1/repository/llm/json/https://github.com/user/repo"
```

**Result:**
- `relatedToOrganizations`: Populated by LLM (list of strings)
- `relatedToOrganizationsROR`: `null` (not populated)
- Original behavior preserved ✅

### With Organization Enrichment
```bash
curl "http://localhost:1234/v1/repository/llm/json/https://github.com/user/repo?enrich_orgs=true"
```

**Result:**
- `relatedToOrganizations`: Updated with standardized organization names (strings)
- `relatedToOrganizationsROR`: Populated with full Organization objects
- `organization_enrichment`: Additional field with enrichment details
- Enhanced EPFL relationship analysis

## Benefits of This Approach

### ✅ Backwards Compatibility
- Existing code that reads `relatedToOrganizations` continues to work
- No breaking changes to API consumers
- Simple string list remains accessible

### ✅ Progressive Enhancement
- New clients can use `relatedToOrganizationsROR` for rich metadata
- Access to ROR IDs, organization types, countries, websites
- Hierarchical relationships (parent organizations)

### ✅ Data Consistency
- Both fields updated together when enrichment is enabled
- Organization names in `relatedToOrganizations` match `legalName` in `relatedToOrganizationsROR`
- Single source of truth for organization data

## Model Definition

```python
class SoftwareSourceCode(BaseModel):
    # ... other fields ...

    # Backwards compatible: simple list of organization names
    relatedToOrganizations: Optional[List[str]] = None

    # New enriched field: full Organization objects with ROR data
    relatedToOrganizationsROR: Optional[List[Organization]] = None

    # ... other fields ...
```

## Migration Path for Clients

### Current Clients (No Changes Required)
```python
# Existing code continues to work
orgs = response["output"]["relatedToOrganizations"]
for org_name in orgs:
    print(f"Organization: {org_name}")
```

### New Clients (Enhanced Features)
```python
# Access rich organization data
ror_orgs = response["output"]["relatedToOrganizationsROR"]
for org in ror_orgs:
    print(f"Organization: {org['legalName']}")
    print(f"  ROR ID: {org['hasRorId']}")
    print(f"  Type: {org['organizationType']}")
    print(f"  Country: {org['country']}")
    if org['parentOrganization']:
        print(f"  Parent: {org['parentOrganization']}")
```

## API Response Comparison

### Standard Response (enrich_orgs=false)
```json
{
  "link": "...",
  "output": {
    "relatedToOrganizations": ["EPFL", "SDSC"],
    "relatedToOrganizationsROR": null
  },
  "cached": true
}
```

### Enriched Response (enrich_orgs=true)
```json
{
  "link": "...",
  "output": {
    "relatedToOrganizations": [
      "École Polytechnique Fédérale de Lausanne",
      "Swiss Data Science Center"
    ],
    "relatedToOrganizationsROR": [
      {
        "legalName": "École Polytechnique Fédérale de Lausanne",
        "hasRorId": "https://ror.org/02s376052",
        "organizationType": "University",
        "country": "Switzerland"
      },
      {
        "legalName": "Swiss Data Science Center",
        "hasRorId": "https://ror.org/02hdt9m26",
        "organizationType": "Facility",
        "country": "Switzerland"
      }
    ],
    "relatedToEPFL": true,
    "relatedToEPFLJustification": "..."
  },
  "organization_enrichment": {
    "organizations": [...],
    "relatedToEPFL": true,
    "relatedToEPFLJustification": "...",
    "analysis_notes": "..."
  },
  "cached": false
}
```

## Implementation Details

When `enrich_orgs=true`, the API:

1. Performs standard LLM analysis (as before)
2. Runs organization enrichment with PydanticAI agent
3. Updates **both** organization fields:
   ```python
   enriched_orgs = organization_enrichment.get('organizations', [])
   if enriched_orgs:
       # Update string list (backwards compatible)
       llm_result['relatedToOrganizations'] = [
           org.get('legalName') for org in enriched_orgs
       ]
       # Add enriched objects (new feature)
       llm_result['relatedToOrganizationsROR'] = enriched_orgs
   ```
4. Updates EPFL relationship fields
5. Includes full enrichment data in separate `organization_enrichment` field

## Testing

### Verify Backwards Compatibility
```bash
# Standard request (no enrichment)
curl "http://localhost:1234/v1/repository/llm/json/https://github.com/sdsc-ordes/gimie" \
  | jq '.output.relatedToOrganizations'
# Returns: ["EPFL", "SDSC", ...] (strings)

curl "http://localhost:1234/v1/repository/llm/json/https://github.com/sdsc-ordes/gimie" \
  | jq '.output.relatedToOrganizationsROR'
# Returns: null
```

### Verify Enrichment
```bash
# Enriched request
curl "http://localhost:1234/v1/repository/llm/json/https://github.com/sdsc-ordes/gimie?enrich_orgs=true" \
  | jq '.output.relatedToOrganizations'
# Returns: ["École Polytechnique Fédérale de Lausanne", "Swiss Data Science Center", ...]

curl "http://localhost:1234/v1/repository/llm/json/https://github.com/sdsc-ordes/gimie?enrich_orgs=true" \
  | jq '.output.relatedToOrganizationsROR[0]'
# Returns: {legalName: "...", hasRorId: "...", organizationType: "...", ...}
```

## Summary

✅ **Backwards Compatible**: Existing `relatedToOrganizations` field preserved
✅ **Enhanced Data**: New `relatedToOrganizationsROR` field with rich metadata
✅ **Opt-In**: Enrichment only runs when explicitly requested
✅ **Non-Breaking**: No changes required for existing API consumers
✅ **Progressive**: New clients can leverage enhanced organization data

This implementation provides the best of both worlds: maintaining compatibility while enabling powerful new features! 🎉
