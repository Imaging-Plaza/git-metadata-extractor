# Mapping and ontology maintenance

The [visual field guide](../guide/index.html) introduces the architecture,
source families and extraction sequence. This page is the searchable companion
for changing mappings and ontology contracts. The authoritative detailed guide
is [`mapping/config/README.txt`](https://github.com/Imaging-Plaza/git-metadata-extractor/blob/feat/2026-11-refactor/git_metadata_extractor/mapping/config/README.txt).

## The contracts are separate

The HTTP API is `/v2`. Default canonical output uses the **pinned v3 development
ontology**, whose generated context identifies `v3.0.0-develop`. This does not
mean that a stable v3 ontology release has been published. The source is the
immutable gitlink at `vendor/open-pulse-ontology`, with approved changes in
`ontology/patches/`. Older agent JSON Schemas remain a separate contract.

Generation and post-processing also have distinct owners:

| Responsibility | Source of truth |
|---|---|
| Request, cache and extraction lifecycle | `services/extraction.py` |
| Input-specific generation, fan-out and retries | `pipeline/orchestrator.py`, `PLAN_BY_TYPE` |
| Ordered post-agent chains and runtime gates | `pipeline/run.py` |
| Field transforms and typed mapping/policy checks | `mapping/` |
| Source/entity mappings and shared policies | `mapping/config/` |
| Layer-specific validation | `validation/layers.py` |

All paths in this table are relative to `git_metadata_extractor/`.

## Where a mapping change belongs

Adapters fetch, normalize and match source records in Python. Source mappings
enrich fields; agent mappings build payloads from prepared facts. Python stages
reconcile these payloads before raw and canonical projection mappings read the
same intermediate. Optional raw projection runs first because canonical
projection replaces that intermediate.

| Configuration location | Owns |
|---|---|
| `agent/*.yaml` | The six rule-based entity payload builders |
| `github/`, `orcid/`, `ror/`, `infoscience/` | Source-specific enrichment |
| `raw/`, `canonical/` | Projection fields for their generated contracts |
| `policies/ontology.yaml` | Routing, passthrough, IRI coercion and source attribution |
| `policies/extraction.yaml` | Classification keywords, defaults and auxiliary filenames |
| `policies/merge.yaml` | Store-side identity matching and merge dispositions |

These paths are inside `git_metadata_extractor/mapping/config/`. The files ship
in the Python wheel; do not create another copy in repository-level `config/`.
YAML does not fetch sources, add pipeline stages or execute arbitrary Python.
LLM prompts, tools and hybrid refiner logic remain Python concerns.

## A mapping recipe

This is an excerpt from `raw/repository.yaml`, not a complete replacement:

```yaml
schema_version: 1
name: raw.repository
version: '2'
source_contract: extractor.v2.entity
target_contract: raw.repository
fields:
  pulse:watcherCount:
    sources: [_subscribers_count]
  schema:url:
    sources: [_homepage]
    transform: web_iri
```

GitHub `subscribers_count` means watchers, while `watchers_count` means stars.
GitHub repository `size` is KB, not a file count. Fixtures must prove source
semantics as well as output datatypes.

Each field declares exactly one of `sources` or `value`. The first nonempty
source wins. Zero and false are data; `None` is missing. Empty strings, lists
and dictionaries are normally missing unless `empty: preserve` is set.
Missing outputs are omitted unless `missing: 'null'` is explicit. Dot paths
traverse nested objects; parent/child target collisions are rejected.

The `web_iri` transform requires an explicit HTTP(S) URL, validates it and
creates an `@id` reference; invalid links and bare domains are omitted.
Unknown transforms, duplicate YAML keys and fields absent from generated
target contracts fail validation. Internal fields are permitted only on agent
contracts and must start with `_`; output boundaries strip this metadata.

1. Edit the owning YAML and bump its version string.
2. Add a source-record or behavior test that demonstrates the intended meaning.
3. Validate mappings and run focused tests in the project environment:

   ```bash
   python -m git_metadata_extractor.mapping --check
   python -m pytest tests/v2/test_yaml_mappings.py tests/v2/test_full_yaml_migration.py
   ```

4. Run affected agent, projection or unifier tests, plus relevant model checks.
5. Restart the extractor. Parsed configuration, fingerprints and module-level
   policy views are cached for the process lifetime; there is no hot reload.

Inspect the current inventory and aggregate fingerprint with
`python -m git_metadata_extractor.mapping`. Use `--schema` for the mapping and
policy JSON Schema used by editors. Keep the inventory authoritative rather
than maintaining another complete field list in documentation.

## Updating the ontology or schemas

For raw/canonical changes, update the vendor pin and approved patches together.
Use the repository tooling to prepare the ontology and regenerate contracts:

```bash
just ontology-models-generate
just ontology-models-check
python -m git_metadata_extractor.mapping --check
```

The generation recipe prepares the pinned ontology before generating models.
Update generated contracts, YAML mappings and semantic fixtures in the same
change. Run affected projection, unifier and validation tests. A target-property
check proves that a property exists; JSON Schema and SHACL still check values,
cardinalities and graph relationships. The extraction SHACL gate is warning-only;
store validation has layer-specific policy. Inspect `validation/layers.py`
before changing enforcement behavior.

Agent and strict contracts are generated from the authoritative files under
`git_metadata_extractor/schema/json/agent/` and `schema/json/strict/`.
The current generator reads those sources directly. Files under
`tests/v2/fixtures/schema/` are example payloads, not duplicate JSON Schemas;
the historical promoted-copy directory is absent from this checkout. Do not
recreate the obsolete three-copy workflow.

After a schema edit, run `just v2-models-generate` and `just v2-models-check`.
Update affected example payloads and behavior tests as needed. The generator
and formatter versions are pinned because generated models are compared
byte-for-byte.

The [historical v3 proposal](../releases/ontology-v3.0.0.md) is a preserved design
artifact, not the contract for the current pinned v3 models.

## Source records, vectors and models

The extraction service reads index state managed by the separate
[open-pulse-sources](https://github.com/sdsc-ordes/open-pulse-sources) service.
The installed library pin is `v0.1.2`; the matching Compose writer image is
`0.1.2`. Library reads still use shared Qdrant and DuckDB stores. Optional HTTP
search/manifest access does not imply that record hydration is entirely remote.

Qdrant supports vector similarity search. DuckDB stores provide indexed records
that can be fetched for their actual fields. Embeddings help find candidates;
they do not replace the source records. An embedding model encodes text for
retrieval; an extraction LLM reasons over supplied context and tools to produce
structured entities. Their configuration and costs are separate concerns.

Current Compose shares the `gme-data` volume for records and uses Qdrant for
vectors. S3 is not the implemented storage boundary described here. Index
ingest, embed and reset operations belong to the sources service. See the
[cross-repo contract](../cross-repo-contract.md) before changing the library pin
or writer image; they must stay aligned.

## Keep the output layers distinct

| Layer | Purpose |
|---|---|
| Canonical output | Consumer-facing entity graph in the projected ontology shapes |
| Raw substrate | Optional source-attributed raw entities grouped in named graphs |
| Extraction descriptor | `pulse:ExtractionRun` and software-agent context for a run |
| Execution evidence | Activities, retrievals, citations and mapping identities |
| Store selection provenance | Unifier merge/selection decisions in `graph:prov` |
| Run report | Optional compact summary of observed execution |

Mapping activities record name, version, source/target contracts and the
aggregate fingerprint, which also includes policies, generated target schemas
and mapping engine code. Policies do not emit separate execution activities.
The fingerprint identifies content; it does not archive an old configuration
or input record. A recorded mapping activity does not establish that every
output field has a checked source citation.

## Observing an extraction

Add `"include_run_report": true` to a `POST /v2/extract` body, or
`include_run_report=true` to the synchronous GET query. This is opt-in; it does
not start extra provider work. The resulting `run_report` is a response sibling
of `output`, not metadata inserted into canonical entity nodes.

```json
{
  "source_url": "https://github.com/owner/repository",
  "agent_runtime": "rule_based",
  "include_run_report": true
}
```

The report describes stages in observed order, including repeated stage names,
outcomes and durations; activities and retrievals; validation summaries;
entity/property change counts; usage subtotals; and attribution counts.
It excludes raw entity values from change summaries. Runtime names describe a
configuration, while the report records what actually executed:

- `rule_based` uses deterministic generators and stays LLM-free.
- `hybrid` adds typed refiners and enabled bio-affiliation/GitHub-parent LLM
  resolution. LLM-only dedup, critic, veracity and organization relationships
  remain skipped.
- `llm` runs full per-entity model agents and enabled LLM-only stages.

For a pipeline cache hit, report status is `cache_hit`: there are no new stage
executions or model calls to invent. `request_run_id` and `output_run_id` can
identify different runs. Historical evidence is distinguished from current
activity. Incomplete usage totals are known subtotals, not total cost estimates.

Retrieval proves access, not derivation. Attribution counts recorded trace
claims, not final-graph coverage or factual correctness. Stage changes compare
boundaries and omit intermediate mutations. Consult the report's `limitations`
and the typed definitions in `observation/run_report_models.py` when interpreting it.
