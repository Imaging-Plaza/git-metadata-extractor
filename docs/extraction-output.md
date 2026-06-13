<!--
Sources (verify against these):
- src/v2/api_models/contracts.py  (V2ExtractResponse, V2Stats, V2JSONLDOutput, V2JSONOutputEnvelope, V2ExtractRequest)
- src/v2/api.py                   (extract endpoint, verify_token dep, V2ExtractResponse assembly ~L1784, output_format branch ~L1718)
- src/v2/pipeline/stages/jsonld_build.py   (build_jsonld_output: @context + @graph + excluded_entities; gme-internal: rename; include_internal_fields)
- src/v2/pipeline/stages/output_assembly.py (build_json_output: root_entity/related_entities/excluded_entities/entities_by_type)
- src/v2/pipeline/stages/stats.py  (compute_stats: entities_count, triples_count, run_id, duration_ms, stages_completed)
- src/v2/pipeline/stages/reconciliation.py (canonical composite ids: personId__orgId / personId__repoId, double underscore)
- src/v2/schema/json/strict/*.schema.json  (entity contracts)
- src/v2/schema/json/context/v2.0.jsonld   (@context, context_version 2.1.2)
- docs/repository-enrichment-fields.md, docs/v2-pipeline.md, docs/releases/ontology-v3.0.0.md
-->

# Understanding extraction output

A field-by-field guide to what `GET /v2/extract/{path}` returns and how to consume it.

`GET /v2/extract/{path}` runs the full pipeline and returns a single JSON object: a wrapper with run metadata around a graph of entities aligned to the [Open Pulse Ontology](https://sdsc-ordes.github.io/open-pulse-ontology/). This page explains the wrapper, the two output shapes (`jsonld` and `json`), the entity types and their fields, and the opt-in `gme-internal:*` enrichment layer.

!!! note "Authentication"
    Every `/v2/extract` request needs `Authorization: Bearer <token>`. Only `GET /v2/health` is open. See [v2-api-reference.md](v2-api-reference.md) for the full endpoint list and request parameters.

!!! note "GET runs synchronously; POST enqueues a job"
    `GET /v2/extract/{path}` is the synchronous call documented here — it runs the pipeline and returns the `V2ExtractResponse` envelope below. `POST /v2/extract` is the **async** variant: it takes a JSON body, returns `202 Accepted` with a `job_id` to poll, and the finished `V2ExtractResponse` shows up at the job-result endpoint. The envelope is identical either way.

## The response envelope

The top level is always the same object, regardless of output format:

| Key | Type | Meaning |
|---|---|---|
| `source_url` | string | The normalized GitHub URL that was extracted. |
| `detected_type` | `"repository"` \| `"user"` \| `"organization"` | What the classifier resolved the URL to. |
| `output_format` | `"jsonld"` \| `"json"` | Echoes the requested shape (default `jsonld`). |
| `output` | object | The entity graph. Shape depends on `output_format` — see below. |
| `context_summary_markdown` | string \| `null` | The scout's LLM context brief. Present only when you pass `?include_context_summary=true`; otherwise omitted. |
| `warnings` | string[] | Non-fatal notes from the run (dropped entities, validation issues, link checks). Often empty `[]`. |
| `stats` | object | Run metadata — see [Run metadata](#run-metadata-stats). |

!!! note "Omitted vs null"
    The endpoint serializes with `response_model_exclude_none=True`, so `null`/`None` fields are dropped from the JSON entirely rather than sent as `null`. If you don't see `context_summary_markdown`, it was `null`.

### Run metadata (`stats`)

The `stats` object (`V2Stats`) carries everything about the run itself:

| Field | Type | Meaning |
|---|---|---|
| `entities_count` | integer | Number of typed entities in the output graph. |
| `triples_count` | integer | RDF triple count of the assembled graph (0 for the flat `json` format, which is not loaded into RDF). |
| `run_id` | string | UUID for this extraction. Quote it in bug reports — server logs key off it. |
| `duration_ms` | integer | Wall-clock pipeline duration in milliseconds. |
| `stages_completed` | string[] | Names of the pipeline stages that ran (see [v2-pipeline.md](v2-pipeline.md)). |

## Two output shapes

You choose the shape with `?output_format=` (default `jsonld`).

### `jsonld` (default) — an RDF-ready graph

`output` is a JSON-LD document with three keys:

| Key | Meaning |
|---|---|
| `@context` | Prefix and type mappings (Open Pulse `@context`, version 2.1.2). |
| `@graph` | Array of entity nodes, each keyed by `@id` / `@type`. |
| `excluded_entities` | Present only when entities were dropped by strict validation; each record wraps the rejected `entity` (plus its `entity_type`) and the `reason`. Omitted when nothing was excluded. |

This loads directly into any RDF triplestore. Node `@id`s are real IRIs (an ORCID, a ROR, a GitHub URL) or a minted `urn:pulse:<id>` when no public identifier exists.

### `json` — a flat, grouped envelope

Pass `?output_format=json` to get a plain object instead — no `@context`, no IRI expansion:

| Key | Meaning |
|---|---|
| `root_entity` | The primary entity for the URL (the repo, person, or org), or `null`. |
| `related_entities` | All other entities discovered in the run. |
| `excluded_entities` | Entities rejected by strict validation. |
| `entities_by_type` | The same entities bucketed under fixed keys: `repositories`, `persons`, `organizations`, `articles`, `memberships`, `contributions`. |

Use `json` when you want to walk entities by kind without an RDF library. Use `jsonld` (the default) when you intend to load the result into a graph store. The entity bodies (`schema:name`, `identifiers`, etc.) are identical between the two; only the wrapper differs.

## Entity types

The graph contains up to six entity types, each validated against a strict JSON Schema (`src/v2/schema/json/strict/`). Every entity shares the same skeleton:

- `id` — the resolved identifier (also the node `@id` in JSON-LD).
- `type` — the ontology class (table below).
- `shacl` — the SHACL shape name it conforms to.
- `idSource` — which identifier in the hierarchy `id` came from.
- `identifiers` — an object holding each candidate identifier (always includes a `uuid` fallback).

!!! note "The `idSource` / identifier hierarchy"
    Each entity has an ordered identifier hierarchy; the first non-null one wins and becomes both `id` and `idSource`. `uuid` is the last-resort fallback and is always present in `identifiers`. For example a Person resolves `pulse:orcid → pulse:infosciencePersonIdentifier → pulse:githubUsername → uuid`.

| `type` | `shacl` | Schema | Identifier hierarchy (`idSource`) |
|---|---|---|---|
| `schema:Person` | `pulse:PersonShape` | `person.schema.json` | `pulse:orcid` → `pulse:infosciencePersonIdentifier` → `pulse:githubUsername` → `uuid` |
| `schema:SoftwareSourceCode` | `pulse:RepositoryShape` | `repository.schema.json` | `pulse:githubRepositoryHandle` → `schema:citation` (DOI) → `uuid` |
| `org:Organization` | `pulse:OrganizationShape` | `organization.schema.json` | `pulse:ror` → `pulse:infoscienceOrganizationIdentifier` → `pulse:githubOrganizationHandle` → `uuid` |
| `schema:ScholarlyArticle` | `pulse:ArticleShape` | `article.schema.json` | `schema:identifier` (DOI) → `pulse:infoscienceArticleIdentifier` → `uuid` |
| `pulse:Contribution` | `pulse:ContributionShape` | `contribution.schema.json` | `pulse:composite` (`personId__repoId`) → `uuid` |
| `org:Membership` | `pulse:MembershipShape` | `membership.schema.json` | `pulse:composite` (`personId__orgId`) → `uuid` |

!!! note "Composite ids use a double underscore"
    Membership and Contribution `id`s are composites of the two entity ids they link, joined with a **double** underscore (`__`): `{personId}__{orgId}` and `{personId}__{repoId}`. The double separator is deliberate — each half is itself a full IRI (or a handle that may contain a single `_`), so `__` lets the composite be split back into its halves unambiguously.

### `schema:SoftwareSourceCode` — the repository

The central entity for a repository extraction. Required: `schema:name`, `pulse:githubRepositoryHandle`, and at least one `schema:author` (a reference to a Person `id`). Selected optional fields:

| Field | Type | Notes |
|---|---|---|
| `pulse:githubRepoStars`, `pulse:githubRepoForks` | integer \| null | Star / fork counts. |
| `schema:dateCreated` | string \| null | ISO 8601 datetime (`...Z`). |
| `schema:license` | string (IRI) \| null | SPDX license IRI. |
| `schema:citation` | string (IRI) \| null | DOI URL. |
| `schema:programmingLanguage` | string[] | Languages. |
| `pulse:repositoryType` | enum | One of `pulse:Software`, `pulse:Data`, `pulse:Documentation`, `pulse:EducationalResource`, `pulse:Other`. |
| `pulse:discipline` | string[] | Wikidata discipline IRIs (`wd:Q...`). |
| `pulse:ownedBy` | string \| null | Reference to the owning Person or Organization `id`. |
| `pulse:isForkOf` | string \| null | Parent repository handle, if a fork. |

### `schema:Person`

Required: `schema:name`, plus **at least one** of `pulse:githubUsername`, `schema:email`, `pulse:infosciencePersonIdentifier`, or `pulse:orcidIdentifier`. Cross-reference fields point at other entities by `id`:

| Field | Type | Notes |
|---|---|---|
| `schema:email` | string | Validated email. |
| `schema:url` | string (IRI) \| null | Profile / homepage. |
| `org:hasMembership` | string[] | References to `org:Membership` ids (`personId__orgId`). |
| `pulse:hasContribution` | string[] | References to `pulse:Contribution` ids (`personId__repoId`). |
| `pulse:owns` | string[] | Repository handles this person owns. |

!!! warning "No `schema:affiliation` on a Person"
    Affiliations are never stamped directly on a Person. They are modelled as a separate `org:Membership` entity linking the Person to an Organization. See [v2-pipeline.md](v2-pipeline.md#the-affiliation-strategy).

### `org:Organization`

Required: `schema:name`, plus **at least one** of `schema:identifier` (ROR), `pulse:githubOrganizationHandle`, or `pulse:infoscienceOrganizationIdentifier`. Optional fields include `pulse:OrganizationType` (e.g. `pulse:University`, `pulse:PrivateCompany`, `pulse:NonProfitOrganization`), `pulse:githubOrgFollowers`, and the hierarchy links `org:hasUnit` / `org:unitOf` / `pulse:owns`.

### `schema:ScholarlyArticle`

A linked publication. Required: `schema:name` (title), `schema:identifier` (a DOI URL), `schema:datePublished` (`YYYY-MM-DD`), and `schema:author` (array of Person `id` references, min 1). Optional: `schema:sourceOrganization` (an Organization `id` reference).

### `pulse:Contribution`

A Person's commit activity on a repository. Required: `pulse:contributionTo` (repository handle), `pulse:contributionCount` (integer ≥ 0), and `schema:author` (a single Person `id`). Optional: `pulse:firstContributionDate` / `pulse:lastContributionDate` (ISO 8601 datetimes).

### `org:Membership`

Links a Person to an Organization (the affiliation model). Required: `org:organization` (an Organization `id` reference). Optional: `org:role`, `time:hasBeginning` / `time:hasEnd` (`YYYY-MM-DD` dates). The Person side carries the back-reference via `org:hasMembership`.

!!! note "Accuracy note"
    Only the fields listed in the strict schemas can appear on an entity — the shapes are closed (`additionalProperties: false`). Anything outside the ontology is surfaced only via `gme-internal:*` (below).

## The `@context`

In `jsonld` output, `@context` (Open Pulse, `context_version` 2.1.2) maps short prefixes to namespaces — `schema:` → `http://schema.org/`, `pulse:` → `https://open-pulse.epfl.ch/ontology#`, `org:` → `http://www.w3.org/ns/org#`, `time:`, `wd:` (Wikidata), and others. It also declares which predicates are IRI references (`@type: @id`) versus typed literals:

- IRI references: `schema:author`, `schema:url`, `schema:license`, `schema:citation`, `schema:sourceOrganization`, `org:organization`, `org:hasMembership`, `org:hasUnit`, `org:unitOf`, `pulse:hasContribution`, `pulse:contributionTo`, `pulse:owns`, `pulse:ownedBy`, `pulse:isForkOf`, `pulse:discipline`, `pulse:repositoryType`, `pulse:OrganizationType`, and more.
- Typed literals: `schema:dateCreated` (`xsd:dateTime`), `schema:datePublished` (`xsd:date`), `pulse:contributionCount` / `pulse:githubRepoStars` / `pulse:githubRepoForks` / `pulse:githubOrgFollowers` (`xsd:integer`), etc.

This is why cross-entity links are bare `id` strings in the entity body but resolve to graph edges once expanded.

## Internal enrichment: `include_internal_fields`

The pipeline collects far more provider metadata (badges, releases, package coordinates, container images, funding URLs, …) than the ontology models. **By default these are stripped** so the output is strictly ontology-conformant.

Add `?include_internal_fields=true` to keep them. Each internal `_`-prefixed field is renamed to a `gme-internal:<field>` term (e.g. `_avatar_url` → `gme-internal:avatar_url`) and the `gme-internal` prefix (→ `https://openpulse.science/git-metadata-extractor#`) is registered in `@context`, so the payload still expands to real IRI triples. Parsed `publiccode.yml` scalars are additionally hoisted to `publiccode:*` terms.

!!! warning "Not SHACL-conformant"
    With `include_internal_fields=true` the document is intentionally **not** conformant to the closed Open Pulse SHACL shapes — `gme-internal:*` and `publiccode:*` are separate vocabularies. It still loads into a triplestore (no SHACL conformance is needed for that). Strict validation runs identically with the flag on or off; the flag only changes what you see.

The full catalogue of `gme-internal:*` fields — repo metadata, CI/coverage, `releases`/`latest_version`, container/compose images, per-registry published packages, `badges`, `funding_urls`, `citation_cff` — is documented in [repository-enrichment-fields.md](repository-enrichment-fields.md).

## A trimmed JSON-LD response

`GET /v2/extract/https://github.com/sdsc-ordes/gimie`:

```bash
curl -s \
  -H "Authorization: Bearer $GME_TOKEN" \
  "http://localhost:8000/v2/extract/https://github.com/sdsc-ordes/gimie"
```

```json
{
  "source_url": "https://github.com/sdsc-ordes/gimie",
  "detected_type": "repository",
  "output_format": "jsonld",
  "output": {
    "@context": {
      "schema": "http://schema.org/",
      "pulse": "https://open-pulse.epfl.ch/ontology#",
      "org": "http://www.w3.org/ns/org#",
      "schema:author": { "@type": "@id", "@container": "@set" },
      "schema:license": { "@type": "@id" },
      "pulse:contributionCount": { "@type": "xsd:integer" }
    },
    "@graph": [
      {
        "@id": "https://github.com/sdsc-ordes/gimie",
        "@type": "schema:SoftwareSourceCode",
        "schema:name": "gimie",
        "pulse:githubRepositoryHandle": "https://github.com/sdsc-ordes/gimie",
        "schema:license": "https://spdx.org/licenses/Apache-2.0",
        "schema:programmingLanguage": ["Python"],
        "pulse:githubRepoStars": 42,
        "schema:author": ["https://orcid.org/0000-0002-1825-0097"]
      },
      {
        "@id": "https://orcid.org/0000-0002-1825-0097",
        "@type": "schema:Person",
        "schema:name": "Jane Researcher",
        "pulse:githubUsername": "https://github.com/cmdoret",
        "org:hasMembership": ["https://orcid.org/0000-0002-1825-0097__https://ror.org/02s376052"],
        "pulse:hasContribution": ["https://orcid.org/0000-0002-1825-0097__https://github.com/sdsc-ordes/gimie"]
      },
      {
        "@id": "https://ror.org/02s376052",
        "@type": "org:Organization",
        "schema:name": "Swiss Data Science Center"
      },
      {
        "@id": "https://orcid.org/0000-0002-1825-0097__https://ror.org/02s376052",
        "@type": "org:Membership",
        "org:organization": "https://ror.org/02s376052"
      }
    ]
  },
  "warnings": [],
  "stats": {
    "entities_count": 4,
    "triples_count": 17,
    "run_id": "3f9c1e0a-...-9b2d",
    "duration_ms": 8421,
    "stages_completed": ["classify_url", "gather_context", "...", "build_jsonld_output"]
  }
}
```

Reading the parts:

- The **wrapper** (`source_url`, `detected_type`, `stats`) describes the run; `output` holds the graph.
- The first **`@graph`** node is the repository (`schema:SoftwareSourceCode`); its `schema:author` is an IRI reference to the Person node.
- The **Person** points to its affiliation through `org:hasMembership`, not a direct `schema:affiliation` — the `org:Membership` node and the `org:Organization` it references appear as separate nodes.
- Both the **Membership `@id`** (`personId__orgId`) and the **Contribution reference** (`personId__repoId`) are double-underscore composites of the two entity ids they link; the repo half is the repository node's full `@id`.
- `warnings: []` and the `stats` block confirm a clean run; quote `stats.run_id` if you need support.

To pull the enrichment layer (badges, releases, package coordinates), add the flag:

```bash
curl -s \
  -H "Authorization: Bearer $GME_TOKEN" \
  "http://localhost:8000/v2/extract/https://github.com/sdsc-ordes/gimie?include_internal_fields=true"
```

## See also

- [v2-api-reference.md](v2-api-reference.md) — the full endpoint and parameter reference.
- [v2-pipeline.md](v2-pipeline.md) — how the graph is built, stage by stage.
- [repository-enrichment-fields.md](repository-enrichment-fields.md) — every `gme-internal:*` field.
- [Open Pulse Ontology](https://sdsc-ordes.github.io/open-pulse-ontology/) — the ontology the entities conform to.
