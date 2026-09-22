# What git-metadata-extractor needs from open-pulse-ontology v3.0.0

**Status:** request / for the ontology maintainers.
**Written against:** PR #25 (`feature/platform-profiles`) at `290579d`, and the published
`v3.0.0` TTL vendored here at `docs/releases/v3.0.0/open-pulse-ontology-v3.0.0.ttl`.
**Companion:** `PROVENANCE_ARCHITECTURE.md` (the four-layer design this serves).

Until the branches merge, this service assumes the shape described below and generates
its models from it. Every item is something the extractor's refactor depends on.

---

## 0. Correction — verified against the submodule, 2026-09-08

The first version of this document was written from PR #25's **shapes** files
plus the published v3.0.0 TTL. It never read
`src/ontology/ontology-definitions-provenance.ttl` (229 lines), which is far
richer than the shapes suggested. Two of four asks were wrong.

| Ask | Verified status |
|---|---|
| 1 · Port the `Observation` vocabulary | **Reframed** — PR #25 deliberately uses RDF-star annotations instead of reified Observation nodes. Needed only as the plain-RDF fallback; see §3.2. |
| 2 · `pulse:platformInstance` | **Stands** — 0 occurrences in the submodule |
| 3 · `ExtractionRun rdfs:subClassOf prov:Activity` | **Withdrawn — already done**, `ontology-definitions-provenance.ttl:40` |
| 4 · Organization counterpart to `samePersonAs` | **Stands** — `samePersonAs` exists, `sameOrganizationAs` does not |

Already provided by PR #25, contrary to the original text: `pulse:ExtractionRun`
(as a `prov:Activity`), `pulse:ExtractionOutput`, `prov:SoftwareAgent`,
`prov:wasDerivedFrom`, `prov:wasGeneratedBy`,
`prov:{started,ended,generated}AtTime`, `pulse:observationKind`,
`pulse:observationConfidence`, `pulse:observedOn`, `pulse:firstObservedOn`,
`pulse:lastConfirmedOn`, `pulse:observationCount`, `pulse:samePersonAs`,
`pulse:partOfRun`, `pulse:extractedBy`, `pulse:extractionSeed`.

Genuinely absent, because they belong to the reified encoding PR #25 chose not
to use: `pulse:Observation`, `pulse:observedFrom`, `pulse:observedProperty`,
`pulse:observedValue`, `pulse:aboutEntity`.

## 1. The two branches: encoding, not capability

| Model | Where it lives | Encoding |
|---|---|---|
| **Profiles** | PR #25 | `pulse:PlatformProfile`, `pulse:OrganizationProfile`, `pulse:PlatformEnumeration` (7 members), `pulse:platformUsername`, `pulse:organizationHandle`, `pulse:platformInternalId`, `pulse:platformNodeId`, `pulse:hasProfile`, `pulse:profileOf`, `pulse:organizationProfileOf` |
| **Provenance, RDF-star** | PR #25 | Annotations on quoted triples: `prov:wasDerivedFrom`, `pulse:observationKind`, `observationConfidence`, `observedOn`, `firstObservedOn`, `lastConfirmedOn`, `observationCount`, plus `ExtractionRun`, `ExtractionOutput`, `SoftwareAgent`, `partOfRun`, `extractedBy`, `extractionSeed`, `samePersonAs` |
| **Provenance, reified** | published `v3.0.0` | The same ground as nodes: `pulse:Observation` + `observedFrom`, `observedProperty`, `observedValue`, `aboutEntity` |

The branches are **not** profile-versus-provenance, as the first draft of this
document claimed. PR #25 has both models; it encodes provenance as RDF-star
annotations rather than reified `Observation` nodes, and documents the reasoning
in `ontology-definitions-provenance.ttl`.

So the merge question is narrower than "combine two halves": it is **which
encoding `graph:prov` uses**, and then carrying only the vocabulary that
encoding needs. §3.2 argues the RDF-star choice is sound but store-only, which
is what makes the reified vocabulary worth keeping available rather than
discarding.

---

## 2. Gaps in both branches

### 2.1 No way to say *which instance* of a platform — blocking

`pulse:platform` is `pulse:GitLab`, and `pulse:organizationHandle` is an
`xsd:string` with `sh:maxCount 1`. Nothing records the host. So:

```turtle
# gitlab.com                          # gitlab.epfl.ch
[] pulse:platform pulse:GitLab ;      [] pulse:platform pulse:GitLab ;
   pulse:organizationHandle "epfl" .     pulse:organizationHandle "epfl" .
```

These are indistinguishable, so they canonicalise into one profile — **one node for two
unrelated organizations**. EPFL runs its own GitLab and we intend to harvest it, so this
is a first-class case, not an edge one.

**Requested:**

```turtle
pulse:platformInstance a owl:DatatypeProperty ;
    rdfs:domain pulse:PlatformProfile , pulse:OrganizationProfile ;
    rdfs:range xsd:anyURI ;
    rdfs:comment "Base URI of the platform instance this profile lives on, for "
                 "self-hosted deployments (e.g. https://gitlab.epfl.ch). Absent "
                 "means the platform's canonical public instance." .

pulse:PlatformInstanceShape a sh:PropertyShape ;
    sh:path pulse:platformInstance ;
    sh:nodeKind sh:IRI ;
    sh:maxCount 1 .
```

Then add `sh:property pulse:PlatformInstanceShape` to `PlatformProfileShape`,
`OrganizationProfileShape` and their `Raw*` counterparts. Identity becomes
`(platform, platformInstance, handle)`.

**Interim assumption:** we key profile identity on `schema:url` (already present as
`sh:nodeKind sh:IRI`) instead. It works, but it makes the identity key a full URL rather
than a tuple, which is harder to index and to compare.

### 2.2 Named graph to ExtractionRun link — WITHDRAWN, already present

PR #25 declares `pulse:ExtractionRun rdfs:subClassOf prov:Activity` at
`ontology-definitions-provenance.ttl:40`, and models each platform's substrate
graph as a `pulse:ExtractionOutput` (`rdfs:subClassOf prov:Entity`) joined to
its run by `prov:wasGeneratedBy`. That is a better design than the one asked
for: the graph IRI denotes the *output*, not the run, so several platform
outputs from one run share a single Activity.

Nothing needed here. `PROVENANCE_ARCHITECTURE.md`'s gap 3 is closed.

### 2.3 `samePersonAs` is Person-only — needed for organizations too

The published branch has `pulse:samePersonAs ⊂ owl:sameAs`. We need the same for
organizations, both for profile→Organization linking and for the `/v2` id migration
(old GitHub-handle ids must stay followable after re-identification).

**Requested:** either generalise to `pulse:sameEntityAs`, or add
`pulse:sameOrganizationAs ⊂ owl:sameAs` alongside it.

**Interim assumption:** plain `owl:sameAs`. This already validates — all 10 canonical
shapes in PR #25 carry `sh:ignoredProperties ( rdf:type owl:sameAs )` — but it is
untyped, so we cannot distinguish "same organization" from any other identity claim.

---

## 2.4 v3 changes identifier *value formats* — bare, not URL

Found while generating models from the shapes. The canonical shapes' patterns:

| Form | Property | Pattern |
|---|---|---|
| **bare** | `pulse:orcidIdentifier` | `^\d{4}-\d{4}-\d{4}-\d{3}[0-9X]$` |
| **bare** | `pulse:doi` | `^10\.\d{4,9}/[-._;()/:a-zA-Z0-9]+$` |
| **bare** | `pulse:repositoryHandle` | `^[a-zA-Z0-9\-_.]+(/[a-zA-Z0-9\-_.]+)+$` |
| **URL** | `pulse:ror` | `^https://ror\.org/[0-9a-z]{9}$` |

Today's hand-written schemas require the **URL** form for all of them
(`^https://orcid\.org/...`, `^https://github\.com/...`), and the live graph
matches: all 21 ORCID values in the 120-URL corpus are URLs.

So **adopting v3 is a value migration, not only a property rename.** Every
ORCID, DOI and repository handle in the store has to be rewritten from
`https://orcid.org/0000-...` to `0000-...`. That is not in the refactor plan and
needs to be, alongside the `owl:sameAs` id bridge — the two are independent:
one changes node *ids*, this changes literal *values*.

**Confirmed intentional (2026-09-08).** The bare direction is deliberate, and
`pulse:ror` staying URL-shaped is deliberate too. So this is not an ask — it is
confirmed migration scope:

- every `pulse:orcidIdentifier`, `pulse:doi` and `pulse:repositoryHandle` in
  the store is rewritten from URL to bare
- `pulse:ror` is left alone
- consumers that string-match `https://orcid.org/` stop matching

Worth adding to each shape's `sh:description` upstream so the asymmetry does
not read as an oversight to the next person — it is the one question this
document asked twice.

### This reinterprets 10 of the corpus's SHACL violations

The 10 residual violations after the handle fixes are all values failing a
**URL** pattern:

```
focus=https://github.com/HippolyteKarakostas
path=pulse:githubUsername
message=Value does not match pattern '^https://github\.com/[A-Za-z0-9]...'
```

Those values are *bare*. Under v2.1.2 they are violations; under v3 they are
correct. So they are not a producer bug to fix, as
`tests/v2/corpus/BASELINE_FINDINGS.md` first concluded — **they are the
codebase already drifting toward v3's format ahead of the ontology.** Fixing
them to the URL form would mean undoing that work at migration time.

Recommendation: leave those 10 alone and let the v3 migration resolve them.

## 2.5 The discipline enumeration lost every value we can emit — PATCHED

Found while generating `Literal` types from the enumeration instances
(2026-09-08). This one is not a preference; under v3 as it stands, **every
discipline value this service is capable of emitting is a SHACL violation.**

The shapes constrain the field by class:

```turtle
sh:property [ sh:path pulse:discipline ; sh:class pulse:DisciplineEnumeration ] ;
```

`sh:class` is satisfied only when the value node has an `rdf:type` that reaches
`pulse:DisciplineEnumeration`. The v3 enumeration files declare **1606**
instances, and the restructure changed which tier gets declared:

| tier | v2.1.2 | v3 |
|---|---|---|
| 5 faculty roots (Natural sciences, Humanities, ...) | instances | **absent entirely** |
| ~41 second-tier categories (Computer engineering, ...) | instances | `rdfs:subClassOf` targets only, never typed |
| ~1606 leaves | absent | instances |

Measured against `schema/models/agent.py`, which is the vocabulary our agents
may choose from:

```
v2 agent enum members:            46
  ...that are v3 instances:        0      <- zero overlap
  ...that are v3 parents only:    41
  ...absent from v3 entirely:      5
```

The 41 are exactly v3's interior-only parents: present as the object of an
`rdfs:subClassOf`, never as the subject of an `a pulse:DisciplineEnumeration`.
`sh:class` walks `rdf:type/rdfs:subClassOf*` **from** the value node, so a node
with no `rdf:type` at all fails no matter how many children it has.
`wd:Q428691` (Computer engineering) is the clearest case — CLAUDE.md documents
it as the repository agent's fallback, and it is parent-only in v3.

The 5 with no v3 home at all are the whole root tier:

| QID | label |
|---|---|
| `wd:Q7991` | Natural sciences |
| `wd:Q34749` | Social sciences |
| `wd:Q80083` | Humanities |
| `wd:Q7112556` | Applied sciences |
| `wd:Q816264` | Formal sciences |

### Resolved 2026-09-08 — `ontology/patches/03-coarse-discipline-tiers.patch`

Confirmed as a real gap and fixed locally rather than left as an ask. The patch
restores all 46 coarse terms that v2.1.2 declared, on top of v3's 1606 leaves:

- the **5 faculty divisions** as instances, with their v2.1.2 labels
- the **41 top-level categories** as instances, each already present in the
  file as the parent of one or more leaves

Two deliberate departures from v2.1.2:

- `wd:Q11862829` ("academic discipline") is **not** reintroduced. v2.1.2
  referenced it as the faculty roots' parent but never declared it, and as a
  tag value it carries no signal. The 5 roots become true roots.
- `wd:Q395` (Mathematics) and `wd:Q413` (Physics) keep no parent, as in
  v2.1.2, rather than have one invented for them.

Result: `1652` discipline instances, and all 46 agent-emittable values
validate. `test_every_discipline_the_agents_may_emit_is_a_valid_v3_member`
asserts the whole vocabulary, so the patch dropping out of the series fails a
test instead of producing a graph-wide violation.

**Still to upstream.** Like patches 01 and 02, this belongs in PR #25 — see
`ontology/patches/README.md`. The original ask, for reference: too

Add `a pulse:DisciplineEnumeration` to the 41 second-tier categories (and,
if the root tier is meant to be usable, the 5 roots). It is additive, it breaks
no leaf, and it keeps `sh:class` satisfiable for coarse tags.

The alternative — we remap every emission to a leaf — is the wrong fix, and for
a reason this repo already has production evidence for. The catch-all
`wd:Q428691` was removed from both repository agents precisely because coarse
tagging was manufacturing false signal: 77% of a 441-repo batch carried the
catch-all *only*. Forcing leaf-only precision pushes the agents the other way,
into inventing specificity they cannot support. Coarse is sometimes the honest
answer, and it should stay expressible.

If the tiering is deliberate — leaves only, by design — then say so, and this
becomes a scoped remap plus a prompt change on our side rather than a patch.
Either way it has to be decided before v3 adoption: it is not a long tail of
edge cases, it is 100% of the field.

## 2.6 RDFS inference makes every DOI and ROR node fail its own closed shape — PATCHED

Found while measuring the canonical projection against the real shapes
(2026-09-08). Systemic, and it blocks phase 6 outright.

`ontology-definitions-canonical.ttl` declares:

```turtle
pulse:doi a rdf:Property ; rdfs:subPropertyOf schema:identifier .
pulse:ror a rdf:Property ; rdfs:subPropertyOf schema:identifier .
```

`validation/shacl_validation.py` validates with `inference="rdfs"`. So any node
carrying `pulse:doi` or `pulse:ror` materialises a `schema:identifier` triple —
and every closed shape ignores only `( rdf:type owl:sameAs )`:

```
ArticleShape         sh:ignoredProperties ( rdf:type owl:sameAs )
OrganizationShape    sh:ignoredProperties ( rdf:type owl:sameAs )
```

The result is that **conformant data fails validation**: an organization with a
ROR, or an article with a DOI, violates its own shape because of a triple the
ontology itself asked to be inferred. Measured across the 120-repo corpus, this
was the sole blocker on 4 of the first 8 runs before it was patched.

Note the shapes already anticipate this pattern for `owl:sameAs` — which is why
`pulse:samePersonAs ⊂ owl:sameAs` (and patch 02's `sameOrganizationAs`) do not
break. The same courtesy was not extended to `schema:identifier`.

### Resolved — `ontology/patches/04-ignore-inferred-identifier.patch`

Adds `schema:identifier` to `sh:ignoredProperties` on the two closed shapes
that have a subproperty of it. Two lines. The alternatives are worse: dropping
the `rdfs:subPropertyOf` axioms loses real semantics, and turning off RDFS
inference loses it everywhere.

Worth considering upstream: add `schema:identifier` to *every* closed shape's
ignored list, so the next property declared a subproperty of it does not
reintroduce this. Any of `pulse:orcidIdentifier`,
`pulse:githubRepositoryHandle` or the platform ids would.

## 2.7 A registry lookup cannot name itself as a source — PATCHED

Found while wiring the substrate writer (phase 3, 2026-09-09), by reading the
emitted TriG rather than by validating it.

The substrate anchors every entity to the `pulse:ExtractionOutput` for the
source that asserted it — `pulse:partOfRun` — and `ExtractionOutputShape`
carries `pulse:platform` to say which source that was. But the platform is
derived from the entity's profile or its id host, so a **ROR-identified
organization has no platform at all**: it arrives from a registry lookup during
extraction, not from an account on a hosting service. With no platform there is
no output to anchor to, and the organization fell through into the graph
reserved for extraction metadata — sitting beside the `ExtractionRun` that
describes the run, as though "Normalization Housing Foundation" were a fact
about the extraction rather than a fact the extraction found.

Nothing in the graph was invalid. `RawOrganizationShape` is open and requires
no anchor, so raw conformance stayed at 119/119 with the entity in the wrong
graph entirely — the same lesson as §2.5's `"entities"` handle.

`ExtractionOutputShape` is `sh:closed` over exactly three properties:

```turtle
pulse:ExtractionOutputShape
    sh:closed true ;
    sh:property [ sh:path prov:wasGeneratedBy ; sh:class pulse:ExtractionRun ] ;
    sh:property [ sh:path pulse:platform ; sh:class pulse:PlatformEnumeration ] ;
    sh:property [ sh:path prov:generatedAtTime ; sh:datatype xsd:dateTime ] .
```

So `pulse:platform` is the *only* place an output can name its source. A
platformless output for registry lookups is legal — `pulse:platform` has no
`sh:minCount` — but then the substrate records that something was looked up
without recording where, which defeats the point of anchoring.

### Resolved — `ontology/patches/05-ror-platform.patch`

One member: `pulse:ROR a pulse:PlatformEnumeration`. This is not a widening of
what "platform" means, because the enumeration already reads that way
upstream — `pulse:ORCID` is a member, described in the ontology itself as "the
ORCID researcher-identifier registry", and `pulse:Zenodo` is an archive. The
enumeration is in practice "external source we hold an identity or record on",
and ROR is the one such source the pipeline already queries on every run that
lacks a member.

Consequences, deliberately accepted:

- `pulse:PlatformEnumeration` goes 7 → 8 members, so
  `enumerations.Platform` gains `"pulse:ROR"` and
  `test_enumeration_member_counts` moves with it.
- `pulse:ROR` and the existing property `pulse:ror` now differ only in case.
  That is the same shape as `pulse:ORCID` (member) beside
  `pulse:orcidIdentifier` (property) and is legal in Turtle, but it is a real
  reading hazard — worth a rename to `pulse:RORRegistry` upstream if the
  maintainers dislike it.
- ROR is still **absent from `pulse:IdentifierSchemeEnumeration`**, which has
  `pulse:GRID` ("superseded by ROR but still in wide use"), `pulse:Ringgold`
  and `pulse:ISNI`. Not patched here — `pulse:ror` is a first-class property so
  nothing needs the scheme member yet — but the omission looks accidental and
  is worth raising with the same PR.

The alternatives, for the record. A platformless
`urn:pulse:output:{run}:ror` needs no ontology change and is reversible, but
leaves the output mute about its own source. Treating ROR organizations as
canonical-only is cleanest conceptually — a ROR id *is* the identity — but then
the substrate cannot explain where a ROR organization's name came from, which
is exactly the question the layer exists to answer.

## 2.8 The raw layer references a class it cannot validate — PATCHED

Found while filling the last hole in the canonical graph (2026-09-09).

`RawPersonShape` declares:

```turtle
sh:property [ sh:path pulse:hasContribution ; sh:class pulse:Contribution ; ... ] ;
```

So the raw layer expects `pulse:Contribution` nodes to exist in a substrate
graph. But **no raw shape targets that class** — `pulse:Contribution` is the
one entity type the raw layer references and cannot validate. `sh:class`
resolves (the class is declared in the canonical definitions), so a
contribution in the substrate is not *invalid*; it is simply unshaped, which
is worse: the layer would carry an entity type nothing checks.

### Why it mattered, which took a while to see

This repo had read that absence as intent — "a contribution is a *derived*
edge, so it belongs to the unifier" — and excluded contributions from the
substrate accordingly. **The premise was wrong about the count.**
`agents/rule_based/contribution_agent.py` reads GitHub's `contributions`
field: the number is something a platform reports, not something we compute.
What is derived is the aggregate across platforms and runs.

The consequence was not a violation anywhere. It was a hole:

| | |
|---|---|
| `/v2/extract` output | 46 `pulse:Contribution` entities |
| store-side `graph:canonical` | **0** |

And it could not be closed without this patch, because the canonical
`ContributionShape` requires `pulse:contributionCount` (`sh:minCount 1`) — so
with the count absent from the substrate the unifier had nothing to assert, and
an edge without a count would fail the (now enforcing) canonical gate. Nothing
reported the hole either: `PersonShape` puts no `sh:minCount` on
`pulse:hasContribution`, so a canonical Person with no contributions is
perfectly conformant.

### Resolved — `ontology/patches/06-raw-contribution-shape.patch`

`RawContributionShape`, `sh:closed`, mirroring the canonical one's properties
with **no `sh:minCount` anywhere**. That asymmetry is deliberate and matches
the validation split (§3l of the handoff): the substrate is append-only and the
only durable copy of what a run found, so it must never refuse a slice for an
incomplete assertion. The requirement bites at the canonical gate.

Also deliberate: no `pulse:partOfRun`, exactly as `RawMembershipShape` has
none. The anchor belongs to entities a source describes, not to the edges
between them, so the named graph is a contribution's attribution.

Worth raising with the same PR: the ontology's own definition of the class
reads *"a person's **aggregated** contributions to a repository"*, which is the
reading that produced the original exclusion. If the class is meant to be
per-platform in the raw layer and aggregated in canonical — which is what this
patch assumes — the definition could say so.

## 3. Decisions we need confirmed, not changed

### 3.1 Substrate openness

`PROVENANCE_ARCHITECTURE.md` asks for a substrate shape set with `sh:closed false`, so
raw assertions are never rejected at write time. PR #25's raw layer is mostly closed:

| | Shapes |
|---|---|
| `sh:closed false` | `RawPersonShape`, `RawOrganizationShape` |
| `sh:closed true` | the other 10, including `RawPlatformProfileShape`, `RawOrganizationProfileShape`, `RawRepositoryShape`, `RawArticleShape`, `RawMembershipShape` |

Opening exactly the two entity types that hold provisional, cross-platform-merged data
is a defensible reading of collapse-late, and possibly better than opening everything.
**Please confirm it is deliberate.** If a platform starts returning a field we have no
property for, a closed `RawRepositoryShape` rejects the raw assertion — which is the
thing the substrate layer exists to avoid.

### 3.2 Provenance encoding — RDF-star is store-only

PR #25's provenance file documents the finding that settles this, more
definitely than the spike I proposed would have:

> pySHACL has no RDF-star support at all as of 0.40.0; rdflib can't even parse
> Turtle-star as of 7.6.0.

This repo pins `rdflib==6.3.2` and `pyshacl==0.28.1` — **both older than the
versions cited as lacking support.** RDF-star is therefore unusable anywhere
the Python side parses or serialises RDF.

It stays viable under one hard constraint: **quoted triples exist only inside
Oxigraph.** The unifier writes them via SPARQL `INSERT`; Python reads them back
only as SPARQL `SELECT` bindings, which are plain terms. No rdflib parse, no
pyshacl pass, no JSON-LD serialisation of `graph:prov`.

Consequences to design for rather than discover in phase 7:

- `graph:prov` can never be SHACL-validated — which PR #25 already states as
  deliberate, so both positions agree.
- Provenance cannot appear in a JSON-LD response. A provenance endpoint must
  build JSON from SPARQL results.
- If provenance ever *must* round-trip JSON-LD, the plain-RDF
  `pulse:Observation` encoding becomes mandatory, and ask 1 turns from optional
  into blocking.

### 3.3 Nested repository paths — already fine

GitLab nests groups arbitrarily (`group/subgroup/project`).
`pulse:RepositoryHandleShape`'s pattern is
`^[a-zA-Z0-9\-_.]+(/[a-zA-Z0-9\-_.]+)+$` — the `+` on the group admits any depth, so
this already works. No change needed. Nested groups map onto `org:unitOf` /
`org:hasUnit`.

---

## 4. Consequences for this repo, for reference

These are ours to do, listed so the ontology side can see the blast radius.

- `pulse:ror` replaces `schema:identifier` as the ROR carrier, so the `pulse:ror`
  stripping in `pipeline/stages/jsonld_build.py` becomes wrong and must go — it would
  delete an identity anchor.
- `pulse:githubUsername` and `pulse:githubOrganizationHandle` disappear from
  `PersonShape` / `OrganizationShape`, so every Person without an ORCID and every
  Organization without a ROR is re-identified. `idSource` loses two enum members.
- Property-shape renames to absorb: `*PropertyShape` → `*Shape`,
  `GithubUsernameShape` → `PlatformUsernameShape`,
  `GithubRepo{Stars,Forks}Shape` → `Repository{Stars,Forks}Shape`,
  `Infoscience*IdentifierShape` → `PlatformInternalIdShape` / `PlatformNodeIdShape`.
- Four new canonical types to support: `PlatformProfile`, `OrganizationProfile`,
  `Project`, `Deposit`. `Project` is load-bearing rather than optional — it is how
  `PROVENANCE_ARCHITECTURE.md` links the same repository across platforms without
  merging identity.
- Four new raw-only concepts: `Collection`, `Community`, `Funding`,
  `ExternalIdentifier`.

## 5. Summary of asks

| # | Ask | Severity |
|---|---|---|
| 1 | `pulse:platformInstance` on the profile shapes | blocking for GitLab |
| 2 | Organization counterpart to `samePersonAs` | needed for the id migration |
| 3 | Confirm the raw-layer openness split is deliberate | confirmation |
| 4 | Port the reified `Observation` vocabulary | only if `graph:prov` must round-trip JSON-LD |
| 5 | Upstream `03-coarse-discipline-tiers.patch` — the coarse discipline tiers (§2.5) | **patched locally**; was blocking 100% of `pulse:discipline` values |
| 6 | Upstream `04-ignore-inferred-identifier.patch` — ignore the inferred `schema:identifier` (§2.6) | **patched locally**; was blocking every DOI article and ROR organization |
| 7 | Upstream `05-ror-platform.patch` — `pulse:ROR` as a platform member (§2.7) | **patched locally**; without it registry-sourced organizations have no provenance anchor |
| 8 | Upstream `06-raw-contribution-shape.patch` — `RawContributionShape` (§2.8) | **patched locally**; without it the raw layer references a class it cannot validate, and canonical has no contributions |

**Closed 2026-09-08:** the bare-identifier direction and the `pulse:ror`
exception are both intentional (§2.4). The looser cardinality on 14 properties
is accepted as-is — no `sh:maxCount` patch. Consequence to design around: the
generated models expose those as `list[...]`, so consuming code cannot assume a
scalar. `schema:name`, `pulse:ror` and `pulse:orcidIdentifier` are lists even
where exactly one value is expected.

Withdrawn after verification: the `ExtractionRun`/`prov:Activity` link and the
named-graph association are already in PR #25, and the RDF-star decision is
already documented there with the library evidence behind it.
