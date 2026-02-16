# Pydantic to JSON-LD Mapping

This page summarizes key mappings used by `PYDANTIC_TO_ZOD_MAPPING` in `src/data_models/conversion.py`.

## Core model mappings

## `SoftwareSourceCode`

- `name` -> `schema:name`
- `description` -> `schema:description`
- `codeRepository` -> `schema:codeRepository`
- `author` -> `schema:author`
- `repositoryType` -> `pulse:repositoryType`
- `discipline` -> `pulse:discipline`
- `relatedToOrganizations` -> `pulse:relatedToOrganization`
- `linkedEntities` -> `pulse:linkedEntities`

## `Person`

- `name` -> `schema:name`
- `emails` -> `schema:email`
- `githubId` -> `schema:username`
- `orcid` -> `md4i:orcidId`
- `affiliations` -> `schema:affiliation`
- `linkedEntities` -> `pulse:linkedEntities`

## `Organization`

- `legalName` -> `schema:legalName`
- `hasRorId` -> `md4i:hasRorId`
- `organizationType` -> `schema:additionalType`
- `attributionConfidence` -> `pulse:confidence`

## `GitHubUser` and `GitHubOrganization`

- Metadata fields map under `pulse:metadata`
- EPFL assessment fields map to `pulse:relatedToEPFL`, `pulse:confidence`, `pulse:justification`

## Linked-entities mappings

- Relation model includes `catalogType`, `entityType`, `confidence`, `justification`
- Infoscience-specific entity models map publication/person/orgunit details into schema/pulse fields.

## Notes

- The map name is historical (`PYDANTIC_TO_ZOD_MAPPING`) but is used by both JSON-LD and Zod-compatible conversion paths.
- If you add fields to models, update this mapping and associated tests/conversion checks.
