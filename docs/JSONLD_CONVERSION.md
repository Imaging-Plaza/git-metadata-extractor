# JSON-LD Conversion Guide

This document explains how the Git Metadata Extractor converts Pydantic models to JSON-LD (JSON for Linking Data) format, and how to extend this system to new models.

## Table of Contents

1. [Overview](#overview)
2. [Architecture](#architecture)
3. [How It Works](#how-it-works)
4. [Extending to New Models](#extending-to-new-models)
5. [Field Mapping Reference](#field-mapping-reference)
6. [API Integration](#api-integration)
7. [Troubleshooting](#troubleshooting)
8. [Examples](#examples)

---

## Overview

### What is JSON-LD?

JSON-LD (JSON for Linking Data) is a lightweight syntax for encoding Linked Data using JSON. It allows data to be:
- **Machine-readable**: Structured for automated processing
- **Semantically rich**: Fields mapped to standard vocabularies (schema.org, custom ontologies)
- **Interoperable**: Can be integrated with other semantic web systems
- **Human-friendly**: Still readable as plain JSON

### Why Use JSON-LD?

1. **Imaging Plaza Integration**: The Imaging Plaza project uses JSON-LD for metadata
2. **Semantic Web Compatibility**: Compatible with RDF, SPARQL, and other semantic tools
3. **Standard Vocabularies**: Leverages schema.org and domain-specific ontologies
4. **Data Integration**: Enables linking across different data sources

### JSON-LD Structure

```json
{
  "@context": {
    "schema": "http://schema.org/",
    "imag": "https://imaging-plaza.epfl.ch/ontology/",
    "md4i": "https://w3id.org/md4i/"
  },
  "@graph": [
    {
      "@id": "https://github.com/user/repo",
      "@type": "http://schema.org/SoftwareSourceCode",
      "schema:name": "Repository Name",
      "schema:author": [
        {
          "@type": "http://schema.org/Person",
          "schema:name": "Jane Doe"
        }
      ]
    }
  ]
}
```

**Key Components:**
- **@context**: Namespace prefix definitions
- **@graph**: Array of entities (resources)
- **@id**: Unique identifier (usually a URL)
- **@type**: Semantic type (from schema.org or custom ontology)

---

## Architecture

### System Components

```
┌─────────────────┐
│ Pydantic Model  │ (SoftwareSourceCode, GitHubUser, etc.)
└────────┬────────┘
         │
         ├─── model.convert_pydantic_to_jsonld()
         │
         v
┌─────────────────────────────┐
│ Generic Converter Function  │ (convert_pydantic_to_jsonld)
│ - Field mapping lookup      │
│ - Recursive conversion      │
│ - Special type handling     │
└────────┬────────────────────┘
         │
         ├─── PYDANTIC_TO_ZOD_MAPPING (field names → URIs)
         ├─── type_mapping (classes → semantic types)
         │
         v
┌─────────────────┐
│   JSON-LD Dict  │ {@context, @graph}
└─────────────────┘
```

### File Locations

- **Generic Converter**: `src/data_models/conversion.py`
  - `convert_pydantic_to_jsonld()` function
  - `PYDANTIC_TO_ZOD_MAPPING` dictionary
  - Type mappings
  
- **Model-Specific Methods**: In respective model files
  - `src/data_models/repository.py` → `SoftwareSourceCode.convert_pydantic_to_jsonld()`
  - `src/data_models/user.py` → `GitHubUser.convert_pydantic_to_jsonld()` (if implemented)
  - `src/data_models/organization.py` → `GitHubOrganization.convert_pydantic_to_jsonld()` (if implemented)

- **API Integration**: `src/api.py`
  - JSON-LD endpoints (`/v1/repository/llm/json-ld/`, `/v1/repository/gimie/json-ld/`)

---

## How It Works

### Step-by-Step Conversion Process

#### 1. Model Method Call

The model instance calls the generic converter:

```python
class SoftwareSourceCode(BaseModel):
    name: str
    author: List[Person]
    # ... more fields
    
    def convert_pydantic_to_jsonld(self) -> dict:
        from src.data_models.conversion import convert_pydantic_to_jsonld
        
        # Determine base URL for @id
        base_url = str(self.codeRepository[0]) if self.codeRepository else None
        
        return convert_pydantic_to_jsonld(self, base_url=base_url)
```

#### 2. Generic Converter

The generic converter (`convert_pydantic_to_jsonld()`) processes the model:

```python
def convert_pydantic_to_jsonld(
    pydantic_obj: Any,
    base_url: Optional[str] = None
) -> Union[Dict, List]:
    """Convert any Pydantic model to JSON-LD format."""
    
    # 1. Get model class name
    model_name = type(pydantic_obj).__name__
    
    # 2. Look up field mappings
    field_mapping = PYDANTIC_TO_ZOD_MAPPING.get(model_name, {})
    
    # 3. Create entity dict
    entity = {}
    
    # 4. Add @id and @type
    entity["@id"] = base_url or f"urn:{model_name}:{id(pydantic_obj)}"
    entity["@type"] = type_mapping.get(type(pydantic_obj), "http://schema.org/Thing")
    
    # 5. Convert fields
    for field_name, field_value in pydantic_obj.model_dump().items():
        if field_value is None:
            continue
            
        # Look up semantic URI for this field
        semantic_key = field_mapping.get(field_name, field_name)
        
        # Convert field value based on type
        entity[semantic_key] = convert_field_value(field_value)
    
    # 6. Wrap in @context and @graph
    return {
        "@context": {...},
        "@graph": [entity]
    }
```

#### 3. Field Value Conversion

Different types are handled specially:

**Simple Types** (str, int, float, bool):
```python
"schema:name": {"@value": "Repository Name"}
```

**URLs** (HttpUrl):
```python
"schema:codeRepository": [{"@id": "https://github.com/user/repo"}]
```

**Dates** (date, datetime):
```python
"schema:datePublished": {"@value": "2024-01-15"}
```

**Enums**:
```python
"imag:discipline": [{"@value": "Computer Science"}]
```

**Nested Models** (Person, Organization):
```python
"schema:author": [
    {
        "@type": "http://schema.org/Person",
        "schema:name": {"@value": "Jane Doe"},
        "md4i:orcidId": {"@id": "https://orcid.org/0000-0001-2345-6789"}
    }
]
```

**Lists**:
Each item is converted recursively, maintaining structure.

#### 4. Field Mapping Lookup

The `PYDANTIC_TO_ZOD_MAPPING` dictionary maps Pydantic field names to semantic URIs:

```python
PYDANTIC_TO_ZOD_MAPPING = {
    "SoftwareSourceCode": {
        "name": "schema:name",
        "description": "schema:description",
        "codeRepository": "schema:codeRepository",
        "author": "schema:author",
        "license": "schema:license",
        "programmingLanguage": "schema:programmingLanguage",
        "discipline": "imag:discipline",
        "relatedToOrganizationsROR": "imag:relatedToOrganizationsROR",
        "relatedToEPFL": "imag:relatedToEPFL",
        # ... more fields
    },
}
```

**Namespace Prefixes:**
- `schema:` → `http://schema.org/` (Standard web schemas)
- `sd:` → `https://w3id.org/okn/o/sd#` (Software Description Ontology)
- `imag:` → `https://imaging-plaza.epfl.ch/ontology/` (Imaging Plaza custom ontology)
- `md4i:` → `https://w3id.org/md4i/` (Metadata for Images ontology)

---

## Extending to New Models

### Complete Example: Adding JSON-LD to `GitHubUser`

Let's walk through adding JSON-LD support to the `GitHubUser` model step by step.

#### Step 1: Define Field Mappings

In `src/data_models/conversion.py`, add to `PYDANTIC_TO_ZOD_MAPPING`:

```python
PYDANTIC_TO_ZOD_MAPPING: Dict[str, Dict[str, str]] = {
    # ... existing mappings ...
    
    "GitHubUser": {
        # Core identity
        "name": "schema:name",
        "fullname": "schema:givenName",
        "githubHandle": "schema:identifier",
        
        # GitHub metadata
        "githubUserMetadata": "imag:githubUserMetadata",
        
        # Organization relationships
        "relatedToOrganization": "imag:relatedToOrganizations",
        "relatedToOrganizationsROR": "imag:relatedToOrganizationsROR",
        "relatedToOrganizationJustification": "imag:relatedToOrganizationJustification",
        
        # Discipline and position
        "discipline": "imag:discipline",
        "disciplineJustification": "imag:disciplineJustification",
        "position": "schema:jobTitle",
        "positionJustification": "imag:positionJustification",
        
        # EPFL relationship
        "relatedToEPFL": "imag:relatedToEPFL",
        "relatedToEPFLJustification": "imag:relatedToEPFLJustification",
        "relatedToEPFLConfidence": "imag:relatedToEPFLConfidence",
        
        # Infoscience
        "infoscienceEntities": "imag:infoscienceEntities",
    },
}
```

**Mapping Strategy:**
1. Use `schema:` for standard fields (name, jobTitle, identifier)
2. Use `imag:` for Imaging Plaza-specific fields (discipline, relatedToEPFL)
3. Use `md4i:` for metadata fields (usually in nested objects)
4. Keep semantic meaning consistent with schema.org when possible

#### Step 2: Add Type Mapping

In `convert_pydantic_to_jsonld()` function, add to `type_mapping`:

```python
def convert_pydantic_to_jsonld(
    pydantic_obj: Any,
    base_url: Optional[str] = None
) -> Union[Dict, List]:
    """Convert any Pydantic model to JSON-LD format."""
    
    # ... existing code ...
    
    # Type mappings - maps Pydantic classes to semantic types
    type_mapping = {
        SoftwareSourceCode: "http://schema.org/SoftwareSourceCode",
        Person: "http://schema.org/Person",
        Organization: "http://schema.org/Organization",
        InfoscienceEntity: "http://schema.org/Thing",
        GitHubUser: "http://schema.org/Person",  # ← Add this
        # ... more types
    }
    
    # ... rest of function ...
```

**Type Selection:**
- Use schema.org types when available (`Person`, `Organization`, `SoftwareSourceCode`)
- Use `Thing` as a fallback for generic entities
- Consider custom ontology types for domain-specific entities

#### Step 3: Add Model Method

In `src/data_models/user.py`, add the conversion method:

```python
from typing import Optional

class GitHubUser(BaseModel):
    """GitHub user profile with enrichment data"""
    
    name: Optional[str] = None
    fullname: Optional[str] = None
    githubHandle: Optional[str] = None
    # ... more fields ...
    
    def convert_pydantic_to_jsonld(self) -> dict:
        """
        Convert this GitHubUser instance to JSON-LD format.
        
        Returns:
            dict: JSON-LD formatted data with @context and @graph
        """
        from src.data_models.conversion import convert_pydantic_to_jsonld
        
        # Determine base URL for @id generation
        # Priority: GitHub profile URL > fallback to URN
        base_url = None
        if self.githubHandle:
            base_url = f"https://github.com/{self.githubHandle}"
        
        return convert_pydantic_to_jsonld(self, base_url=base_url)
```

**Base URL Strategy:**
- Use the most canonical URL for the entity (GitHub profile, repository URL, etc.)
- If no URL available, let converter generate a URN (`urn:ModelName:id`)
- Base URL becomes the `@id` field in JSON-LD output

#### Step 4: Update Analysis Class

In `src/analysis/user.py`, update `dump_results()`:

```python
class User:
    """User analysis class"""
    
    def __init__(self, username: str, force_refresh: bool = False):
        self.username = username
        self.force_refresh = force_refresh
        self.data: Optional[GitHubUser] = None
        # ... other initialization
    
    async def run_analysis(self, ...):
        """Run user analysis"""
        # ... analysis logic ...
        pass
    
    def dump_results(self, output_type: str = "pydantic"):
        """
        Dump results in specified format.
        
        Args:
            output_type: "pydantic" (default), "json-ld", "dict"
            
        Returns:
            Pydantic model, JSON-LD dict, or plain dict depending on output_type
        """
        if output_type == "json-ld":
            if self.data:
                return self.data.convert_pydantic_to_jsonld()
            return None
        elif output_type == "pydantic":
            return self.data
        elif output_type == "dict":
            return self.data.model_dump() if self.data else None
        else:
            raise ValueError(f"Unknown output_type: {output_type}")
```

#### Step 5: Create API Endpoint

In `src/api.py`, add a JSON-LD endpoint:

```python
from fastapi import HTTPException
from src.data_models.api import APIOutput, APIStats, ResourceType
from datetime import datetime

@app.get(
    "/v1/user/llm/json-ld/{full_path:path}",
    tags=["User"],
    responses={
        200: {
            "description": "Successful Response",
            "content": {
                "application/json": {
                    "example": {
                        "link": "https://github.com/username",
                        "type": "user",
                        "parsedTimestamp": "2024-01-15T10:30:00.000Z",
                        "output": {
                            "@context": {
                                "schema": "http://schema.org/",
                                "imag": "https://imaging-plaza.epfl.ch/ontology/",
                                "md4i": "https://w3id.org/md4i/",
                            },
                            "@graph": [{
                                "@id": "https://github.com/username",
                                "@type": "http://schema.org/Person",
                                "schema:name": {"@value": "Jane Doe"},
                                "schema:identifier": {"@value": "username"},
                                "imag:discipline": [{"@value": "Computer Science"}],
                                "imag:relatedToEPFL": True,
                            }]
                        },
                        "stats": {
                            "agent_input_tokens": 1234,
                            "agent_output_tokens": 567,
                            "total_tokens": 1801,
                            "duration": 45.23,
                            "status_code": 200
                        }
                    }
                }
            }
        }
    }
)
async def get_user_jsonld(
    full_path: str = Path(..., description="GitHub user URL or path"),
    force_refresh: bool = Query(False, description="Force refresh from APIs"),
    enrich_orgs: bool = Query(False, description="Enable organization enrichment"),
    enrich_users: bool = Query(False, description="Enable user enrichment"),
) -> APIOutput:
    """
    Retrieve GitHub user profile metadata in JSON-LD format.
    
    This endpoint returns semantic web compatible data with @context and @graph structures.
    """
    with AsyncRequestContext(
        request_type="user_jsonld",
        resource_url=full_path
    ):
        try:
            # Extract username from path
            username = full_path.split("/")[-1]
            
            # Initialize user analysis
            user = User(username, force_refresh=force_refresh)
            
            # Run analysis
            await user.run_analysis(
                run_organization_enrichment=enrich_orgs,
                run_user_enrichment=enrich_users,
            )
            
            # Check if analysis succeeded
            if user.data is None:
                raise HTTPException(
                    status_code=500,
                    detail=f"User analysis failed: no data generated for {username}"
                )
            
            # Convert to JSON-LD
            try:
                jsonld_output = user.dump_results(output_type="json-ld")
                
                if jsonld_output is None:
                    raise ValueError("JSON-LD conversion returned None")
                
                # Verify JSON-LD structure
                if "@context" not in jsonld_output or "@graph" not in jsonld_output:
                    raise ValueError("Missing @context or @graph in JSON-LD output")
                    
            except Exception as e:
                logger.error(f"Failed to convert user to JSON-LD: {e}", exc_info=True)
                raise HTTPException(
                    status_code=500,
                    detail=f"Failed to convert user data to JSON-LD: {str(e)}"
                )
            
            # Get usage statistics
            usage_stats = user.get_usage_stats()
            
            # Create API stats
            stats = APIStats(
                agent_input_tokens=usage_stats["input_tokens"],
                agent_output_tokens=usage_stats["output_tokens"],
                estimated_input_tokens=usage_stats["estimated_input_tokens"],
                estimated_output_tokens=usage_stats["estimated_output_tokens"],
                duration=usage_stats["duration"],
                start_time=usage_stats["start_time"],
                end_time=usage_stats["end_time"],
                status_code=usage_stats["status_code"],
            )
            stats.calculate_total_tokens()
            
            # Return response
            response = APIOutput(
                link=full_path,
                type=ResourceType.USER,
                parsedTimestamp=datetime.now(),
                output=jsonld_output,  # Raw JSON-LD dict
                stats=stats,
            )
            
            return response
            
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"Error in user JSON-LD endpoint: {e}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Internal server error: {str(e)}"
            )
```

**Key Points:**
- Use `APIOutput` with `output: Union[dict, list, ...]` (dict/list FIRST!)
- Include comprehensive error handling
- Validate JSON-LD structure before returning
- Add OpenAPI example showing realistic JSON-LD output
- Return raw dict (not wrapped in additional structure)

---

## Field Mapping Reference

### Current Mappings

#### SoftwareSourceCode

```python
"SoftwareSourceCode": {
    # Schema.org fields
    "name": "schema:name",
    "description": "schema:description",
    "codeRepository": "schema:codeRepository",
    "conditionsOfAccess": "schema:conditionsOfAccess",
    "dateCreated": "schema:dateCreated",
    "dateModified": "schema:dateModified",
    "datePublished": "schema:datePublished",
    "isAccessibleForFree": "schema:isAccessibleForFree",
    "keywords": "schema:keywords",
    "author": "schema:author",
    "license": "schema:license",
    "image": "schema:image",
    "url": "schema:url",
    "featureList": "schema:featureList",
    "operatingSystem": "schema:operatingSystem",
    "applicationCategory": "schema:applicationCategory",
    "programmingLanguage": "schema:programmingLanguage",
    "softwareRequirements": "schema:softwareRequirements",
    
    # Software Description Ontology (sd:)
    "readme": "sd:readme",
    "hasExecutableInstructions": "sd:hasExecutableInstructions",
    "hasDocumentation": "sd:hasDocumentation",
    
    # Imaging Plaza custom fields (imag:)
    "repositoryType": "imag:repositoryType",
    "repositoryTypeJustification": "imag:repositoryTypeJustification",
    "relatedToOrganization": "imag:relatedToOrganizations",
    "relatedToOrganizationJustification": "imag:relatedToOrganizationJustification",
    "relatedToOrganizationsROR": "imag:relatedToOrganizationsROR",
    "discipline": "imag:discipline",
    "disciplineJustification": "imag:disciplineJustification",
    "relatedToEPFL": "imag:relatedToEPFL",
    "relatedToEPFLJustification": "imag:relatedToEPFLJustification",
    "relatedToEPFLConfidence": "imag:relatedToEPFLConfidence",
    "infoscienceEntities": "imag:infoscienceEntities",
    "gitAuthors": "imag:gitAuthors",
    "webpagesToCheck": "imag:webpagesToCheck",
}
```

#### Person

```python
"Person": {
    "name": "schema:name",
    "email": "schema:email",
    "affiliation": "schema:affiliation",
    "affiliations": "schema:affiliation",
    "currentAffiliation": "schema:affiliation",
    "orcidId": "md4i:orcidId",
    "contributionSummary": "imag:contributionSummary",
}
```

#### Organization

```python
"Organization": {
    "legalName": "schema:legalName",
    "alternateNames": "schema:alternateName",
    "hasRorId": "md4i:hasRorId",
    "organizationType": "schema:additionalType",
    "parentOrganization": "schema:parentOrganization",
    "country": "schema:addressCountry",
    "website": "schema:url",
    "attributionConfidence": "imag:attributionConfidence",
}
```

#### InfoscienceEntity

```python
"InfoscienceEntity": {
    "name": "schema:name",
    "url": "schema:url",
    "confidence": "imag:confidence",
    "justification": "imag:justification",
}
```

### Namespace Prefixes

| Prefix | Full URI | Purpose |
|--------|----------|---------|
| `schema:` | `http://schema.org/` | Standard web schemas (name, author, license, etc.) |
| `sd:` | `https://w3id.org/okn/o/sd#` | Software Description Ontology (readme, documentation) |
| `imag:` | `https://imaging-plaza.epfl.ch/ontology/` | Imaging Plaza custom ontology (discipline, EPFL relations) |
| `md4i:` | `https://w3id.org/md4i/` | Metadata for Images (ORCID, ROR IDs) |

### Adding New Fields

When adding new fields to Pydantic models:

1. **Choose the right namespace**:
   - Use `schema:` if the concept exists in schema.org
   - Use `imag:` for domain-specific fields (imaging, research)
   - Use `md4i:` for metadata/identifier fields
   - Use `sd:` for software-specific fields

2. **Check schema.org**: https://schema.org/
   - Search for the concept (e.g., "email" → `schema:email`)
   - Use the exact property name from schema.org

3. **Document custom fields**: If using `imag:` or custom namespaces, document in Imaging Plaza ontology

---

## API Integration

### APIOutput Model

The `APIOutput` model wraps all API responses. For JSON-LD endpoints, special handling is required.

#### Critical: Union Type Ordering

```python
class APIOutput(BaseModel):
    """API output model for all endpoints"""
    
    model_config = {"arbitrary_types_allowed": True}
    
    link: HttpUrl = None
    type: ResourceType = None
    parsedTimestamp: datetime = None
    
    # ✅ CORRECT - dict/list FIRST in Union
    output: Union[dict, list, SoftwareSourceCode, GitHubOrganization, GitHubUser, Any] = None
    
    stats: APIStats = None
```

**Why this matters:**
- Pydantic validates Union types left-to-right
- If models come first, Pydantic tries to coerce dict to model
- This corrupts JSON-LD structure (loses @context, wrong field names)
- Putting `dict, list` first preserves raw JSON-LD structure

#### Field Validator

Preserve dict/list without conversion:

```python
@field_validator("output", mode="before")
@classmethod
def preserve_dict_output(cls, v):
    """Preserve dict/list output as-is without converting to Pydantic models."""
    if isinstance(v, (dict, list)):
        return v
    return v
```

#### Model Serializer

Keep dict/list during serialization:

```python
@model_serializer(mode='wrap')
def serialize_model(self, serializer):
    """Custom serializer to preserve dict/list in output field."""
    data = serializer(self)
    if isinstance(self.output, (dict, list)):
        data['output'] = self.output
    return data
```

### Cache Considerations

JSON-LD endpoints should follow the same caching pattern:

```python
# In endpoint
cache_manager = get_cache_manager()
cache_key = f"user_jsonld:{username}"

# Check cache
if not force_refresh:
    cached = cache_manager.get(cache_key)
    if cached:
        return cached

# ... run analysis ...

# Cache result (365 days)
cache_manager.set(cache_key, response, ttl=365*24*60*60)
```

---

## Troubleshooting

### Common Issues and Solutions

#### Issue 1: Fields Missing from JSON-LD Output

**Symptom**: Some fields from your Pydantic model don't appear in JSON-LD output.

**Cause**: Fields not mapped in `PYDANTIC_TO_ZOD_MAPPING`.

**Solution**: Add field mappings:

```python
# In src/data_models/conversion.py
PYDANTIC_TO_ZOD_MAPPING["YourModel"] = {
    "missingField": "schema:appropriateProperty",
    # ... other fields
}
```

**Verification**: Check conversion output, look for fields with original names (unmapped) vs. prefixed names (mapped).

---

#### Issue 2: Wrong @type in Output

**Symptom**: Entity has `@type: "http://schema.org/Thing"` instead of correct type.

**Cause**: Model class not in `type_mapping` dict.

**Solution**: Add type mapping in `convert_pydantic_to_jsonld()`:

```python
type_mapping = {
    # ... existing types ...
    YourModel: "http://schema.org/YourType",
}
```

---

#### Issue 3: Pydantic Coerces JSON-LD to Model

**Symptom**: API returns wrong model structure (e.g., `GitHubOrganization` instead of JSON-LD).

**Cause**: `APIOutput.output` Union type has models before `dict`.

**Solution**: Reorder Union:

```python
# ❌ WRONG
output: Union[SoftwareSourceCode, dict, list, Any]

# ✅ CORRECT
output: Union[dict, list, SoftwareSourceCode, Any]
```

**Debug**: Add logging before return:

```python
logger.info(f"Response output type: {type(jsonld_output)}")
logger.info(f"Has @context: {'@context' in jsonld_output}")
```

---

#### Issue 4: Nested Models Not Converting

**Symptom**: Nested objects appear as plain dicts instead of JSON-LD entities.

**Cause**: Nested model class not in `type_mapping`.

**Solution**: Add type mapping for nested model:

```python
type_mapping = {
    # ... existing types ...
    NestedModel: "http://schema.org/NestedType",
}
```

**Verification**: Check if nested objects have `@type` field.

---

#### Issue 5: None Values in Output

**Symptom**: JSON-LD contains many `null` or empty fields.

**Cause**: Pydantic fields with `None` values included in output.

**Solution**: The converter already skips `None` values. Check if model is setting default values:

```python
# ❌ Sets empty list even if no data
field: List[str] = Field(default_factory=list)

# ✅ Only set if data exists
field: Optional[List[str]] = None
```

---

#### Issue 6: URLs Not Wrapped in @id

**Symptom**: URLs appear as plain strings instead of `{"@id": "..."}`.

**Cause**: Field type is `str` instead of `HttpUrl`.

**Solution**: Use Pydantic `HttpUrl` type:

```python
from pydantic import HttpUrl

class YourModel(BaseModel):
    website: HttpUrl  # ✅ Will wrap in @id
    # Not: website: str  # ❌ Plain string
```

---

### Debugging Techniques

#### 1. Add Logging

In `convert_pydantic_to_jsonld()`:

```python
logger.debug(f"Converting {model_name} to JSON-LD")
logger.debug(f"Base URL: {base_url}")
logger.debug(f"Field mapping keys: {list(field_mapping.keys())}")
logger.debug(f"Model fields: {list(pydantic_obj.model_fields_set)}")
```

In API endpoint:

```python
logger.info(f"Repository data type: {type(repository.data).__name__}")
logger.info(f"JSON-LD output type: {type(jsonld_output)}")
logger.info(f"JSON-LD output keys: {jsonld_output.keys()}")
if "@graph" in jsonld_output:
    logger.info(f"@graph length: {len(jsonld_output['@graph'])}")
    logger.info(f"First entity @type: {jsonld_output['@graph'][0].get('@type')}")
```

#### 2. Validate JSON-LD Structure

```python
def validate_jsonld(data: dict) -> bool:
    """Validate basic JSON-LD structure"""
    if not isinstance(data, dict):
        return False
    if "@context" not in data:
        print("Missing @context")
        return False
    if "@graph" not in data:
        print("Missing @graph")
        return False
    if not isinstance(data["@graph"], list):
        print("@graph is not a list")
        return False
    if len(data["@graph"]) == 0:
        print("@graph is empty")
        return False
    
    first_entity = data["@graph"][0]
    if "@type" not in first_entity:
        print("First entity missing @type")
        return False
    
    return True

# Use in endpoint
jsonld_output = repository.dump_results(output_type="json-ld")
if not validate_jsonld(jsonld_output):
    raise ValueError("Invalid JSON-LD structure")
```

#### 3. Compare Pydantic vs JSON-LD

```python
# Dump both formats
pydantic_output = repository.dump_results(output_type="pydantic")
jsonld_output = repository.dump_results(output_type="json-ld")

# Compare field presence
pydantic_fields = set(pydantic_output.model_dump().keys())
jsonld_fields = set(jsonld_output["@graph"][0].keys())

missing_in_jsonld = pydantic_fields - jsonld_fields
logger.warning(f"Fields in Pydantic but not JSON-LD: {missing_in_jsonld}")
```

---

## Examples

### Example 1: Complete Repository JSON-LD

Input (Pydantic):
```python
SoftwareSourceCode(
    name="gimie",
    description="Git Meta Information Extractor",
    codeRepository=[HttpUrl("https://github.com/sdsc-ordes/gimie")],
    license="https://spdx.org/licenses/Apache-2.0.html",
    author=[
        Person(
            name="Cyril Matthey-Doret",
            orcidId=HttpUrl("https://orcid.org/0000-0002-1126-1535"),
            affiliations=["EPFL"]
        )
    ],
    programmingLanguage=["Python"],
    discipline=[Discipline.COMPUTER_ENGINEERING],
    relatedToEPFL=True,
    relatedToOrganizationsROR=[
        Organization(
            legalName="EPFL",
            hasRorId=HttpUrl("https://ror.org/03yrm5c26"),
            country="Switzerland"
        )
    ]
)
```

Output (JSON-LD):
```json
{
  "@context": {
    "schema": "http://schema.org/",
    "sd": "https://w3id.org/okn/o/sd#",
    "imag": "https://imaging-plaza.epfl.ch/ontology/",
    "md4i": "https://w3id.org/md4i/"
  },
  "@graph": [
    {
      "@id": "https://github.com/sdsc-ordes/gimie",
      "@type": "http://schema.org/SoftwareSourceCode",
      "schema:name": {"@value": "gimie"},
      "schema:description": {"@value": "Git Meta Information Extractor"},
      "schema:codeRepository": [
        {"@id": "https://github.com/sdsc-ordes/gimie"}
      ],
      "schema:license": {"@id": "https://spdx.org/licenses/Apache-2.0.html"},
      "schema:author": [
        {
          "@type": "http://schema.org/Person",
          "schema:name": {"@value": "Cyril Matthey-Doret"},
          "md4i:orcidId": {"@id": "https://orcid.org/0000-0002-1126-1535"},
          "schema:affiliation": [{"@value": "EPFL"}]
        }
      ],
      "schema:programmingLanguage": [{"@value": "Python"}],
      "imag:discipline": [{"@value": "Computer engineering"}],
      "imag:relatedToEPFL": true,
      "imag:relatedToOrganizationsROR": [
        {
          "@type": "http://schema.org/Organization",
          "schema:legalName": {"@value": "EPFL"},
          "md4i:hasRorId": {"@id": "https://ror.org/03yrm5c26"},
          "schema:addressCountry": {"@value": "Switzerland"}
        }
      ]
    }
  ]
}
```

### Example 2: API Response

GET `/v1/repository/llm/json-ld/https%3A//github.com/sdsc-ordes/gimie`

Response:
```json
{
  "link": "https://github.com/sdsc-ordes/gimie",
  "type": "repository",
  "parsedTimestamp": "2025-10-31T18:06:24.938227",
  "output": {
    "@context": {
      "schema": "http://schema.org/",
      "sd": "https://w3id.org/okn/o/sd#",
      "imag": "https://imaging-plaza.epfl.ch/ontology/",
      "md4i": "https://w3id.org/md4i/"
    },
    "@graph": [
      {
        "@id": "https://github.com/sdsc-ordes/gimie",
        "@type": "http://schema.org/SoftwareSourceCode",
        "schema:name": {"@value": "gimie"},
        "schema:description": {"@value": "Git Meta Information Extractor"},
        "schema:author": [
          {
            "@type": "http://schema.org/Person",
            "schema:name": {"@value": "Cyril Matthey-Doret"},
            "md4i:orcidId": {"@id": "https://orcid.org/0000-0002-1126-1535"}
          }
        ],
        "imag:relatedToEPFL": true
      }
    ]
  },
  "stats": {
    "agent_input_tokens": 0,
    "agent_output_tokens": 0,
    "total_tokens": 0,
    "estimated_input_tokens": 33160,
    "estimated_output_tokens": 3192,
    "estimated_total_tokens": 36352,
    "duration": 260.46219,
    "start_time": "2025-10-31T18:02:04.472401",
    "end_time": "2025-10-31T18:06:24.934591",
    "status_code": 200
  }
}
```

### Example 3: Minimal Implementation for New Model

```python
# 1. In src/data_models/conversion.py
PYDANTIC_TO_ZOD_MAPPING["NewModel"] = {
    "field1": "schema:field1",
    "field2": "imag:field2",
}

# 2. In convert_pydantic_to_jsonld()
type_mapping = {
    # ...
    NewModel: "http://schema.org/Thing",
}

# 3. In src/data_models/yourmodel.py
class NewModel(BaseModel):
    field1: str
    field2: Optional[str] = None
    
    def convert_pydantic_to_jsonld(self) -> dict:
        from src.data_models.conversion import convert_pydantic_to_jsonld
        return convert_pydantic_to_jsonld(self, base_url="https://example.com/entity")

# 4. Test conversion
model = NewModel(field1="value1", field2="value2")
jsonld = model.convert_pydantic_to_jsonld()
print(jsonld)
```

Output:
```json
{
  "@context": {...},
  "@graph": [{
    "@id": "https://example.com/entity",
    "@type": "http://schema.org/Thing",
    "schema:field1": {"@value": "value1"},
    "imag:field2": {"@value": "value2"}
  }]
}
```

---

## Best Practices

1. **Complete Field Mappings**: Map all important fields to semantic URIs
2. **Use Standard Vocabularies**: Prefer schema.org over custom terms
3. **Consistent Namespaces**: Stick to established prefixes (schema, imag, md4i, sd)
4. **Appropriate Base URLs**: Choose canonical URLs for @id generation
5. **Type Validation**: Ensure all models have type mappings
6. **Test Output**: Validate JSON-LD with RDF tools
7. **Document Custom Terms**: Document Imaging Plaza ontology terms
8. **Error Handling**: Add try-catch in endpoints for conversion failures
9. **Logging**: Add debug logs to trace conversion issues
10. **Cache Results**: Cache JSON-LD output (365 days) like other endpoints

---

## Related Documentation

- [FastAPI Patterns](.cursor/rules/fastapi-patterns.mdc) - API endpoint patterns including JSON-LD
- [Pydantic Models](.cursor/rules/pydantic-models.mdc) - Model definitions and JSON-LD conversion
- [Project Architecture](.cursor/rules/project-architecture.mdc) - Overall system structure
- [Imaging Plaza Documentation](https://imaging-plaza.epfl.ch) - Ontology and schema definitions

---

## Questions or Issues?

If you encounter issues not covered in this guide:

1. Check existing JSON-LD endpoints for reference patterns
2. Review error logs for conversion failures
3. Validate field mappings in `PYDANTIC_TO_ZOD_MAPPING`
4. Test with minimal examples before complex models
5. Consult schema.org for standard property names

For Imaging Plaza ontology questions, contact the EPFL Center for Imaging team.

