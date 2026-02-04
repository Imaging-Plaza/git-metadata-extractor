# Option A - Hierarchical ID Resolution

This folder contains mock JSON files representing **Option A**: hierarchical ID resolution strategy for Open Pulse Ontology v2.0.0.

## ID Hierarchy Strategy

Each shape uses a prioritized hierarchy of identifiers. The `id` field is set to the first available identifier in the hierarchy, and `idSource` indicates which identifier was used.

### PersonShape
```
orcid → infosciencePersonIdentifier → githubUsername → uuid
```
| Priority | Identifier | Example |
|----------|------------|---------|
| 1 | ORCID | `0000-0001-2345-6789` |
| 2 | Infoscience Person ID | `f97b60da-bcab-4f2e-ba12-0ee0c4d0d6eb` |
| 3 | GitHub Username | `mweber` |
| 4 | UUID (fallback) | `a1b2c3d4-5e6f-4a7b-8c9d-0e1f2a3b4c5d` |

### RepositoryShape
```
githubHandle → doi → uuid
```
| Priority | Identifier | Example |
|----------|------------|---------|
| 1 | GitHub Handle | `EPFL-ENAC/geodata-toolkit` |
| 2 | DOI | `10.5281/zenodo.12345678` |
| 3 | UUID (fallback) | `b1c2d3e4-5f6a-4b7c-8d9e-0f1a2b3c4d5e` |

### OrganizationShape
```
ror → infoscienceOrganizationIdentifier → githubOrganizationHandle → uuid
```
| Priority | Identifier | Example |
|----------|------------|---------|
| 1 | ROR | `https://ror.org/02s376052` |
| 2 | Infoscience Org ID | `95372c6b-7d45-432e-a84e-660c9fa54e05` |
| 3 | GitHub Org Handle | `numpy` |
| 4 | UUID (fallback) | `e2c4b6a8-1d3f-4e5a-9b7c-8d6e4f2a1b3c` |

### ArticleShape
```
doi → infoscienceArticleIdentifier → uuid
```
| Priority | Identifier | Example |
|----------|------------|---------|
| 1 | DOI | `10.1038/s41586-024-07856-z` |
| 2 | Infoscience Article ID | `f97b60da-bcab-4f2e-ba12-0ee0c4d0d6eb` |
| 3 | UUID (fallback) | `f1a2b3c4-5d6e-4f7a-8b9c-0d1e2f3a4b5c` |

### MembershipShape
```
composite (personId_orgId) → uuid
```
| Priority | Identifier | Example |
|----------|------------|---------|
| 1 | Composite | `0000-0001-2345-6789_https://ror.org/02s376052` |
| 2 | UUID (fallback) | `c1a2b3c4-5d6e-4f7a-8b9c-0d1e2f3a4b5c` |

### ContributionShape
```
composite (personId_repoId) → uuid
```
| Priority | Identifier | Example |
|----------|------------|---------|
| 1 | Composite | `0000-0001-2345-6789_EPFL-ENAC/geodata-toolkit` |
| 2 | UUID (fallback) | `d1a2b3c4-5e6f-4a7b-8c9d-0e1f2a3b4c5d` |

## Files

- `pulse_PersonShape.json` - 4 persons
- `pulse_RepositoryShape.json` - 3 repositories
- `pulse_OrganizationShape.json` - 5 organizations
- `pulse_MembershipShape.json` - 5 memberships
- `pulse_ContributionShape.json` - 7 contributions
- `pulse_ArticleShape.json` - 3 articles
