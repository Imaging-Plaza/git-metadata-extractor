# JSON-LD Mapping Update - PULSE Ontology Integration

## Summary

Updated the Pydantic→JSON-LD mapping system to align with the PULSE (EPFL Open Science) ontology. This enables proper semantic representation of research software metadata in RDF/JSON-LD format.

## Changes Made

### 1. Updated Namespace Prefixes

**File**: `src/data_models/conversion.py`

Changed from `imaging-plaza` to `pulse` ontology:

```python
# Before
context = {
    "schema": "http://schema.org/",
    "sd": "https://w3id.org/okn/o/sd#",
    "imag": "https://imaging-plaza.epfl.ch/ontology/",
    "md4i": "https://w3id.org/md4i/",
}

# After
context = {
    "schema": "http://schema.org/",
    "sd": "https://w3id.org/okn/o/sd#",
    "pulse": "https://open-pulse.epfl.ch/ontology#",
    "md4i": "http://w3id.org/nfdi4ing/metadata4ing#",
    "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
    "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
    "owl": "http://www.w3.org/2002/07/owl#",
    "xsd": "http://www.w3.org/2001/XMLSchema#",
    "dcterms": "http://purl.org/dc/terms/",
    "wd": "http://www.wikidata.org/entity/",
}
```

### 2. Extended Property Mappings

**File**: `src/data_models/conversion.py`

#### Added New Models
- `AcademicCatalogRelation`: Links to academic catalogs (Infoscience, ORCID, ROR, Wikidata)
- `CatalogEntity`: Entities from academic catalogs
- `InfosciencePublication`: Publications from EPFL's Infoscience
- `InfoscienceAuthor`: Authors from Infoscience
- `InfoscienceLab`: Labs/orgunits from Infoscience
- `GitHubOrganization`: GitHub org with enriched metadata

#### Updated Existing Models
- **Person**: Added `academicCatalogRelations`, `gitAuthorIds`, `affiliationHistory`, etc.
- **Organization**: Added `academicCatalogRelations`
- **SoftwareSourceCode**: Added `academicCatalogRelations`, updated property mappings

### 3. Property Mapping Updates

Key changes in `PYDANTIC_TO_ZOD_MAPPING`:

| Old Property | New Property | Model |
|--------------|--------------|-------|
| `imag:confidence` | `pulse:confidence` | All |
| `imag:justification` | `pulse:justification` | All |
| `imag:discipline` | `pulse:discipline` | SoftwareSourceCode |
| `imag:repositoryType` | `pulse:repositoryType` | SoftwareSourceCode |
| `imag:relatedToOrganization` | `pulse:relatedToOrganization` | SoftwareSourceCode |
| `md4i:orcid` | `md4i:orcidId` | Person |
| `schema:email` | `pulse:email` | Person, GitAuthor |

### 4. Added Bidirectional Mappings

Updated `JSONLD_TO_PYDANTIC_MAPPING` to support both full URIs and prefixed forms:

```python
# Example: Both forms supported
"http://schema.org/name": "name",
"schema:name": "name",
"https://open-pulse.epfl.ch/ontology#confidence": "confidence",
"pulse:confidence": "confidence",
```

### 5. Type Mappings

Updated to align with PULSE ontology SHACL shapes:

```python
type_mapping = {
    "SoftwareSourceCode": "schema:SoftwareSourceCode",
    "Person": "schema:Person",
    "Organization": "schema:Organization",
    "GitHubOrganization": "schema:GitHubOrganization",
    "AcademicCatalogRelation": "pulse:AcademicCatalogRelation",
    "CatalogEntity": "pulse:CatalogEntity",
    "InfosciencePublication": "schema:ScholarlyArticle",
    "Discipline": "pulse:DisciplineEnumeration",
    "RepositoryType": "pulse:RepositoryTypeEnumeration",
    # ... more
}
```

### 6. Documentation

Created comprehensive documentation:

#### `docs/PYDANTIC_JSONLD_MAPPING.md`
- Complete property mappings for all models
- SHACL shape references
- Datatype specifications
- Usage examples
- Validation rules

#### `docs/JSONLD_CONVERSION_SUMMARY.md`
- Quick reference tables
- Common use cases
- Example JSON-LD outputs
- Migration notes from imaging-plaza
- ORCID handling specifics

## New Features

### Academic Catalog Relations

The system now supports linking entities to academic catalogs:

```python
AcademicCatalogRelation(
    catalogType="infoscience",
    entityType="person",
    entity=CatalogEntity(
        uuid="abc-123",
        name="Jane Doe",
        email="jane@epfl.ch",
        profileUrl="https://infoscience.epfl.ch/entities/person/abc-123"
    ),
    confidence=0.95,
    justification="Matched on name and email",
    matchedOn=["name", "email"]
)
```

This converts to:

```json
{
  "@type": "pulse:AcademicCatalogRelation",
  "pulse:catalogType": "infoscience",
  "pulse:entityType": "person",
  "pulse:hasCatalogEntity": {
    "@type": "pulse:CatalogEntity",
    "pulse:uuid": "abc-123",
    "schema:name": "Jane Doe",
    "pulse:email": "jane@epfl.ch",
    "pulse:profileUrl": {"@id": "https://infoscience.epfl.ch/entities/person/abc-123"}
  },
  "pulse:confidence": 0.95,
  "pulse:justification": "Matched on name and email",
  "pulse:matchedOn": ["name", "email"]
}
```

### Wikidata Discipline Mapping

Disciplines are now mapped to Wikidata entities:

```python
Discipline.BIOLOGY  # → wd:Q420
Discipline.MATHEMATICS  # → wd:Q395
Discipline.PHYSICS  # → wd:Q413
```

### PULSE Repository Types

Repository types use PULSE ontology enumerations:

```python
RepositoryType.SOFTWARE  # → pulse:Software
RepositoryType.EDUCATIONAL_RESOURCE  # → pulse:EducationalResource
RepositoryType.DATA  # → pulse:Data
```

## Validation

The mappings align with PULSE ontology SHACL shapes:

### Key Constraints
- `schema:name`: max 60 characters
- `schema:description`: max 2000 characters
- `schema:codeRepository`: pattern `^http.*`
- `pulse:confidence`: range 0.0-1.0
- `schema:author`: required, Person or Organization
- `pulse:catalogType`: enum (infoscience, orcid, ror, wikidata)
- `pulse:entityType`: enum (person, organization, publication, project)

## Migration Path

### Backward Compatibility

Old properties are still supported in JSON-LD input but will be converted to new properties:

```python
# Both work:
"imag:confidence" → mapped to "confidence"
"pulse:confidence" → mapped to "confidence"
```

### Code Changes Required

If you're using the old property names in code:

```python
# Before
"imag:relatedToOrganization"
"imag:infoscienceEntities"

# After
"pulse:relatedToOrganization"
"pulse:hasAcademicCatalogRelation"
```

## Testing

### Using the CLI Tool (Recommended)

A command-line tool is available for easy conversion:

```bash
# Convert JSON to JSON-LD
python scripts/convert_json_jsonld.py to-jsonld input.json output.jsonld \
    --base-url https://github.com/user/repo

# Convert JSON-LD to JSON
python scripts/convert_json_jsonld.py to-json input.jsonld output.json
```

See [JSON-LD Conversion CLI Guide](./JSON_JSONLD_CONVERSION_CLI.md) for detailed usage.

### Using Python Code

To test the conversion in Python:

```python
from src.data_models.repository import SoftwareSourceCode
from src.data_models.models import Person, RepositoryType
from src.data_models.conversion import convert_pydantic_to_jsonld

repo = SoftwareSourceCode(
    name="Test Repo",
    description="A test repository",
    codeRepository=["https://github.com/test/repo"],
    author=[
        Person(
            name="Test User",
            orcid="0000-0002-1234-5678"
        )
    ],
    repositoryType=RepositoryType.SOFTWARE,
    repositoryTypeJustification=["Contains source code"]
)

jsonld = convert_pydantic_to_jsonld(repo, base_url="https://github.com/test/repo")
print(jsonld)
```

## Files Modified

1. `src/data_models/conversion.py` - Main conversion logic
2. `docs/PYDANTIC_JSONLD_MAPPING.md` - Complete mapping documentation
3. `docs/JSONLD_CONVERSION_SUMMARY.md` - Quick reference guide

## Next Steps

1. **SHACL Validation**: Implement SHACL validation using the PULSE shapes
2. **RDF Export**: Add Turtle/N-Triples serialization options
3. **GraphDB Integration**: Connect to EPFL's triplestore
4. **SPARQL Queries**: Create example queries for common use cases
5. **CLI Tool**: Add command-line tool for JSON→JSON-LD conversion

## References

- [PULSE Ontology](https://open-pulse.epfl.ch/ontology#)
- [Schema.org](http://schema.org/)
- [Software Description Ontology](https://w3id.org/okn/o/sd#)
- [Metadata4Ing](http://w3id.org/nfdi4ing/metadata4ing#)
- [Wikidata](https://www.wikidata.org/)

## Version

- **Date**: 2025-11-06
- **Author**: GitHub Copilot
- **Version**: 2.0.0 (PULSE integration)
