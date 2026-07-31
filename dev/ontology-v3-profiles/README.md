# Adapting GME to the three-profile ontology — feasibility assessment

**Target:** [`sdsc-ordes/open-pulse-ontology` PR #25](https://github.com/sdsc-ordes/open-pulse-ontology/pull/25)
(`feature/platform-profiles` → `develop`, 73 files, +14684/−749) — "feat: major
overhaul". Three profiles now exist under `src/ontology/`: **raw**,
**provenance**, **canonical**.

**Scope of this assessment:** the **raw** and **provenance** profiles, which are
the ones an extractor writes. Canonical is read here only where it constrains
what raw must hand over. Assessed against GME `3.0.0` (this repo, `develop` at
`0757f4a`), which emits **Open Pulse Ontology v2.1.2**
(`schema/json/context/v2.0.jsonld`, `ontology_version: open-pulse-ontology-v2.1.2`).

Date: 2026-07-30. Nothing here has been implemented — this is an evaluation.

---

## Verdict

**The field-level work is easy and mostly mechanical. The structural work is a
rewrite of how GME thinks about identity, and one part of it is blocked by our
pinned RDF stack.**

Three findings drive everything below:

1. **GME already collects most of what the raw profile asks for.** The raw
   profile's platform properties are, to a striking degree, the fields GME
   already extracts and then hides in `_`-prefixed internal metadata /
   `gme-internal:` because v2.1.2 had no home for them. `_bio` →
   `pulse:biography`, `_company` → `pulse:company`, `_visibility` →
   `pulse:visibility`, `_open_issues_count` → `pulse:openIssueCount`, and ~40
   more. Full table in [`field-mapping.md`](field-mapping.md). This is the good
   news, and it is a lot of good news: the raw profile is close to a
   formalisation of what GME's providers already return.

2. **The raw profile inverts GME's identity strategy.** Raw says, in
   `ontology-shapes-raw.ttl` on `pulse:RawPersonShape`:
   *"Platform-scoped identities held by this provisional person. Extraction
   never resolves canonical identity — this is the anchor."* Platform fields
   (name, email, username, bio, company, avatar, counts) belong on a
   `pulse:PlatformProfile` node, one per source platform, linked by
   `pulse:hasProfile` / `pulse:profileOf`. GME does the opposite today: it
   resolves canonical identity *during* extraction (ORCID → GitHub login →
   UUID, `canonicalization/`) and stamps platform fields straight onto the
   Person. Under the new shapes, **today's Person entity is invalid in both
   profiles** — canonical `pulse:PersonShape` is `sh:closed true` and does not
   contain `schema:name`, `schema:email` or a username at all; those moved to
   `PlatformProfileShape`.

3. **Provenance needs RDF-star, and our stack cannot produce or validate it.**
   The provenance profile records winner-links as RDF-star annotations on
   quoted triples (`<< ?s ?p ?o >> prov:wasDerivedFrom ?output`). Its own
   header states pySHACL has no RDF-star support and rdflib cannot parse
   Turtle-star. We pin `rdflib==6.3.2` and `pyshacl==0.28.1` — both far older
   than the versions they tested. So this is not a "wire it up" task; it needs
   a serialisation decision first (see work-stream 3).

**Rough shape of the effort:** two of the five work-streams are large. The
renames and the new entity types are predictable. The PlatformProfile split
touches the identity core — agents, reconciliation, composite IDs, all schemas,
and effectively the whole test suite. Provenance is small *if* we accept a
non-star fallback, and open-ended if we don't.

---

## Work-streams, by cost

### 1. Platform-neutral renames — mechanical, breaking, low risk

The overhaul de-GitHub-ifies property names. Confirmed renames affecting us:

| GME 3.0.0 emits | Raw/canonical profile | Now lives on |
|---|---|---|
| `pulse:githubRepoStars` | `pulse:repositoryStars` | Repository |
| `pulse:githubRepoForks` | `pulse:repositoryForks` | Repository |
| `pulse:githubRepositoryHandle` | `pulse:repositoryHandle` | Repository |
| `pulse:githubOrganizationHandle` | `pulse:organizationHandle` | **OrganizationProfile** |
| `pulse:githubUsername` | `pulse:platformUsername` | **PlatformProfile** |
| `pulse:githubOrgFollowers` | `pulse:followerCount` | **OrganizationProfile** |

Each touches the three byte-identical schema copies (see AGENTS.md "Schema
change rules"), `context/v2.0.jsonld`, the generated Pydantic models, and test
fixtures. Note the right-hand column: three of six are not renames but
*relocations*, so they land in work-stream 2 rather than here.

**Also mechanical:** `pulse:composite` (on Membership and Contribution) reaches
RDF today — `HELPER_ONLY_FIELDS` in `pipeline/stages/jsonld_build.py:10` drops
`shacl`, `identifiers`, `idSource`, `_person_ref` but not `composite`. Every
new Membership/Contribution shape is `sh:closed true`, so it becomes a
violation. One-line fix.

### 2. The PlatformProfile split — the expensive one

What has to change:

- **New node types**: `pulse:PlatformProfile`, `pulse:OrganizationProfile`, one
  per (entity, platform) pair, each carrying that platform's fields plus
  `pulse:platform`, `pulse:platformInternalId`, `pulse:platformNodeId`,
  `pulse:platformUsername`.
- **Person/Organization become thin anchors.** Raw Person keeps `schema:name`
  (raw is `sh:closed false`) plus relations and an optional ORCID; canonical
  Person keeps relations + ORCID only.
- **Stop resolving identity at extraction time**, or emit both and let the
  canonical layer decide. This is a product decision, not a code decision:
  GME's whole value proposition today includes the ID hierarchy in
  `canonicalization/`, and the new model relocates that job to a downstream
  unifier (`pulse:samePersonAs`).
- **Composite IDs** (`{person_id}__{org_id}`) presume a resolved person IRI. If
  the person is provisional, composites need a provisional-stable basis.
- **Blast radius**: all six agent families (LLM + rule-based + refiners), 
  `reconciliation.py`, `canonicalization/`, the six strict schemas × three
  copies, the generated models, and most of the 1500-test suite.

A cheaper intermediate exists: keep GME's current resolution, and emit
PlatformProfile nodes *in addition*, carrying the platform fields that today go
to `gme-internal:`. That gets us raw-profile field parity and defers the
identity inversion. Worth costing before committing to the full split.

### 3. Provenance — data is there, serialisation is the problem

GME already has almost every input the provenance profile wants:

| Provenance term | GME already has |
|---|---|
| `pulse:ExtractionRun` + start/end | `run_id` (uuid4) and stage timings in `api/extract.py` |
| `pulse:extractedBy` → `prov:SoftwareAgent` + `schema:softwareVersion` | package version (now single-sourced from `pyproject.toml`) |
| `pulse:extractionSeed` | the `source_url` / batch list |
| `pulse:observationConfidence` | `_ror_match_confidence`, `_discovery_confidence`, `_rescue_confidence`, `_org_resolver_confidence`, LLM resolver confidence gates |
| `pulse:observationKind` | `_ror_match_tier`, `_discovery_reason`, `_rescue_reason`, `_org_resolver_reason` |
| `prov:wasDerivedFrom` | `_source` (`resolve_company_to_ror`, `resolve_bio_to_ror`, …), `_agent`, `observation/query_log.py` |
| `pulse:samePersonAs` | `llm_dedup` + reconciliation already compute this — then **discard** it by merging and remapping IDs |

Two real obstacles:

1. **Per-platform named graphs.** `pulse:ExtractionOutput` is one named graph
   per platform per run. GME emits a single flat `@graph`, and by assembly time
   the per-source attribution of an individual triple is largely gone — agents
   merge provider data before output. Recovering it means threading source
   attribution through the agent layer, not just relabelling at the end.
2. **RDF-star.** Blocked as described above. Options: (a) emit N-Quads with
   plain reification instead of quoted triples, accepting divergence from the
   spec; (b) emit the annotations as ordinary triples on an Observation node —
   which is what the profile explicitly moved *away* from; (c) upgrade rdflib
   and accept that pySHACL cannot validate the result; (d) defer provenance to
   a later release. **Recommendation: (d) for v4.0.0, with (a) prototyped**,
   and raise the tooling question with the ontology team — they hit the same
   wall.

Cheap early win regardless: `pulse:samePersonAs` is a plain triple, needs no
RDF-star, and turns information we currently throw away into output.

### 4. New entity types — predictable work, mostly deterministic

| New class | Source | GME status |
|---|---|---|
| `pulse:Collection` | HF collections | not collected; `_collection` field exists; already ROADMAP item 14 |
| `pulse:Community` | Zenodo communities | **already indexed** — `zenodo_communities` store in open-pulse-sources |
| `pulse:Funding` | ORCID funding records | not collected; ORCID provider would need the endpoint |
| `pulse:ExternalIdentifier` (+ `identifierScheme`) | ORCID external ids, arXiv/Scopus/… | **already collected** as `_orcid_external_identifiers` |
| `pulse:Deposit` (canonical) | Zenodo/Infoscience deposits | not modelled; GME puts `schema:datePublished` on the Article directly |

### 5. Enumerations — small, and one is a gift

`visibility`, `modality`, `SpaceSdk`, `SpaceRuntimeStatus`, `identifierScheme`,
`membershipType`. The last one is free: GME already distinguishes ORCID
employments from educations, which is exactly
`pulse:Employment` / `pulse:Education` / `pulse:SocietyMembership`, and raw
Membership adds `pulse:department` + `pulse:qualification` for data we already
pull.

---

## What their side is missing that we can capture

Ranked by how much of our current extraction it would strand. Full list with
the exact GME field names in [`field-mapping.md`](field-mapping.md#unmapped--gaps-to-feed-back).

1. **Package-registry coordinates — no concept at all.** We collect
   `_conda_channel`, `_maven_group_id`, `_maven_artifact_id`, `_latest_version`,
   and `dev/superpowers/specs/` has designs for npm / PyPI / Maven / NuGet / Go
   / crates / RubyGems. The ontology has no artifact, package or release
   concept, so "this repo ships as `pypi:gimie`" has nowhere to go. This is the
   single biggest gap — it is how software actually gets *used*.

   **It also blocks a dependency term they just added.** `pulse:dependsOn` is
   typed repository → repository, but our dependency data is an SPDX SBOM of
   *package* coordinates (`pypi:requests@2.31`). There is no lossless way to
   emit `dependsOn` without a package node. Detail and options:
   [`dependencies-and-dependents.md`](dependencies-and-dependents.md).
2. **Container artifacts and deployment topology.** `_docker_hub_url`,
   `_container_images`, `_compose_files`, `_compose_images`,
   `_compose_image_urls`. Only `pulse:externalReference` comes close, and it is
   a bare link. Note DockerHub is also **absent from `PlatformEnumeration`**
   while open-pulse-sources maintains a `dockerhub` index.
3. **`PlatformEnumeration` is narrower than our provider set.** It has GitHub,
   GitLab, Bitbucket, Infoscience, ORCID, HuggingFace, Zenodo. We read ROR,
   OpenAlex, ETHZ Research Collection, SNSF, SWISSUbase, RenkuLab, DockerHub
   and EPFL Graph. **ROR's absence is the sharp one**: it is our canonical
   organization authority, and it cannot be named as a platform.
4. **Infoscience is missing from `IdentifierSchemeEnumeration`** (arXiv, Scopus,
   ResearcherId, ISBN, ISSN, PMID, Ringgold, GRID, ISNI, SWHID, Other) — even
   though `pulse:Infoscience` exists as a *platform*. We carry
   `infosciencePersonIdentifier`, `infoscienceOrganizationIdentifier` and
   `infoscienceArticleIdentifier`; all three would collapse to
   `OtherIdentifierScheme` and lose their scheme.
5. **Repository health and quality signals.** `_badges` / `_badge_count`,
   `_has_ci`, `_test_coverage`, `_community_health_percentage`,
   `_has_issue_template`, `_has_pull_request_template`, `_has_issues`,
   `_has_projects`. Raw models `hasWiki`/`hasPages`/`hasDiscussions` but stops
   short of the maturity signals — which are exactly what a research-software
   observatory is asked about.
6. **ROR / registry organization metadata.** `_ror_established`, `_ror_status`,
   `_ror_types`, `_acronyms`, `_unit_code`, `_parent_acronym`, `_director_name`,
   `_org_type_dspace`, `_infoscience_code`. `OrganizationProfile` is thin
   (platform, handle, name, url, internalId, description, location,
   followerCount, dateModified) and ROR is not a platform, so none of this
   lands.
7. **Smaller but real:** `_archived_at` (they have only the boolean
   `pulse:archived`, not when), `_size_kb` (`fileCount` exists, bytes do not),
   `_network_count`, `_primary_language` (multi-valued
   `schema:programmingLanguage` only, no primary), name variants
   (`_aliases`, `_original_name`, `_orcid_other_names` — no `skos:altLabel`
   modelling), `_profile_readme`.

### Git author identities — resolved, and still blocked on cardinality

Canonical `pulse:ContributionShape` defines `pulse:gitAuthorName` and
`pulse:gitAuthorEmail`. Two separate issues, one now settled:

**Privacy — settled 2026-07-31.** We emit the hashed local part plus the domain
and never a full address, which is what
`pipeline/stages/privacy.py::anonymize_email` already produces. So
`pulse:gitAuthorEmail` simply stays unpopulated; no flag, no opt-in, no code
path that emits an address. Names are emitted in full — they are the
attribution record.

**Cardinality — still blocking.** Both properties are `sh:maxCount 1`, but one
person commits under several (name, email) pairs. Capturing *all* of them, as
required, is impossible under the current shape. Proposal:
[`../v4.0.0/ontology.ttl`](../v4.0.0/ontology.ttl) §9 (`pulse:GitIdentity`);
analysis in
[`../v4.0.0/git-author-identities.md`](../v4.0.0/git-author-identities.md).

---

## Recommended sequencing for v4.0.0

1. **Now, cheap, no blockers:** the renames (WS1), `pulse:composite` fix,
   enumerations (WS5), `ExternalIdentifier` from data we already have,
   `pulse:samePersonAs` from data we already discard.
2. **Next, and the real decision:** PlatformProfile. Cost the
   "profiles-in-addition" variant against the full identity inversion before
   writing code — the second one changes what GME *is for*.
3. **Feed back to the ontology team before they merge #25**, since several gaps
   above are cheap to add while the PR is open and expensive afterwards:
   ROR + DockerHub in `PlatformEnumeration`, Infoscience in
   `IdentifierSchemeEnumeration`, a package/artifact concept, and the
   `gitAuthorEmail` privacy question.
4. **Defer:** the provenance graph, pending the RDF-star tooling answer.

## What was not checked

- Only `ontology-{definitions,shapes,enumerations}-{raw,provenance,canonical}.ttl`
  were read; the three `ontology-combined-*.ttl` files (200 KB+ each) were not,
  on the assumption they are merges of the above.
- No conversion was attempted — no sample GME graph was validated against the
  new shapes. That is the obvious next step and would turn the estimates here
  into measurements.
- The discipline-tree growth mentioned in the PR description was not assessed;
  it interacts with `concept_tagging` and the EPFL Graph index and deserves its
  own look.
