# Organization Enrichment

## Overview

The organization enrichment feature provides a second-pass agentic analysis of repository metadata to identify, standardize, and enrich organization information. It uses **PydanticAI** to create an intelligent agent that can analyze git author emails, ORCID affiliations, and other metadata to comprehensively identify all related organizations.

## Key Features

### 🔍 Multi-Source Analysis
- **Git Author Emails**: Analyzes email domains to identify institutional affiliations
- **ORCID Affiliations**: Extracts organization information from author ORCID records
- **Existing Metadata**: Reviews and refines initial LLM analysis results

### 🌐 ROR Integration
- Queries the [Research Organization Registry (ROR)](https://ror.org/) API
- Provides standardized organization names and persistent identifiers
- Retrieves additional metadata: organization type, country, website, relationships

### 🤖 Agentic Approach
Uses PydanticAI to create an intelligent agent with tools:
- **`search_ror`**: Queries ROR API for organization standardization
- **`search_web`**: Searches the web for additional context (DuckDuckGo)
- **`extract_domain_from_email`**: Analyzes email domains with built-in institutional knowledge

### 📊 Enhanced Organization Model

The `Organization` model has been extended with:

```python
class Organization(BaseModel):
    legalName: str = None                      # Official organization name
    hasRorId: Optional[HttpUrl] = None         # ROR persistent identifier
    alternateNames: Optional[List[str]] = None # Other known names
    organizationType: Optional[str] = None     # university, lab, company, etc.
    parentOrganization: Optional[str] = None   # Parent organization if applicable
    country: Optional[str] = None              # Country location
    website: Optional[HttpUrl] = None          # Official website
```

### 🎯 EPFL Relationship Analysis

Provides detailed analysis of relationship to EPFL, considering:
- Direct affiliations (emails, ORCID records)
- Indirect relationships (labs, joint centers)
- Hierarchical structures (e.g., Swiss Data Science Center established by EPFL & ETH)

## Usage

### API Endpoint

Add the `enrich_orgs=true` parameter to the existing LLM JSON endpoint:

```bash
# Standard LLM analysis with organization enrichment
curl "http://localhost:1234/v1/repository/llm/json/https://github.com/sdsc-ordes/gimie?enrich_orgs=true"
```

**Query Parameters:**
- `enrich_orgs=true` - Enable organization enrichment (default: false)
- `force_refresh=true` - Force refresh from external APIs, bypassing cache (optional)

**Response structure:**
```json
{
  "link": "https://github.com/sdsc-ordes/gimie",
  "output": {
    // Standard LLM analysis results
    "name": "gimie",
    "author": [...],
    "gitAuthors": [...],

    // Organization fields (updated when enrich_orgs=true)
    "relatedToOrganizations": [
      "École Polytechnique Fédérale de Lausanne",
      "Swiss Data Science Center",
      "Institut Pasteur"
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
        "country": "Switzerland"
      }
      // ... more organizations
    ],
    "relatedToEPFL": true,
    "relatedToEPFLJustification": "Multiple lines of evidence...",
    // ... other metadata
  },
  "organization_enrichment": {
    // Full enrichment analysis (for reference)
    "organizations": [...],
    "relatedToEPFL": true,
    "relatedToEPFLJustification": "...",
    "analysis_notes": "..."
  },
  "cached": false
}
```

**Important Fields:**
- **`output.relatedToOrganizations`**: List of organization names (strings) - **backwards compatible**
- **`output.relatedToOrganizationsROR`**: List of Organization objects with ROR data - **new enriched field**
- **`organization_enrichment`**: Full enrichment analysis for reference

### Programmatic Usage

```python
from src.core.organization_enrichment import enrich_organizations_from_dict

# Assuming you have LLM output
llm_output = {
    "name": "repository-name",
    "author": [...],
    "gitAuthors": [...],
    # ... other fields
}

# Enrich organizations
enrichment_result = await enrich_organizations_from_dict(
    llm_output,
    "https://github.com/user/repo"
)

# Access results
for org in enrichment_result["organizations"]:
    print(f"{org['legalName']} - {org.get('hasRorId', 'No ROR ID')}")
```

### Running the Example

```bash
cd /workspaces/git-metadata-extractor
python examples/example_organization_enrichment.py
```

## How It Works

### 1. Initial Analysis
The standard `/v1/repository/llm/json` endpoint extracts metadata using LLM with GIMIE context, including:
- Repository information
- Authors with ORCID enrichment
- Git authors with emails
- Initial organization mentions

### 2. Organization Enrichment (Agentic Analysis)

The PydanticAI agent receives:
- Git author emails
- ORCID affiliations
- Existing organization mentions
- Repository context

The agent then:

1. **Analyzes email domains**
   - Identifies institutional domains (@epfl.ch, @ethz.ch, etc.)
   - Uses built-in knowledge of common academic institutions
   - Extracts organization hints from email patterns

2. **Processes affiliations**
   - Reviews ORCID affiliation strings
   - Identifies variations of organization names
   - Detects departments, labs, and sub-units

3. **Uses tools intelligently**
   - Calls `search_ror` for standardization
   - May use `search_web` for additional context
   - Validates findings with email domain analysis

4. **Builds comprehensive organization list**
   - Standardizes names using ROR data
   - Identifies organizational hierarchies
   - Enriches with metadata (type, country, website)
   - Provides detailed EPFL relationship analysis

### 3. Result Integration

The enriched endpoint returns both:
- Original LLM analysis
- Organization enrichment results

This allows comparison and validation of the enrichment process.

## Architecture

```
┌─────────────────────────┐
│  /llm/json/enriched     │
│  API Endpoint           │
└───────────┬─────────────┘
            │
            ├──► Step 1: Standard LLM Analysis
            │    ├─ GIMIE context extraction
            │    ├─ LLM metadata generation
            │    └─ ORCID author enrichment
            │
            └──► Step 2: Organization Enrichment
                 ├─ PydanticAI Agent
                 │  ├─ System prompt
                 │  ├─ Context (emails, affiliations)
                 │  └─ Tools:
                 │     ├─ search_ror
                 │     ├─ search_web
                 │     └─ extract_domain_from_email
                 │
                 └─ OrganizationEnrichmentResult
                    ├─ Standardized organizations
                    ├─ ROR IDs
                    ├─ EPFL relationship analysis
                    └─ Analysis notes
```

## Configuration

The organization enrichment uses environment variables for LLM configuration:

```bash
# Model configuration (same as main LLM analysis)
export MODEL="gpt-4o"  # or o3-mini, gpt-4o-mini, etc.
export BASE_URL="http://localhost:1234/v1"
export OPENAI_API_KEY="not-needed"  # for local models
```

## Benefits

1. **Standardization**: Uses ROR as authoritative source for organization identification
2. **Completeness**: Analyzes multiple data sources for comprehensive coverage
3. **Hierarchy**: Identifies relationships between organizations (departments, parent orgs)
4. **Validation**: Cross-references multiple sources (emails, ORCID, ROR)
5. **Flexibility**: Agentic approach allows intelligent decision-making and tool use
6. **Traceability**: Provides detailed justifications for findings

## Example: Swiss Data Science Center

For a repository like [gimie](https://github.com/sdsc-ordes/gimie), the enrichment:

1. **Detects** multiple `@epfl.ch` emails in git authors
2. **Identifies** "Swiss Data Science Center" in ORCID affiliations
3. **Queries ROR** for both organizations
4. **Discovers** that Swiss Data Science Center is a joint center of EPFL & ETH
5. **Enriches** with full metadata:
   - Legal names
   - ROR IDs
   - Organization types
   - Countries
   - Websites
6. **Analyzes** EPFL relationship considering both direct and indirect connections

## Dependencies

- `pydanticai`: Agent framework
- `httpx`: Async HTTP client for API calls
- `pydantic`: Data validation
- ROR API: https://ror.org/
- DuckDuckGo API: https://duckduckgo.com/

## Future Enhancements

Potential improvements:
- Cache ROR lookups to reduce API calls
- Add more institutional domain knowledge
- Support additional organization registries (GRID, Wikidata)
- Integrate funding organization databases
- Add confidence scores to organization matches
- Support for disambiguation of common organization names
