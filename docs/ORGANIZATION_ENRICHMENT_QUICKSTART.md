# Organization Enrichment - Qui**3. New API Endpoint** (`src/api.py`)

Enhanced the existing endpoint with an optional parameter:
```
GET /v1/repository/llm/json/{repository_url}?enrich_orgs=true
```

When `enrich_orgs=true`:
1. Performs standard LLM analysis (same as before)
2. Runs organization enrichment with PydanticAI agent
3. Returns both standard output and enrichment results
## What Was Implemented

A second-pass agentic analysis system using **PydanticAI** that refines and enriches organization information from repository metadata.

## Key Components

### 1. Enhanced Organization Model (`src/core/models.py`)
```python
class Organization(BaseModel):
    legalName: str = None
    hasRorId: Optional[HttpUrl] = None
    alternateNames: Optional[List[str]] = None  # NEW
    organizationType: Optional[str] = None      # NEW
    parentOrganization: Optional[str] = None    # NEW
    country: Optional[str] = None               # NEW
    website: Optional[HttpUrl] = None           # NEW
```

### 2. PydanticAI Agent (`src/core/organization_enrichment.py`)

**Tools available to the agent:**
- `search_ror()` - Query ROR API for standardized org info
- `search_web()` - Search web for additional context (DuckDuckGo)
- `extract_domain_from_email()` - Analyze institutional email domains

**Main functions:**
- `enrich_organizations()` - Core enrichment logic
- `enrich_organizations_from_dict()` - Convenience wrapper for API use

### 3. New API Endpoint (`src/api.py`)

```
GET /v1/repository/llm/json/enriched/{repository_url}
```

Performs two-step analysis:
1. Standard LLM analysis (same as `/llm/json`)
2. Organization enrichment with PydanticAI agent

### 4. Documentation & Examples

- **`docs/ORGANIZATION_ENRICHMENT.md`** - Complete documentation
- **`examples/example_organization_enrichment.py`** - Runnable example

## Usage

### Test the API Endpoint

```bash
# Make sure your server is running
curl "http://localhost:1234/v1/repository/llm/json/https://github.com/sdsc-ordes/gimie?enrich_orgs=true" | jq
```

### Run the Example

```bash
cd /workspaces/git-metadata-extractor
python examples/example_organization_enrichment.py
```

### Install New Dependencies

```bash
pip install httpx pydanticai
```

## How It Works

1. **Analyzes git author emails** (e.g., `robin.franken@epfl.ch` → EPFL)
2. **Reviews ORCID affiliations** (e.g., "Swiss Data Science Center")
3. **Queries ROR API** to get standardized names and IDs
4. **Identifies hierarchies** (e.g., SDSC is joint center of EPFL & ETH)
5. **Enriches metadata** with type, country, website, etc.
6. **Provides detailed EPFL analysis** with specific evidence

## Example Output

```json
{
  "output": {
    "relatedToOrganizations": [
      "École Polytechnique Fédérale de Lausanne",
      "Swiss Data Science Center"
    ],
    "relatedToOrganizationsROR": [
      {
        "legalName": "École Polytechnique Fédérale de Lausanne",
        "hasRorId": "https://ror.org/02s376052",
        "alternateNames": ["EPFL"],
        "organizationType": "University",
        "country": "Switzerland",
        "website": "https://www.epfl.ch/"
      },
      {
        "legalName": "Swiss Data Science Center",
        "hasRorId": "https://ror.org/02hdt9m26",
        "organizationType": "Facility",
        "parentOrganization": "Joint center of EPFL and ETH Zürich"
      }
    ],
    "relatedToEPFL": true,
    "relatedToEPFLJustification": "Multiple authors with @epfl.ch emails..."
  }
}
```

**Key fields:**
- `relatedToOrganizations` - Organization names (strings) - **backwards compatible**
- `relatedToOrganizationsROR` - Full Organization objects with ROR data - **new**

## Architecture

```
Initial LLM Analysis → Organization Enrichment Agent → Enhanced Results
                              ↓
                      [PydanticAI Tools]
                       - ROR API
                       - Web Search
                       - Email Analysis
```

## Next Steps

1. Install dependencies: `pip install httpx pydanticai`
2. Test the example: `python examples/example_organization_enrichment.py`
3. Try the API endpoint with your repositories
4. Review `docs/ORGANIZATION_ENRICHMENT.md` for full details
