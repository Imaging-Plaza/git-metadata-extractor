# Provenance-aware extraction & unification — architecture (agent reference)

**Status:** design / proposed. Targets V2 (`src/v2/`). V1 is frozen.
**Companion:** `PROVENANCE_ARCHITECTURE.html` (human-oriented).

## Goal

Two coupled goals over one graph:

1. **Deduplication** — collapse the same real-world entity seen across platforms/repos into one canonical node (`:Robin`), with a single query-friendly value per property.
2. **Triple-level provenance** — for any canonical triple (`:Robin schema:name "Robin Franken"`), resolve *which source graph and which source triple* it was derived from.

## Core principle: four layers

```
extract → named-graph substrate → unify (store-side) → canonical graph + provenance graph
```

- Extraction is **collapse-late**: it emits `pulse:PlatformProfile` nodes with platform-scoped IDs and a **provisional Person per profile**. It never resolves canonical identity.
- The **substrate** is one named graph per `(source × run)` — raw assertions, never rewritten. This is the "force it into named RDF graphs" step.
- **Unification** runs in the triplestore (Oxigraph), over the union of accumulated runs. It clusters profiles into a canonical Person, **chooses** one value per property, and materializes them into `graph:canonical`.
- The **provenance graph** (`graph:prov`) records, for each *derived* canonical triple, a link back to the source triple/graph.

### Why named graphs alone are insufficient

A named graph attributes a **raw** triple to whoever asserted it. The canonical triple is **derived** — a value chosen among candidates on a subject (`:Robin`) the unifier minted. It lives in `graph:canonical`, whose author is the unifier, not the source. Recovering the source by matching the object value fails whenever the value was normalized or is non-unique. The provenance graph records the derivation decision explicitly, so it survives normalization.

## Identity / ID generation (critical)

| Node | Minted when | IRI rule |
|---|---|---|
| `PlatformProfile` (+ provisional Person) | **extraction** | platform-scoped, deterministic (e.g. `https://github.com/{login}`, Infoscience author URI) |
| canonical `Person` | **unification** | promote `https://orcid.org/{id}` → `https://ror.org/{id}` (orgs) → else `urn:pulse:{uuid}` |

This inverts today's behavior: `src/v2/canonicalization/id_resolution.py` + `reconciliation.py` currently pick the canonical Person IRI at extraction/reconcile time (`ORCID → github-login → uuid`). Same priority, **moved to unify**.

## Generalization across entity types

The methodology splits into a **type-agnostic layer** (identical for every class) and a **thin per-type identity resolver** (the only part that varies).

- **Type-agnostic:** named-graph substrate, provenance graph, winner-selection, upsert/counters, two-shape validation. Operates on triples regardless of subject type.
- **Per-type resolver:** promoted canonical ID, the profile/record node, match/conflict keys.

| Type | Canonical ID (promote → else) | Per-source record | Match keys | Disposition |
|---|---|---|---|---|
| Person | ORCID → github → `urn:pulse:{uuid}` | `PlatformProfile` (GitHub/ORCID/Infoscience/HF) | ORCID · email · name+affil | Merge |
| Organization | ROR → handle → `urn:` | `OrganizationProfile` (GitHub/HF org) | ROR · handle · name | Merge |
| ScholarlyArticle | DOI → Infoscience/arXiv → `urn:` | registry record (Infoscience/OpenAlex/Zenodo/Crossref) | DOI · title+authors | Merge |
| Dataset (`pulse:Data` repo) | DOI (Zenodo/DataCite) → `urn:` | deposit record | DOI · concept-DOI | Merge |
| Repository (code) | handle `owner/name` | per-platform repo record (rarely >1) | handle · fork lineage · DOI backlink | Link (identity ≈ 1) |
| Membership / Contribution | composite `{a}__{b}` | — reified edge | resolved endpoints | Recompute |

**Nuances:**

1. **Repositories are the outlier — link, don't merge.** No global ID (identity ≈ platform handle). Cross-source relatedness is modeled as a *relationship* via `schema:Project` + `pulse:projectOutput` (plus `pulse:isForkOf`, `schema:citation`), not an identity merge. Person / Org / Article / Dataset dedup on global IDs (ORCID / ROR / DOI) — the high-precision, easy case.
2. **Relationship edges are derived → they get provenance and must be recomputed.** `schema:author`, `pulse:owns`, `org:hasMembership`, `pulse:contributionTo`, `schema:citation` are asserted by the unifier, so they are `Observation` targets. `Membership` / `Contribution` composite IDs must rebuild once endpoints canonicalize — logic already present in `reconciliation.py` (`_apply_remaps`).

**Precedent:** the dormant `dev/…/deduplication/` prototype's `DuplicateDetector` already had one method per bucket (`_find_person_duplicates`, `_find_organization_duplicates`, `_find_repository_duplicates`, `_find_article_duplicates`, `_find_membership_duplicates`, `_find_contribution_duplicates`) — the per-type resolver pattern, pre-sketched. Build Phase 4's unifier as a **registry of per-type resolvers** over the shared machinery.

## Fit against the current pipeline

Current driver: `src/v2/api.py` (master sequence) + `src/v2/pipeline/orchestrator.py` (stage runner, shared `runtime_context`/`pipeline_outputs`).

| Current stage / file | Disposition | Change |
|---|---|---|
| `gather_context` (`pipeline/stages/context_gather.py`), per-entity agents (`agents/llm/<e>/agent.py`, `agents/rule_based/<e>_agent.py`) | **Adapt** | Emit `PlatformProfile` + provisional Person, platform-scoped IDs. Drop canonical-identity resolution. |
| `context_summary`, RAG tools (`agents/llm/agent_tools/`) | **Keep** | Unchanged. |
| `llm_dedup` (`pipeline/stages/llm_dedup.py`), `reconcile_entities` (`pipeline/stages/reconciliation.py`) | **Relocate** | Union-find on ROR/ORCID/handle → store-side profile→Person linker; cross-**run**, not intra-request. |
| resolvers (`resolve_*_to_ror` in `api.py`), `infer_owners`, `infer_org_units`, `org_relationships`, `guarantee_repo_author` | **Relocate** | Run over accumulated store, not one repo's slice. |
| `strict_validation` + `assemble_output` (`pipeline/stages/output_assembly.py`) | **Adapt** | Split into substrate validation (open) at write + canonical validation (closed) after unify. |
| `build_jsonld_output` (`pipeline/stages/jsonld_build.py`) — single `{@context,@graph}` | **Replace** | Write per-`(source×run)` **named graphs** (TriG / N-Quads → Oxigraph). |
| `shacl_validation.py` (warning-only, `open-pulse-ontology-v2.1.2.ttl`) | **Adapt** | Two shape sets; promote canonical gate to enforcing. |
| — | **New** | Substrate writer · `ExtractionRun` stamping · store-side unifier · provenance writer · query/provenance API. |

Note: today each `/v2/extract` is **intra-request** and cached per-URL — it cannot unify one person across repos. Cross-run dedup is the reason unification moves to the store.

## Ontology gaps

Source of truth is a TTL (OWL + embedded SHACL). JSON schemas live in one place
since 2026-09-08 — `git_metadata_extractor/schema/json/{agent,strict}/{entity}.schema.json`
(the triplication and its two stale copies were removed). Models generated via
`just v2-models-generate`.

1. **Merge the two ontology branches.** Profile model (`PlatformProfile`, `OrganizationProfile`, `PlatformEnumeration`, platform-scoped `platformUsername`/`repositoryHandle`/`organizationHandle`/`platformInternalId`/`platformNodeId`, `gitAuthorName`/`gitAuthorEmail`, `hasProfile`/`profileOf`) is in `v3.0.0-develop`. Provenance model (`ExtractionRun`, `Observation`, `observedFrom`/`observedProperty`/`observedValue`/`partOfRun`/`observedOn`/`observationConfidence`, `firstObservedOn`/`lastConfirmedOn`/`observationCount`, `samePersonAs ⊂ owl:sameAs`) is in published `v3.0.0` (`docs/releases/v3.0.0/open-pulse-ontology-v3.0.0.ttl`). **Neither has both.**
2. **Nothing is in the repo schemas** — only frozen v2.1.2. Both class families are greenfield under `src/v2/schema/` and `src/v2/validation/`.
3. **No named-graph ↔ run link.** Declare that a substrate graph IRI *is* an `ExtractionRun` (or `prov:Activity`), so `observedFrom → graph` resolves.
4. **Two SHACL shape sets needed.** Substrate: `sh:closed false`, `PlatformProfile` required, ≥1 identifier, git identity literal (not linked). Canonical: closed `Person`, ≥1 `pulse:hasProfile`, one value per functional property.
5. **Encoding decision.** PROV-O (`prov:wasDerivedFrom`) vs `pulse:Observation`; RDF-star vs reification. **Default: RDF-star quoted-triple links in a store-side `graph:prov`** — Oxigraph is RDF-star native, and because `graph:prov` is not SHACL-validated, SHACL-star immaturity does not bite. Use `pulse:Observation` (plain RDF) only if provenance must round-trip JSON-LD or be SHACL-shaped. The canonical/substrate graphs stay plain RDF regardless.

## Implementation phases

Each phase should land behind a flag and keep the existing single-`@graph` path working until cutover.

1. **Ontology & schemas** — merge profile+provenance TTL into `src/v2/validation/`; author substrate + canonical agent/strict JSON schemas (3-copy sync); `just v2-models-generate`; `just v2-models-check` green.
   - *Done when:* both shape sets load in `validation/ontology.py`; models regenerate cleanly.
2. **Extraction emits profiles** — per-entity agents output `PlatformProfile` + provisional Person; new `ExtractionRun` stamped in `runtime_context`.
   - *Done when:* a `/v2/extract` produces profile-shaped entities validating against substrate SHACL; `identifiers.uuid` still server-minted.
3. **Substrate writer** — new stage replacing `build_jsonld_output`: serialize each source's entities into a named graph `graph:{source}/{run}` (N-Quads/TriG); load into Oxigraph.
   - *Done when:* a run materializes ≥1 named graph in Oxigraph; raw triples byte-preserved.
4. **Store-side unifier** — port `dev/ontology-v2-json-response/a-001/deduplication/` (`EntityRegistry`/`DuplicateDetector`/`ReferenceNormalizer`/`GraphMerger`/`GraphValidator`) into a profile→Person linker + value selector; SPARQL `CONSTRUCT`/`INSERT` into `graph:canonical`. Sources are `.pyc`-only — reconstruct from bytecode or rewrite fresh.
   - *Done when:* two profiles with a shared ORCID collapse to one canonical Person across two runs.
5. **Provenance writer** — during value selection, emit winner-links into `graph:prov` as **RDF-star** quoted-triple annotations (`<< s p o >> prov:wasDerivedFrom <graph> ; pulse:observationKind …`) — or `pulse:Observation` nodes if the plain-RDF fallback is chosen — **derived-only**, **upsert**, bump `observationCount`/`lastConfirmedOn`.
   - *Done when:* every derived canonical triple resolves to its source graph + run via one lookup; single-source unmodified values need no record.
6. **Validation split** — substrate SHACL (open) at write; canonical SHACL (closed) after unify, **enforcing** (fail the canonical build on violation, unlike today's warning-only gate).
7. **API** — query endpoint over `graph:canonical`; provenance-lookup endpoint (`triple → source graph/run/confidence`).

## Decisions (defaults)

- **Provisional Person per profile:** yes (front & canonical ontologies structurally identical → unify is a pure merge).
- **Winner encoding:** **RDF-star** (`<< s p o >> prov:wasDerivedFrom …`) in a store-side `graph:prov`, derived-only, upsert + counters — exact per-triple handle, compact, Oxigraph-native. `graph:prov` is not one of the SHACL-validated shape sets, so SHACL-star immaturity does not apply. Fall back to `pulse:Observation` (plain RDF) only if provenance must be SHACL-shaped or emitted as JSON-LD.
- **Unification location:** store-side (Oxigraph), enabling cross-run dedup across the 2,590-repo seed list (`data/my-seeds.txt`).
- **Canonical ID:** ORCID → ROR → `urn:pulse:{uuid}`, decided at unify.
- **Provenance grain:** reify only *contested/normalized* winners; single-source unmodified triples are already attributed by their named graph.
- **History:** upsert current winner + summary counters on the entity; do not append every observation.

## Cost profile

- **Query:** provenance in its own named graph → zero overhead on `graph:canonical` queries; "where from?" = one indexed lookup; whole-graph annotation = linear batch job.
- **Write:** negligible — piggybacks on the value-selection the unifier already does.
- **Storage:** scales with `deduplicated entities × ~5–15 fields`, not raw triple volume. Cheap corner = RDF-star + derived-only + upsert; expensive corner = Observation + all-winners + append (full audit trail). Compute is the same either way — it's a storage-vs-auditability choice. Dominant growth is the accumulating raw substrate, not provenance.

## Risks / open questions

- Oxigraph as the unification engine assumes it is in-scope and writable by this service (today referenced only in comments — `prune_dangling_refs.py`, `output_assembly.py`, `id_resolution.py`). Confirm ownership/deployment.
- Moving inference store-side means the per-request response changes contract (profiles, not a resolved graph). Decide whether `/v2/extract` returns the substrate slice and a separate job performs unification, or the endpoint blocks on unify.
- Value-selection policy (which source wins per property) must be explicit and itself provenance-worthy — record the rule in the `Observation` (`observationKind`/`observedFrom`).
- SHACL-star immaturity is the main blocker if RDF-star is chosen; validate the pyshacl path before committing.
