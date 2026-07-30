# Infoscience API Findings

This page captures implementation-level findings reflected in `src/context/infoscience.py`.

## Effective API base

- `https://infoscience.epfl.ch/server/api`

## Practical endpoint behavior used by this project

1. `GET /discover/search/objects` with `configuration=researchoutputs`
- Reliable for publication search and repository-name matching.

2. `GET /discover/search/objects` with `configuration=person`
- Preferred path for person profiles.
- Fallback to publication author metadata when no person profile is returned.

3. `GET /discover/search/objects` with `configuration=orgunit`
- Preferred path for labs/orgunits.
- Fallback to publication affiliation metadata when no orgunit record is returned.

4. `GET /entities/{entity_type}/{uuid}` and `GET /core/items/{uuid}`
- Used for direct UUID resolution.

## Normalization rules implemented

- Publication UUID -> `https://infoscience.epfl.ch/entities/publication/{uuid}`
- Person UUID -> `https://infoscience.epfl.ch/entities/person/{uuid}`
- Orgunit UUID -> `https://infoscience.epfl.ch/entities/orgunit/{uuid}`

## Failure handling

- HTTP and timeout errors are logged and converted to safe empty result structures.
- Parsing errors for individual result items are skipped; remaining items still return.
- Agent tools include local in-memory search caching to avoid repeated identical queries.

## Current limitation boundary

- Infoscience retrieval is integrated end-to-end for repository-, user-, and organization-level analysis.
- In repository flow, only repository-level linked entities are currently assigned automatically; author-level relation assignment remains a follow-up path (`run_author_linked_entities_enrichment` contains TODO parsing logic).
