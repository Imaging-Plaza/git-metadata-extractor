# JSON-LD Conversion Summary

## Quick Reference: Key Property Mappings

This document provides a quick reference for the most commonly used Pydantic→JSON-LD property mappings.

### Core Repository Properties

| Pydantic Field | JSON-LD Property | Notes |
|----------------|------------------|-------|
| `name` | `schema:name` | Repository name |
| `description` | `schema:description` | Repository description |
| `codeRepository` | `schema:codeRepository` | GitHub/GitLab URL |
| `author` | `schema:author` | List of Person/Organization |
| `license` | `schema:license` | SPDX license URL |
| `discipline` | `pulse:discipline` | Wikidata discipline URIs |
| `repositoryType` | `pulse:repositoryType` | PULSE enum values |

### Person Properties

| Pydantic Field | JSON-LD Property | Notes |
|----------------|------------------|-------|
| `name` | `schema:name` | Full name |
| `email` | `pulse:email` | Email address |
| `orcid` | `md4i:orcidId` | ORCID identifier |
| `affiliation` | `schema:affiliation` | Institution/org |
| `academicCatalogRelations` | `pulse:hasAcademicCatalogRelation` | Catalog links |

### Organization Properties

| Pydantic Field | JSON-LD Property | Notes |
|----------------|------------------|-------|
| `legalName` | `schema:legalName` | Official name |
| `hasRorId` | `md4i:hasRorId` | ROR identifier URL |
| `website` | `schema:url` | Organization website |

### Academic Catalog Relations

| Pydantic Field | JSON-LD Property | Notes |
|----------------|------------------|-------|
| `catalogType` | `pulse:catalogType` | infoscience, orcid, ror, wikidata |
| `entityType` | `pulse:entityType` | person, organization, publication, project |
| `entity` | `pulse:hasCatalogEntity` | The actual entity |
| `confidence` | `pulse:confidence` | 0.0-1.0 |
| `justification` | `pulse:justification` | Why this relation exists |
| `matchedOn` | `pulse:matchedOn` | Fields used for matching |

## Namespace Prefixes

```turtle
@prefix schema: <http://schema.org/> .
@prefix sd: <https://w3id.org/okn/o/sd#> .
@prefix pulse: <https://open-pulse.epfl.ch/ontology#> .
@prefix md4i: <http://w3id.org/nfdi4ing/metadata4ing#> .
@prefix rdf: <http://www.w3.org/1999/02/22-rdf-syntax-ns#> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
@prefix owl: <http://www.w3.org/2002/07/owl#> .
@prefix xsd: <http://www.w3.org/2001/XMLSchema#> .
@prefix wd: <http://www.wikidata.org/entity/> .
```

## Example JSON-LD Output

### Repository with Author

```json
{
  "@context": {
    "schema": "http://schema.org/",
    "pulse": "https://open-pulse.epfl.ch/ontology#",
    "md4i": "http://w3id.org/nfdi4ing/metadata4ing#"
  },
  "@graph": [
    {
      "@id": "https://github.com/example/my-repo",
      "@type": "schema:SoftwareSourceCode",
      "schema:name": "My Research Software",
      "schema:description": "A tool for scientific computing",
      "schema:codeRepository": [
        {"@id": "https://github.com/example/my-repo"}
      ],
      "schema:license": "https://spdx.org/licenses/MIT",
      "schema:author": [
        {
          "@type": "schema:Person",
          "schema:name": "Jane Doe",
          "md4i:orcidId": {"@id": "https://orcid.org/0000-0002-1234-5678"},
          "schema:affiliation": ["EPFL"]
        }
      ],
      "pulse:repositoryType": "pulse:Software",
      "pulse:discipline": [
        {"@id": "wd:Q420"}
      ]
    }
  ]
}
```

### Person with Academic Catalog Relations

```json
{
  "@context": {
    "schema": "http://schema.org/",
    "pulse": "https://open-pulse.epfl.ch/ontology#",
    "md4i": "http://w3id.org/nfdi4ing/metadata4ing#"
  },
  "@graph": [
    {
      "@type": "schema:Person",
      "schema:name": "Jane Doe",
      "pulse:email": "jane.doe@epfl.ch",
      "md4i:orcidId": "0000-0002-1234-5678",
      "schema:affiliation": ["EPFL", "CVLAB"],
      "pulse:hasAcademicCatalogRelation": [
        {
          "@type": "pulse:AcademicCatalogRelation",
          "pulse:catalogType": "infoscience",
          "pulse:entityType": "person",
          "pulse:hasCatalogEntity": {
            "@type": "pulse:CatalogEntity",
            "pulse:uuid": "abc-123-def",
            "schema:name": "Jane Doe",
            "pulse:profileUrl": {
              "@id": "https://infoscience.epfl.ch/entities/person/abc-123-def"
            }
          },
          "pulse:confidence": 0.95,
          "pulse:justification": "Matched on name and email",
          "pulse:matchedOn": ["name", "email"]
        }
      ]
    }
  ]
}
```

### Organization with ROR

```json
{
  "@context": {
    "schema": "http://schema.org/",
    "md4i": "http://w3id.org/nfdi4ing/metadata4ing#"
  },
  "@graph": [
    {
      "@type": "schema:Organization",
      "schema:legalName": "École Polytechnique Fédérale de Lausanne",
      "md4i:hasRorId": {"@id": "https://ror.org/02s376052"},
      "schema:url": {"@id": "https://www.epfl.ch"}
    }
  ]
}
```

## Conversion Functions

### Pydantic → JSON-LD

```python
from src.data_models.conversion import convert_pydantic_to_jsonld

# Convert any Pydantic model to JSON-LD
jsonld = convert_pydantic_to_jsonld(pydantic_model, base_url=optional_base_url)
```

The function:
1. Automatically detects the model type
2. Maps fields using `PYDANTIC_TO_ZOD_MAPPING`
3. Handles nested models recursively
4. Converts enums to proper values
5. Formats dates as ISO 8601
6. Converts ORCID IDs to URLs

### JSON-LD → Pydantic

```python
from src.data_models.conversion import convert_jsonld_to_pydantic

# Convert JSON-LD graph to Pydantic model
model = convert_jsonld_to_pydantic(jsonld_graph)
```

The function:
1. Parses the `@graph` array
2. Identifies entity types via `@type`
3. Maps JSON-LD properties to Pydantic fields using `JSONLD_TO_PYDANTIC_MAPPING`
4. Resolves nested entity references
5. Validates and constructs Pydantic models

## Important Notes

### ORCID Handling

ORCID identifiers are stored as plain strings in Pydantic (`0000-0002-1234-5678`) but **always** converted to URL format in JSON-LD:

```json
"md4i:orcidId": {"@id": "https://orcid.org/0000-0002-1234-5678"}
```

### Discipline Values

Disciplines are Wikidata entity URIs:
- Biology: `wd:Q420`
- Mathematics: `wd:Q395`
- Physics: `wd:Q413`
- Computer Engineering: `wd:Q428691`

Full list in PULSE ontology documentation.

### Repository Types

Repository types use PULSE enum values:
- Software: `pulse:Software`
- Educational Resource: `pulse:EducationalResource`
- Documentation: `pulse:Documentation`
- Data: `pulse:Data`
- Other: `pulse:Other`

### Confidence Scores

All confidence scores must be between 0.0 and 1.0 (inclusive). Used for:
- `pulse:confidence` in academic catalog relations
- `pulse:relatedToEPFLConfidence`
- `Organization.attributionConfidence`

### Justification Fields

Multiple fields map to `pulse:justification`:
- `disciplineJustification`
- `repositoryTypeJustification`
- `relatedToOrganizationJustification`
- `relatedToEPFLJustification`
- `AcademicCatalogRelation.justification`

These are kept separate in Pydantic for context but may be merged in JSON-LD serialization.

## Validation

The PULSE ontology includes SHACL shapes for validation. Key rules:

1. **Required fields**: Many properties are marked `sh:minCount 1`
2. **Pattern constraints**: URLs must match `^http.*`
3. **Length constraints**: `schema:name` has `sh:maxLength 60`
4. **Cardinality**: Some fields are `sh:maxCount 1`
5. **Enumerations**: `catalogType`, `entityType`, etc. have fixed value lists

Run SHACL validation after conversion to ensure compliance.

## Migration Notes

### Changes from imaging-plaza to PULSE

Key namespace changes:
- `imag:` → `pulse:` for custom properties
- `md4i:orcid` → `md4i:orcidId`
- Added academic catalog relation support
- Added Wikidata discipline mappings

### Deprecated Properties

- `imag:infoscienceEntities` → Use `pulse:hasAcademicCatalogRelation`
- `imag:relatedToOrganization` → `pulse:relatedToOrganization`

## See Also

- [Full Mapping Documentation](./PYDANTIC_JSONLD_MAPPING.md)
- [PULSE Ontology](https://open-pulse.epfl.ch/ontology#)
- [Academic Catalog Integration](./ACADEMIC_CATALOG_OPTION_B_IMPLEMENTATION.md)
