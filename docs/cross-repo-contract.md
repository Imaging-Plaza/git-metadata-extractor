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
sources `feat/2026-11-refactor` branch (`0.2.0.dev1`). This is local
unpublished work. Published sources `0.1.2` does not provide the new retrieval
and auxiliary routes and must not be substituted for it.

```bash
just sources-setup   # fetch the exact commit in config/services/sources.lock.json
just sources-check   # offline verification; no changes to an existing checkout
docker compose -f tools/deploy/docker-compose.yml build gme-api gme-sources
just sources-smoke   # exercise both built images with temporary fixtures
docker compose -f tools/deploy/docker-compose.yml up -d
```

The setup/check commands require only Python 3.10+ and Git. Without `just`, run
`python scripts/dev/sources_checkout.py` (add `--check` for offline verification).
A new checkout uses a detached HEAD at the recorded commit. Existing checkouts
are validated without switching branches or discarding work; a dirty checkout,
different origin or revision fails with an explanation. Create a feature branch
in sources when developing there, and deliberately update the lock after its
changes are tested. The commands never update the lock automatically.

The development container builds sources from that checkout too: run setup on
the host before opening it. A standalone extractor install needs only its own
Python dependencies. A fresh remote checkout **cannot yet fetch the local-only
sources commit**; publication is a prerequisite, not something setup performs.

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

## Sources MCP alongside HTTP

The local sources development build also mounts a curated read-only MCP
catalog at `http://gme-sources:8080/mcp/` on its existing FastAPI process. It
uses the sources `API_TOKEN` as a static bearer credential, with no additional
service, port or OAuth login flow. Successful calls carry structured content
`{data, service_version}`. Index contents, model settings and upstream
credentials remain source-owned; MCP does not create or embed indices itself.

The catalog covers indexed search/hydration, direct source lookups and bounded
source facts. It exposes no ingest/reset/ensure-indexed operations. Direct
GitHub repository metadata comes from REST, not GME's gimie-derived JSON-LD.
Context/ontology tools, repository corpus grep, graph-neighbor queries, email
hashing and generic web/browser tools remain in GME. Existing GME agent tools
and HTTP adapters remain unchanged; adopting MCP in GME is a separate change.
See the local sources guide at `dev/open-pulse-sources/docs/mcp.md`
for client configuration and extension instructions.

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

## Automated checks and release ordering

`config/services/sources.lock.json` records the repository, full tested commit
and reported service version. It is a development/test compatibility lock, not
a dependency installed into GME. CI's reusable `sources-contract.yml` workflow:

1. Tests the safe checkout command and fetches that exact sources commit.
2. Installs both packages in a dedicated contract-test environment and runs
   `tests/contracts/test_sources_http_contract.py`. A missing sources import
   fails the job rather than silently skipping those tests.
3. Builds each production image separately and runs
   `python scripts/dev/smoke_sources.py` on an internal Docker network.

The smoke creates a temporary DuckDB fixture inside sources, checks authenticated
HTTP lookup and the reported version, and exercises mock rule-based extraction.
It checks that the extractor image contains no sources/DuckDB/Qdrant libraries.
There are no host ports, shared data volumes, upstream APIs or model calls;
only containers/network created for that invocation are removed afterward.

The ordinary extractor test job still installs only GME. Image publication also
requires the paired-service job, and labels the GME image with
`ch.sdsc.pulse.sources-contract-revision`. This label describes the tested remote
service, not code embedded in the extractor image.

Release in this order:

1. Publish the reviewed sources commit/branch. Its current local revision is
   recorded in the lock. Until that commit is fetchable, remote contract CI
   intentionally fails; it never substitutes `main` or the incompatible 0.1.2.
2. For a stable release, set the sources version and lockfile together, run its
   CI, then update GME's compatibility lock to that tested commit/version.
3. Require the GME contract workflow to pass against the published revision.
   Publish the matching sources image and record its immutable registry digest
   in deployment configuration (`SOURCES_IMAGE`). Retain single-worker operation.
4. Publish/deploy the paired GME image only after those checks pass. Configure
   `GME_IMAGE` and `SOURCES_IMAGE` explicitly for deployment; the checked-in
   Compose defaults intentionally remain local development images.

No registry digest or stable release is claimed for the current local branch.
Preparing this configuration does not publish either repository or image.
