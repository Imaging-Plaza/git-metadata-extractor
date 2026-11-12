Editing ontology and source mappings

This directory is the authoritative configuration for deterministic payload
mapping, projection tables and merge/classification policies. It ships inside
the Python wheel: Docker, source checkouts and installed packages read the same
resources independently of working directory. Do not create a second copy in
the repository-level config directory.

Workflow and ownership
  source records -> prepared facts -> agent payloads -> reconciled intermediate
                                                    -> raw projection
                                                    -> canonical projection

Adapters fetch, normalize and match records in Python. Source mappings enrich
fields; agent mappings build the six rule-based entity payloads from prepared
facts. Post-agent Python stages reconcile those payloads before raw/canonical
mappings project the same intermediate into their respective ontology shapes.
Raw projection runs first, before canonical projection replaces the intermediate.
It reads a private normalized copy that retains internal source facts. The
public JSON-LD builder strips them (or renames them in debug output); neither
representation is the raw mapping input. Raw output strips internal keys at
every depth. GitHub profile extras are applied only to GitHub profiles, and a
GitHub biography never falls back to ORCID biography. Programming languages
belong in schema:programmingLanguage, not the natural-language pulse:language.
The store-side unifier separately merges stored raw graphs into canonical data.

Use one small mapping per source/entity or projection contract. Do not combine
unrelated sources into a monolithic mapping. Shared decisions belong in the
three typed policy files below; fetching, matching and graph algorithms stay in
Python. This guide is the detailed reference; inspect the CLI inventory for the
current mappings and fields rather than keeping another field list in docs.

Where to edit
  agent/*.yaml          All six rule-based entity payloads from prepared facts
  github/*.yaml         GitHub repository, person and organization source fields
  orcid/person.yaml     Normalized ORCID person record enrichment
  ror/organization.yaml Normalized ROR organization record enrichment
  infoscience/*.yaml    Normalized Infoscience person/organization enrichment
  raw/*.yaml            Raw repository/profile fields, base profiles and deposits
  canonical/*.yaml      Canonical profiles/deposits and repository metric renames
  policies/ontology.yaml
    Raw/canonical passthrough properties, platform/host routing, IRI coercion,
    source attribution, index-backed platforms and extraction-run membership.
  policies/extraction.yaml
    Ordered repository/organization classification keywords, defaults,
    documentation languages and auxiliary repository filenames.
  policies/merge.yaml
    Merge dispositions, ordered identity match keys/priorities, identity IRI
    templates and per-run properties excluded from canonical graphs.

The source_contract label describes the adapter input, not an upstream response
validator. agent.* maps prepared facts (normalized IDs, dates, relations and
matched records). github.* maps REST record fields; ORCID, ROR and Infoscience
enrichment maps the normalized records returned by the existing providers.
raw.deposit uses the canonical Deposit contract because that shape is shared.

Field rules
  pulse:watcherCount:
    sources: [_subscribers_count]
  identifiers.uuid:
    sources: [identifiers_uuid]
  type:
    value: schema:Person
  pulse:visibility:
    sources: [_visibility]
    transform: enum
    values: {public: 'pulse:PublicVisibility', private: 'pulse:PrivateVisibility'}

Declare exactly one of sources or value. sources is an ordered fallback list;
the first nonempty input wins. Dot paths traverse nested dictionaries, including
nested targets such as identifiers.uuid. Literal dots in keys are not supported.
Parent/child target collisions are rejected. Constants may be JSON values.

False and zero are data. None is missing. Empty strings/lists/dicts are normally
missing; empty: preserve keeps them and prevents fallback to another source.
Missing outputs are omitted unless missing: 'null' is explicit. Quote 'null':
it is a policy name, not a YAML null. To emit a null constant use both value: null
and missing: 'null'. Prepared payload mappings preserve existing empty/null
fields for compatibility; source enrichment usually omits absent data.

Transforms: identity copies the value; iri wraps a string in an @id reference;
web_iri requires an explicit HTTP(S) URL, validates/normalizes it with Pydantic,
and wraps it in @id. Bare domains and invalid links are omitted, not guessed.
strings keeps nonempty string list members; enum lowercases the input lookup
key and uses the explicit values table. Unknown enum inputs follow missing
policy. YAML cannot execute Python or import transforms.

fields must name generated target-model properties. internal_fields is only
allowed for agent contracts and each target must start with _. Existing output
and validation boundaries strip that metadata. Duplicate YAML keys, unknown
transforms/contracts/properties and unknown policy keys fail validation.

Policies use Pydantic models too. Property/type references and classification
enum values are checked against generated contracts. Merge policies must cover
every uncapped property and cannot union always-capped properties. A property
whose cap varies by entity type retains the existing per-type runtime guard.
Identity templates require one plain {} substitution; every promoted identity
field needs a template and must belong to its entity contract. Match fields
also use the entity contract; legacy flat facts from another shape require an
explicit legacy_match_contracts entry (Person email comes from PlatformProfile).
This declares the field's shape; it does not traverse profile nodes. The strongest
identity must be matched first. Same-as predicate semantics remain covered by the
ontology-backed unifier tests, rather than a duplicate ontology term list.

Updating a field or policy
1. Edit its YAML and bump the version string, e.g. '1' to '2'.
2. Add a source-record/behavior test proving meaning as well as datatype.
   GitHub watchers_count means stars; subscribers_count means watchers.
   GitHub size is KB and must never become pulse:fileCount.
3. Run in the project environment:
     python -m git_metadata_extractor.mapping --check
     python -m pytest tests/v2/test_yaml_mappings.py tests/v2/test_full_yaml_migration.py
   Also run affected agent/projection/unifier tests and ontology model checks.
4. Restart the extractor. Parsed configurations and fingerprints are cached for
   process lifetime; there is no hot reload. Module-level policy views refresh
   at restart too.

Inspection and editor support
  python -m git_metadata_extractor.mapping
prints the full mappings/policies inventory, versions and aggregate fingerprint.
  python -m git_metadata_extractor.mapping --schema
prints JSON Schema for mapping and policy documents from their Pydantic models.
  python -m git_metadata_extractor.mapping --check
validates all packaged YAML, including policy coverage against generated models.

Updating the ontology
Raw/canonical models derive from the pinned vendor/open-pulse-ontology SHACL
definitions. Update the pin/approved patches and regenerate with the repository
ontology tooling; run just ontology-models-check and the mapping check. Agent
contracts still derive from schema/json/agent via just v2-models-generate;
verify with just v2-models-check. Update generated contracts and YAML together.
No ontology/schema version change is required by this configuration migration.

Target checks establish property existence, not complete RDF semantics or all
runtime value constraints. Strict JSON Schema and SHACL gates still validate
output types, cardinalities and graph relationships. Semantic source fixtures
remain necessary even when all schema checks pass.

Reproducibility
All mapping/policy YAML, generated target JSON schemas and mapping engine/model/
policy/check/transform code contribute to one fingerprint. Pipeline and agent
verdict caches use it; benchmark manifests carry the inventory. Mapping activities
in the separate evidence graph record mapping name/version, source/target
contract and the aggregate fingerprint (which includes policies). Policies do
not emit separate execution activities. A fingerprint identifies content; it
does not archive an old configuration or source record. A mapping activity
records a transformation, not proof that every resulting field was explicitly
cited by a source. Keep this execution evidence separate from canonical entity
fields and from the unifier's selection decisions in graph:prov.

Adding another source or rule
Add a namespace/name YAML and call map_fields from its adapter/agent. Generated
agent/raw/canonical models are discovered by checks.CONTRACTS; new target shapes
must be generated first. New policy kinds need an explicit typed registration in
policies.py. New executable transforms belong in registry.py and FieldRule's
transform allowlist, with tests. YAML alone neither fetches a source nor changes
pipeline sequencing.

Migration boundary
All six rule-based entity payload builders, their profile enrichment mappings,
raw/canonical declarative projection tables and unifier policy tables now use
YAML. Source matching and precedence, identifier/date normalization, DOI guards,
complex metadata parsers, graph construction, reconciliation, inference and
validation algorithms remain Python. LLM prompts/tool execution and hybrid
refiner logic are unchanged. This is the full declarative mapping migration,
not a scripting language for the entire extraction pipeline.
