# Sources service contract

The extractor and `open-pulse-sources` are separate processes and separate
Git repositories. The extractor calls sources over authenticated HTTP; it no
longer installs its Python package or opens its index files.

| Responsibility | Extractor | Sources |
| --- | --- | --- |
| Graph construction | Agents, ontology mappings, reconciliation, validation, provenance | Source facts only |
| Retrieval | Typed HTTP adapters and tool-facing contracts | Search, filters, scopes, hydration, lineage, federated lookup |
| Index operations | Opt-in job submission | Ingest, embedding, reranking, DuckDB/Qdrant storage, bootstrap |
| Configuration | Pipeline/benchmark profiles, API settings, ontology mappings | Index YAML, embedding models, collections, source scopes |
| Local state | Extraction/provider cache and outputs | Index files and source service cache/jobs |

## Local checkout and deployment

The independent sources checkout lives at `dev/open-pulse-sources`. It is
ignored by this Git repository and excluded from the extractor image's build
context. Work there is committed separately; never stage it as a nested Git
repository or copy its implementation back into the extractor.

The current development pairing is the extractor refactor branch and the
sources `feat/extractor-service-boundary` branch (`0.2.0.dev0`). This is local
unpublished work. Published sources `0.1.2` does not provide the new retrieval
and auxiliary routes and must not be substituted for it.

```bash
docker compose -f tools/deploy/docker-compose.yml build gme-api gme-sources
docker compose -f tools/deploy/docker-compose.yml up -d
```

The development container also builds sources from that checkout. A fresh
clone needs the matching sources branch placed there before building the
stack. A standalone extractor install needs only its own Python dependencies.

Compose keeps the existing `gme-data` volume attached to sources and assigns a
new `gme-api-data` volume to the extractor. Existing index contents are neither
reset nor copied. The extractor cache path is `/app/data/cache/providers.db`.
For devcontainer use, sources mounts the existing `data/index` directory.

Sources runs with **one worker**: DuckDB writes and asynchronous job locks are
process-local. Moving index access into a service removes competing extractor
writers; it does not make multi-process DuckDB writes safe.

## Connection settings

| Extractor variable | Meaning |
| --- | --- |
| `SOURCES_API_URL` | Service root, e.g. `http://gme-sources:8080` |
| `SOURCES_API_TOKEN` | Bearer credential matching the sources service's `API_TOKEN` |
| `SOURCES_API_TIMEOUT_SECONDS` | HTTP timeout, default 30 seconds, maximum 300 |

Compose uses `SOURCES_API_TOKEN`, falling back to `API_TOKEN` if no separate
credential is supplied. Source-specific tokens, Qdrant URLs, index paths and
embedding settings belong on sources. Missing configuration or an unavailable
service yields explicit disabled/unavailable retrieval and stage warnings;
there is no local-library fallback.

## HTTP surfaces

Existing `/v2/manifest` and `/v2/indices/*` management/search routes remain.
The new operations preserve the extractor's retrieval behavior:

- `/v2/retrieval/{provider}/{method}`: bounded, typed provider search,
  hydration, lineage, federated lookup and SNSF grant queries. Requests retain
  scopes, filters and explicit rerank controls; results retain existing
  tool-facing shapes.
- `/v2/extractor/*`: fixed source operations for discipline ancestry, exact
  Infoscience acronym and Zenodo-community lookup, EPFL concept annotation,
  related research facts, and GitHub ensure-indexed job submission. No route
  accepts arbitrary SQL or an arbitrary callable name.

All new routes require bearer authentication. Source exceptions are sanitized
before returning across the boundary. Consult the sources OpenAPI `/docs` for
request models and individual operation names.

An extraction auto-ingest flag authorizes **submission**, not completed work.
The source service checks whether the record is already indexed, owns the
write/embedding lifecycle, and exposes job status through its job API.

## Provenance and verification

Successful responses identify the actual running sources build. The extractor
records that metadata within the extraction scope, including concurrent agent
calls. Missing or conflicting versions produce no invented source snapshot;
no installed-library version is used. The build identifies service software,
not an immutable export of every upstream record.

The boundary is guarded by `tests/v2/test_sources_boundary.py`: no sources,
DuckDB, Qdrant or graph-index implementation imports, no index-library package
dependency, and API startup with those imports blocked. HTTP adapter tests
cover request/response contracts, failure handling and version-scope isolation.
Sources tests cover migrated retrieval implementations and authenticated
operation routes using fake or temporary backends.
