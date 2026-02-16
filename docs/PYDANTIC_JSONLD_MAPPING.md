# Pydantic to JSON-LD Mapping Documentation

This document describes the mapping between Pydantic models and JSON-LD representations based on the PULSE ontology.

## Ontology Namespaces

The following namespace prefixes are used in the JSON-LD context:

| Prefix | Namespace URI | Description |
|--------|---------------|-------------|
| `schema` | `http://schema.org/` | Schema.org vocabulary |
| `sd` | `https://w3id.org/okn/o/sd#` | Software Description Ontology |
| `pulse` | `https://open-pulse.epfl.ch/ontology#` | PULSE ontology (EPFL Open Science) |
| `md4i` | `http://w3id.org/nfdi4ing/metadata4ing#` | Metadata4Ing ontology |
| `rdf` | `http://www.w3.org/1999/02/22-rdf-syntax-ns#` | RDF vocabulary |
| `rdfs` | `http://www.w3.org/2000/01/rdf-schema#` | RDF Schema |
| `owl` | `http://www.w3.org/2002/07/owl#` | OWL vocabulary |
| `xsd` | `http://www.w3.org/2001/XMLSchema#` | XML Schema Datatypes |
| `dcterms` | `http://purl.org/dc/terms/` | Dublin Core Terms |
| `wd` | `http://www.wikidata.org/entity/` | Wikidata entities |

## Core Data Models

### SoftwareSourceCode

Main model representing a software repository.

| Pydantic Field | JSON-LD Property | RDF Type | Description |
|----------------|------------------|----------|-------------|
| `name` | `schema:name` | `xsd:string` | Repository name |
| `description` | `schema:description` | `xsd:string` | Repository description |
| `codeRepository` | `schema:codeRepository` | `xsd:anyURI` | Code repository URL |
| `dateCreated` | `schema:dateCreated` | `xsd:date` | Creation date |
| `datePublished` | `schema:datePublished` | `xsd:date` | Publication date |
| `license` | `schema:license` | `xsd:anyURI` | SPDX license URL |
| `author` | `schema:author` | `schema:Person` or `schema:Organization` | Authors/contributors |
| `url` | `schema:url` | `xsd:anyURI` | Repository homepage |
| `identifier` | `schema:identifier` | `xsd:string` | Unique identifier |
| `programmingLanguage` | `schema:programmingLanguage` | `xsd:string` | Programming languages |
| `citation` | `schema:citation` | `xsd:anyURI` | Citations |
| `isBasedOn` | `schema:isBasedOn` | `xsd:anyURI` | Based on URL |
| `readme` | `sd:readme` | `xsd:anyURI` | README file URL |
| `discipline` | `pulse:discipline` | `pulse:DisciplineEnumeration` | Scientific disciplines |
| `disciplineJustification` | `pulse:justification` | `xsd:string` | Justification for discipline |
| `repositoryType` | `pulse:repositoryType` | `pulse:RepositoryTypeEnumeration` | Repository type |
| `repositoryTypeJustification` | `pulse:justification` | `xsd:string` | Justification for type |
| `relatedToOrganizations` | `pulse:relatedToOrganization` | `xsd:string` | Related organizations |
| `relatedToOrganizationJustification` | `pulse:justification` | `xsd:string` | Justification for org relation |
| `relatedToEPFL` | `pulse:relatedToEPFL` | `xsd:boolean` | Whether related to EPFL |
| `relatedToEPFLConfidence` | `pulse:confidence` | `xsd:decimal` | Confidence score (0.0-1.0) |
| `relatedToEPFLJustification` | `pulse:justification` | `xsd:string` | Justification for EPFL relation |
| `gitAuthors` | `pulse:gitAuthors` | `schema:Person` | Git commit authors |
| `academicCatalogRelations` | `pulse:hasAcademicCatalogRelation` | `pulse:AcademicCatalogRelation` | Academic catalog relations |
| `applicationCategory` | `schema:applicationCategory` | `xsd:string` | Application categories |
| `featureList` | `schema:featureList` | `xsd:string` | Feature list |
| `image` | `schema:image` | `schema:ImageObject` | Images |
| `isAccessibleForFree` | `schema:isAccessibleForFree` | `xsd:boolean` | Free access |
| `operatingSystem` | `schema:operatingSystem` | `xsd:string` | Operating systems |
| `softwareRequirements` | `schema:softwareRequirements` | `xsd:string` | Software requirements |
| `processorRequirements` | `schema:processorRequirements` | `xsd:string` | Processor requirements |
| `memoryRequirements` | `schema:memoryRequirements` | `xsd:integer` | Memory requirements |
| `requiresGPU` | `pulse:requiresGPU` | `xsd:boolean` | GPU requirements |
| `supportingData` | `schema:supportingData` | `schema:DataFeed` | Supporting data |
| `conditionsOfAccess` | `schema:conditionsOfAccess` | `xsd:string` | Access conditions |
| `hasAcknowledgements` | `sd:hasAcknowledgements` | `xsd:string` | Acknowledgements |
| `hasDocumentation` | `sd:hasDocumentation` | `xsd:anyURI` | Documentation URL |
| `hasExecutableInstructions` | `sd:hasExecutableInstructions` | `xsd:string` | Executable instructions |
| `hasExecutableNotebook` | `pulse:hasExecutableNotebook` | `schema:SoftwareApplication` | Executable notebooks |
| `hasFunding` | `sd:hasFunding` | `schema:Grant` | Funding information |
| `hasSoftwareImage` | `sd:hasSoftwareImage` | `schema:SoftwareApplication` | Software images |
| `imagingModality` | `pulse:imagingModality` | `xsd:string` | Imaging modalities |
| `isPluginModuleOf` | `pulse:isPluginModuleOf` | `xsd:string` | Plugin module of |
| `relatedDatasets` | `pulse:relatedDatasets` | `xsd:string` | Related datasets |
| `relatedPublications` | `pulse:relatedPublications` | `xsd:string` | Related publications |
| `relatedModels` | `pulse:relatedModels` | `xsd:string` | Related models |
| `relatedAPIs` | `pulse:relatedAPIs` | `xsd:string` | Related APIs |

**JSON-LD Type**: `schema:SoftwareSourceCode`

### Person

Represents an individual author or contributor.

| Pydantic Field | JSON-LD Property | RDF Type | Description |
|----------------|------------------|----------|-------------|
| `type` | `@type` | - | Type discriminator ("Person") |
| `name` | `schema:name` | `xsd:string` | Person's full name |
| `email` | `pulse:email` | `xsd:string` | Email address(es) |
| `orcid` | `md4i:orcidId` | `xsd:string` | ORCID identifier |
| `gitAuthorIds` | `pulse:gitAuthorIds` | `xsd:string` | Git author identifiers |
| `affiliations` | `schema:affiliation` | `xsd:string` | All affiliations |
| `currentAffiliation` | `schema:affiliation` | `xsd:string` | Current affiliation |
| `affiliationHistory` | `pulse:affiliationHistory` | - | Temporal affiliation data |
| `contributionSummary` | `pulse:contributionSummary` | `xsd:string` | Contribution summary |
| `biography` | `schema:description` | `xsd:string` | Biographical information |
| `academicCatalogRelations` | `pulse:hasAcademicCatalogRelation` | `pulse:AcademicCatalogRelation` | Academic catalog relations |

**JSON-LD Type**: `schema:Person`

**SHACL Shape**: Defined in PULSE ontology as `schema:Person` with properties:
- `schema:name` (required)
- `md4i:orcidId` (optional)
- `schema:affiliation` (optional)
- `pulse:username` (optional)

### Organization

Represents an institution, lab, or company.

| Pydantic Field | JSON-LD Property | RDF Type | Description |
|----------------|------------------|----------|-------------|
| `type` | `@type` | - | Type discriminator ("Organization") |
| `legalName` | `schema:legalName` | `xsd:string` | Legal/official name |
| `hasRorId` | `md4i:hasRorId` | `xsd:anyURI` | ROR identifier URL |
| `alternateNames` | `schema:alternateName` | `xsd:string` | Alternative names |
| `organizationType` | `schema:additionalType` | `xsd:string` | Organization type |
| `parentOrganization` | `schema:parentOrganization` | `xsd:string` | Parent organization |
| `country` | `schema:addressCountry` | `xsd:string` | Country |
| `website` | `schema:url` | `xsd:anyURI` | Website URL |
| `attributionConfidence` | `pulse:confidence` | `xsd:decimal` | Attribution confidence |
| `academicCatalogRelations` | `pulse:hasAcademicCatalogRelation` | `pulse:AcademicCatalogRelation` | Academic catalog relations |

**JSON-LD Type**: `schema:Organization`

**SHACL Shape**: Defined in PULSE ontology as `schema:Organization` with properties:
- `schema:legalName` (required)
- `md4i:hasRorId` (optional)

### GitHubOrganization

Represents a GitHub organization with enriched metadata.

| Pydantic Field | JSON-LD Property | RDF Type | Description |
|----------------|------------------|----------|-------------|
| `name` | `schema:name` | `xsd:string` | Organization name |
| `organizationType` | `schema:additionalType` | `xsd:string` | Organization type |
| `description` | `schema:description` | `xsd:string` | Description |
| `discipline` | `pulse:discipline` | `pulse:DisciplineEnumeration` | Disciplines |
| `disciplineJustification` | `pulse:justification` | `xsd:string` | Discipline justification |
| `relatedToEPFL` | `pulse:relatedToEPFL` | `xsd:boolean` | EPFL relation |
| `relatedToEPFLJustification` | `pulse:justification` | `xsd:string` | EPFL relation justification |
| `relatedToEPFLConfidence` | `pulse:confidence` | `xsd:decimal` | Confidence score |
| `academicCatalogRelations` | `pulse:hasAcademicCatalogRelation` | `pulse:AcademicCatalogRelation` | Academic catalog relations |
| `githubOrganizationMetadata` | `pulse:metadata` | - | GitHub metadata |

**JSON-LD Type**: `schema:GitHubOrganization`

**SHACL Shape**: Defined in PULSE ontology with properties:
- `pulse:username` (GitHub login)
- `pulse:hasRepository` (repositories)
- `schema:affiliation` (affiliations)

## Academic Catalog Models

### AcademicCatalogRelation

Represents a relationship to an entity in an academic catalog (Infoscience, ORCID, ROR, etc.).

| Pydantic Field | JSON-LD Property | RDF Type | Description |
|----------------|------------------|----------|-------------|
| `catalogType` | `pulse:catalogType` | `xsd:string` | Catalog type (infoscience, orcid, ror, wikidata) |
| `entityType` | `pulse:entityType` | `xsd:string` | Entity type (person, organization, publication, project) |
| `entity` | `pulse:hasCatalogEntity` | `pulse:CatalogEntity` | The catalog entity |
| `confidence` | `pulse:confidence` | `xsd:decimal` | Confidence score (0.0-1.0) |
| `justification` | `pulse:justification` | `xsd:string` | Justification text |
| `matchedOn` | `pulse:matchedOn` | `xsd:string` | Fields matched on |

**JSON-LD Type**: `pulse:AcademicCatalogRelation`

**SHACL Shape**: Defined in PULSE ontology with constraints:
- `pulse:catalogType` (required, enum: infoscience, orcid, ror, wikidata)
- `pulse:entityType` (required, enum: person, organization, publication, project)
- `pulse:hasCatalogEntity` (required)
- `pulse:confidence` (required, range: 0.0-1.0)
- `pulse:justification` (required)

### CatalogEntity

Represents an entity from an academic catalog.

| Pydantic Field | JSON-LD Property | RDF Type | Description |
|----------------|------------------|----------|-------------|
| `uuid` | `pulse:uuid` | `xsd:string` | Unique identifier |
| `name` | `schema:name` | `xsd:string` | Entity name |
| `email` | `pulse:email` | `xsd:string` | Email address |
| `orcid` | `md4i:orcidId` | `xsd:string` | ORCID identifier |
| `affiliation` | `schema:affiliation` | `xsd:string` | Affiliation |
| `profileUrl` | `pulse:profileUrl` | `xsd:anyURI` | Profile URL |

**JSON-LD Type**: `pulse:CatalogEntity`

**SHACL Shape**: Defined in PULSE ontology with properties:
- `pulse:uuid` (required)
- `schema:name` (required)
- `pulse:email` (optional)
- `md4i:orcidId` (optional)
- `schema:affiliation` (optional)
- `pulse:profileUrl` (optional)

### InfosciencePublication

Publication from EPFL's Infoscience repository.

| Pydantic Field | JSON-LD Property | RDF Type | Description |
|----------------|------------------|----------|-------------|
| `type` | `@type` | - | Type discriminator |
| `uuid` | `pulse:uuid` | `xsd:string` | DSpace UUID |
| `title` | `schema:name` | `xsd:string` | Publication title |
| `authors` | `schema:author` | `xsd:string` | Author names |
| `abstract` | `schema:abstract` | `xsd:string` | Abstract text |
| `doi` | `schema:identifier` | `xsd:string` | DOI |
| `publication_date` | `schema:datePublished` | `xsd:date` | Publication date |
| `publication_type` | `schema:additionalType` | `xsd:string` | Publication type |
| `url` | `schema:url` | `xsd:anyURI` | Infoscience URL |
| `repository_url` | `schema:codeRepository` | `xsd:anyURI` | Code repository |
| `lab` | `schema:affiliation` | `xsd:string` | Laboratory |
| `subjects` | `schema:keywords` | `xsd:string` | Subject keywords |

**JSON-LD Type**: `schema:ScholarlyArticle`

### InfoscienceAuthor

Author/researcher from Infoscience.

| Pydantic Field | JSON-LD Property | RDF Type | Description |
|----------------|------------------|----------|-------------|
| `type` | `@type` | - | Type discriminator |
| `uuid` | `pulse:uuid` | `xsd:string` | DSpace UUID |
| `name` | `schema:name` | `xsd:string` | Full name |
| `email` | `pulse:email` | `xsd:string` | Email |
| `orcid` | `md4i:orcidId` | `xsd:string` | ORCID |
| `affiliation` | `schema:affiliation` | `xsd:string` | Affiliation |
| `profile_url` | `pulse:profileUrl` | `xsd:anyURI` | Infoscience profile |

**JSON-LD Type**: `schema:Person`

### InfoscienceLab

Laboratory or organizational unit from Infoscience.

| Pydantic Field | JSON-LD Property | RDF Type | Description |
|----------------|------------------|----------|-------------|
| `type` | `@type` | - | Type discriminator |
| `uuid` | `pulse:uuid` | `xsd:string` | DSpace UUID |
| `name` | `schema:name` | `xsd:string` | Lab name |
| `description` | `schema:description` | `xsd:string` | Description |
| `url` | `schema:url` | `xsd:anyURI` | Infoscience URL |
| `parent_organization` | `schema:parentOrganization` | `xsd:string` | Parent org |
| `website` | `schema:url` | `xsd:anyURI` | External website |
| `research_areas` | `schema:knowsAbout` | `xsd:string` | Research areas |

**JSON-LD Type**: `schema:Organization`

## Supporting Models

### GitAuthor

Git commit author information.

| Pydantic Field | JSON-LD Property | RDF Type | Description |
|----------------|------------------|----------|-------------|
| `name` | `schema:name` | `xsd:string` | Author name |
| `email` | `pulse:email` | `xsd:string` | Email |
| `commits` | `pulse:commits` | `pulse:Commits` | Commit statistics |

**JSON-LD Type**: `schema:Person`

### Commits

Commit statistics.

| Pydantic Field | JSON-LD Property | RDF Type | Description |
|----------------|------------------|----------|-------------|
| `total` | `pulse:totalCommits` | `xsd:integer` | Total commits |
| `firstCommitDate` | `pulse:firstCommitDate` | `xsd:date` | First commit date |
| `lastCommitDate` | `pulse:lastCommitDate` | `xsd:date` | Last commit date |

### FundingInformation

| Pydantic Field | JSON-LD Property | RDF Type | Description |
|----------------|------------------|----------|-------------|
| `identifier` | `schema:identifier` | `xsd:string` | Grant identifier |
| `fundingGrant` | `sd:fundingGrant` | `xsd:string` | Grant number |
| `fundingSource` | `sd:fundingSource` | `schema:Organization` | Funding organization |

**JSON-LD Type**: `schema:Grant`

### DataFeed

| Pydantic Field | JSON-LD Property | RDF Type | Description |
|----------------|------------------|----------|-------------|
| `name` | `schema:name` | `xsd:string` | Name |
| `description` | `schema:description` | `xsd:string` | Description |
| `contentUrl` | `schema:contentUrl` | `xsd:anyURI` | Content URL |
| `measurementTechnique` | `schema:measurementTechnique` | `xsd:string` | Measurement technique |
| `variableMeasured` | `schema:variableMeasured` | `xsd:string` | Variable measured |

**JSON-LD Type**: `schema:DataFeed`

### Image

| Pydantic Field | JSON-LD Property | RDF Type | Description |
|----------------|------------------|----------|-------------|
| `contentUrl` | `schema:contentUrl` | `xsd:anyURI` | Image URL |
| `keywords` | `schema:keywords` | `xsd:string` | Keywords |

**JSON-LD Type**: `schema:ImageObject`

## Enumerations

### Discipline

Scientific disciplines aligned with Wikidata entities.

**JSON-LD Type**: `pulse:DisciplineEnumeration`

**Values**: Mapped to Wikidata entities (e.g., `wd:Q420` for Biology, `wd:Q395` for Mathematics)

Examples:
- `BIOLOGY` → `wd:Q420`
- `MATHEMATICS` → `wd:Q395`
- `PHYSICS` → `wd:Q413`
- `COMPUTER_ENGINEERING` → `wd:Q428691`

### RepositoryType

Repository classification.

**JSON-LD Type**: `pulse:RepositoryTypeEnumeration`

**Values**:
- `SOFTWARE` → `pulse:Software`
- `EDUCATIONAL_RESOURCE` → `pulse:EducationalResource`
- `DOCUMENTATION` → `pulse:Documentation`
- `DATA` → `pulse:Data`
- `OTHER` → `pulse:Other`

## Usage Examples

### Converting Pydantic to JSON-LD

```python
from src.data_models.repository import SoftwareSourceCode
from src.data_models.conversion import convert_pydantic_to_jsonld

# Create a Pydantic model instance
repo = SoftwareSourceCode(
    name="My Research Software",
    description="A tool for scientific computing",
    codeRepository=["https://github.com/example/repo"],
    license="https://spdx.org/licenses/MIT",
    author=[
        Person(
            name="Jane Doe",
            orcid="0000-0002-1234-5678",
            affiliation=["EPFL"]
        )
    ],
    repositoryType=RepositoryType.SOFTWARE,
    repositoryTypeJustification=["Contains source code and documentation"]
)

# Convert to JSON-LD
jsonld = convert_pydantic_to_jsonld(repo, base_url="https://github.com/example/repo")
```

### Converting JSON-LD to Pydantic

```python
from src.data_models.conversion import convert_jsonld_to_pydantic

jsonld_graph = [
    {
        "@id": "https://github.com/example/repo",
        "@type": "schema:SoftwareSourceCode",
        "schema:name": "My Research Software",
        "schema:description": "A tool for scientific computing",
        # ... more properties
    }
]

repo = convert_jsonld_to_pydantic(jsonld_graph)
```

## SHACL Validation

The PULSE ontology includes SHACL shapes for validation. Key constraints:

### schema:SoftwareSourceCode
- `schema:name` (required, max 60 chars)
- `schema:description` (required, max 2000 chars)
- `schema:codeRepository` (required, pattern: `^http.*`)
- `schema:dateCreated` (required, datatype: xsd:date)
- `schema:license` (required, pattern: `.*spdx\.org.*`)
- `schema:author` (required, Person or Organization)
- `pulse:discipline` (class: pulse:DisciplineEnumeration)
- `pulse:repositoryType` (class: pulse:RepositoryTypeEnumeration)

### pulse:AcademicCatalogRelation
- All fields required except `matchedOn`
- `confidence` must be between 0.0 and 1.0
- `catalogType` must be one of: infoscience, orcid, ror, wikidata
- `entityType` must be one of: person, organization, publication, project

## References

- PULSE Ontology: `https://open-pulse.epfl.ch/ontology#`
- Schema.org: `http://schema.org/`
- Software Description Ontology: `https://w3id.org/okn/o/sd#`
- Metadata4Ing: `http://w3id.org/nfdi4ing/metadata4ing#`
- Wikidata: `http://www.wikidata.org/entity/`

## Version History

- **2025-11-06**: Updated to align with PULSE ontology, added academic catalog relations
- **Previous**: Based on imaging-plaza ontology
