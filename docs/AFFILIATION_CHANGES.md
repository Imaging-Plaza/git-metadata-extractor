# Affiliation Model Notes

This document reflects the current affiliation representation used in the v2 codebase.

## Canonical affiliation object

`src/data_models/models.py` defines:

- `Affiliation.name`
- `Affiliation.organizationId`
- `Affiliation.source`

`Person.affiliations` is a list of these objects.

## Source provenance values in use

Typical values include:

- `gimie`
- `orcid`
- `agent_org_enrichment`
- `agent_user_enrichment`
- `github_profile`
- `email_domain`

## Where affiliations are populated

- Repository flow:
  - ORCID enrichment in `Repository.run_authors_enrichment`
  - optional user and organization enrichment stages
- User flow:
  - LLM + enrichment paths in `src/analysis/user.py`
- Organization flow:
  - organization enrichment in `src/analysis/organization.py`

## Data quality and privacy notes

- ORCID values are normalized/validated in models.
- Person and GitAuthor emails are anonymized by model validators/utilities before output persistence.

## Migration note

Legacy flat string affiliation assumptions should be updated to consume structured `Affiliation` entries with provenance.
