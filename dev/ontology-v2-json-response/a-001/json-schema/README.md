# JSON Schema for Open Pulse Ontology v2.0.0

This folder contains JSON Schema (draft-07) definitions for validating JSON responses conforming to the [Open Pulse Ontology v2.0.0](../open-pulse-ontology-v2.0.0.ttl) SHACL shapes.

## Quick Start

```bash
# Validate JSON with strict schema (using ajv-cli)
ajv validate -s strict/pulse:PersonShape.schema.json -d ../pulse:PersonShape.json

# Or use Python jsonschema
python -c "import json, jsonschema; jsonschema.validate(json.load(open('../pulse:PersonShape.json')), json.load(open('strict/pulse:PersonShape.schema.json')))"
```

## Folder Structure

```
json-schema/
├── strict/           # Full validation with regex patterns, enums, type enforcement
│   ├── pulse:PersonShape.schema.json
│   ├── pulse:RepositoryShape.schema.json
│   ├── pulse:OrganizationShape.schema.json
│   ├── pulse:MembershipShape.schema.json
│   ├── pulse:ContributionShape.schema.json
│   └── pulse:ArticleShape.schema.json
├── agent/            # Relaxed schemas optimized for LLM agent parsing
│   ├── pulse:PersonShape.schema.json
│   ├── pulse:RepositoryShape.schema.json
│   ├── pulse:OrganizationShape.schema.json
│   ├── pulse:MembershipShape.schema.json
│   ├── pulse:ContributionShape.schema.json
│   └── pulse:ArticleShape.schema.json
└── README.md
```

## Schema Versions

### `strict/` - Full Validation

Use these schemas for:
- **Production validation** of JSON data
- **CI/CD pipelines** requiring strict conformance
- **Data quality checks** before ingestion

Features:
- Regex patterns for all identifiers (ORCID, DOI, ROR, UUID4, GitHub handles, email)
- Enum constraints for `idSource`, `pulse:repositoryType`, `pulse:OrganizationType`
- `minItems`, `minimum` constraints where applicable
- `additionalProperties: false` to reject unknown fields

### `agent/` - LLM Agent Parsing

Use these schemas for:
- **LLM/AI agent output parsing** with structured outputs
- **Development and prototyping** with flexible validation
- **Documentation** of expected formats via descriptions

Features:
- Permissive types (`string`, `["string", "null"]`, `["integer", "null"]`)
- Rich `description` fields with format examples
- No regex enforcement (formats described in text)
- `additionalProperties: false` to guide agents toward known properties

## SHACL Required Fields

Based on the Open Pulse Ontology v2.0.0 SHACL constraints, each shape has specific required fields (minCount ≥ 1):

| Shape | Required Fields (per SHACL) |
|-------|----------------------------|
| PersonShape | `schema:name` + **at least ONE of**: `pulse:githubUsername`, `schema:email`, `pulse:infosciencePersonIdentifier` |
| RepositoryShape | `schema:name`, `pulse:githubRepositoryHandle`, `schema:author` (min 1) |
| OrganizationShape | `schema:name` only |
| MembershipShape | `org:organization` only |
| ContributionShape | `pulse:contributionTo`, `pulse:contributionCount`, `schema:author` |
| ArticleShape | `schema:name`, `schema:identifier` (DOI), `schema:datePublished`, `schema:author` (min 1) |

> **Note:** All shapes also require the metadata fields: `id`, `type`, `shacl`, `identifiers`, `idSource`

## ID Hierarchy Strategy

Each shape uses a prioritized hierarchy of identifiers. The `id` field is set to the first available (non-null) identifier, and `idSource` indicates which was used.

| Shape | ID Hierarchy |
|-------|--------------|
| PersonShape | `orcid` → `infosciencePersonIdentifier` → `githubUsername` → `uuid` |
| RepositoryShape | `githubRepositoryHandle` → `doi` → `uuid` |
| OrganizationShape | `ror` → `infoscienceOrganizationIdentifier` → `githubOrganizationHandle` → `uuid` |
| MembershipShape | `composite (personId_orgId)` → `uuid` |
| ContributionShape | `composite (personId_repoId)` → `uuid` |
| ArticleShape | `doi` → `infoscienceArticleIdentifier` → `uuid` |

## Validation Patterns Reference

| Identifier | Pattern | Example |
|------------|---------|---------|
| ORCID | `^\d{4}-\d{4}-\d{4}-\d{3}[0-9X]$` | `0000-0001-2345-6789` |
| DOI | `^10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+$` | `10.1038/s41586-024-07856-z` |
| ROR | `^https://ror\.org/[0-9a-z]{9}$` | `https://ror.org/02s376052` |
| UUID4 | `^[0-9a-f]{8}-[0-9a-f]{4}-4[0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$` | `f97b60da-bcab-4f2e-ba12-0ee0c4d0d6eb` |
| GitHub Handle | `^[a-zA-Z0-9\-_]+/[a-zA-Z0-9\-_\.]+$` | `EPFL-ENAC/geodata-toolkit` |
| Email | `^[\w\-\.]+@([\w-]+\.)+[\w-]{2,4}$` | `carlos.vivar@epfl.ch` |
| Wikidata IRI | `^wd:Q\d+$` | `wd:Q21201` |
| ISO 8601 Date | `^\d{4}-\d{2}-\d{2}$` | `2025-06-15` |
| ISO 8601 DateTime | `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$` | `2023-03-15T10:30:00Z` |

## Enumeration Values

### Repository Types (`pulse:repositoryType`)
- `pulse:Software`
- `pulse:Data`
- `pulse:Documentation`
- `pulse:EducationalResource`
- `pulse:Other`

### Organization Types (`pulse:OrganizationType`)
- `pulse:University`
- `pulse:ResearchInstitution`
- `pulse:GovernmentAgency`
- `pulse:SoftwareProject`
- `pulse:PrivateCompany`
- `pulse:NonProfitOrganization`
- `pulse:CommunitySpace`
- `pulse:OtherOrganizationType`

## Cross-Reference Patterns

Cross-references between shapes use the **hierarchical ID** of the target shape (the resolved `id` field, which follows each shape's ID hierarchy strategy).

| Source Shape | Reference Field | Target Shape | Target's ID Hierarchy |
|--------------|-----------------|--------------|----------------------|
| PersonShape | `org:hasMembership` | MembershipShape | `composite` → `uuid` |
| PersonShape | `pulse:hasContribution` | ContributionShape | `composite` → `uuid` |
| PersonShape | `pulse:owns` | RepositoryShape | `githubHandle` → `doi` → `uuid` |
| RepositoryShape | `schema:author` | PersonShape | `orcid` → `infosciencePersonIdentifier` → `githubUsername` → `uuid` |
| RepositoryShape | `pulse:ownedBy` | PersonShape/OrganizationShape | Depends on target shape |
| OrganizationShape | `org:hasUnit` / `org:unitOf` | OrganizationShape | `ror` → `infoscienceOrganizationIdentifier` → `githubOrganizationHandle` → `uuid` |
| OrganizationShape | `pulse:owns` | RepositoryShape | `githubHandle` → `doi` → `uuid` |
| MembershipShape | `org:organization` | OrganizationShape | `ror` → `infoscienceOrganizationIdentifier` → `githubOrganizationHandle` → `uuid` |
| ContributionShape | `pulse:contributionTo` | RepositoryShape | `githubHandle` → `doi` → `uuid` |
| ContributionShape | `schema:author` | PersonShape | `orcid` → `infosciencePersonIdentifier` → `githubUsername` → `uuid` |
| ArticleShape | `schema:author` | PersonShape | `orcid` → `infosciencePersonIdentifier` → `githubUsername` → `uuid` |
| ArticleShape | `schema:sourceOrganization` | OrganizationShape | `ror` → `infoscienceOrganizationIdentifier` → `githubOrganizationHandle` → `uuid` |

> **Note:** Cross-references always use the target's resolved `id` value. For example, an ArticleShape's `schema:author` could be an ORCID (`0000-0001-2345-6789`), a GitHub username (`mweber`), or a UUID—depending on which identifier is available for that person.
