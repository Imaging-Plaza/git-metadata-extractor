# Ontology patches

Local, reviewable deltas against the pinned `vendor/open-pulse-ontology`
submodule.

**There are none at the moment.** The series 01–09 below was upstreamed in
sdsc-ordes/open-pulse-ontology#27 (merged into #25) and is part of `develop`
since `83074e9` (2026-10-01), which is now the pin. The table stays as the
record of why each change exists: code, tests and docs still refer to them as
"patch NN".

The mechanism stands for future deltas. The submodule stays on an **immutable
upstream commit**; patches are applied at model-generation time, never
committed into the submodule, so:

- CI resolves the same upstream SHA every time
- the entire delta from upstream is reviewable in one place
- each patch maps 1:1 to an upstream commit, with the rationale written up in
  `../../ONTOLOGY_V3_REQUIREMENTS.md`

When a patch lands upstream, re-pin the submodule and delete the patch file in
the same commit — `prepare_ontology.py` cannot re-apply a delta that is already
there.

## The upstreamed series (01–09)

| Patch | Ask | Why |
|---|---|---|
| `01-platform-instance.patch` | §2.1 | `pulse:platformInstance` + `PlatformInstanceShape`, wired into `PlatformProfileShape`, `OrganizationProfileShape` and both `Raw*` counterparts. Without it, `epfl` on `gitlab.epfl.ch` and on `gitlab.com` are the same `(platform, handle)` pair and collapse into one profile — two organizations, one node. Blocking for GitLab harvesting. |
| `02-same-organization-as.patch` | §2.3 | `pulse:sameOrganizationAs ⊂ skos:exactMatch`, the organization counterpart to the existing `pulse:samePersonAs`. Carries the `/v2` id migration: an organization re-identified from its github handle to its ROR anchor keeps the old id followable. The superproperty is `skos:exactMatch` rather than `owl:sameAs` for the reason patch 09 gives. |
| `03-coarse-discipline-tiers.patch` | §2.5 | Declares the 46 coarse discipline terms v2.1.2 had: the 5 faculty divisions and the 41 top-level categories. v3 declares only leaf disciplines, leaving the 41 as bare `rdfs:subClassOf` parents with no `rdf:type` — and `sh:class` walks `rdf:type/rdfs:subClassOf*` **from** the value node, so a parent with no type fails regardless of its children. Without it, all 46 disciplines the agents can emit are SHACL violations: not a long tail, 100% of the field. |
| `04-ignore-inferred-identifier.patch` | §2.6 | Adds `schema:identifier` to `sh:ignoredProperties` on `ArticleShape` and `OrganizationShape`. `pulse:doi` and `pulse:ror` are declared `rdfs:subPropertyOf schema:identifier`, and the gate runs `inference="rdfs"`, so every DOI article and every ROR organization materialises a `schema:identifier` triple that its own `sh:closed` shape then rejects. Blocks phase 6 outright: the gate cannot be made enforcing while conformant data fails. |
| `05-ror-platform.patch` | §2.7 | Adds `pulse:ROR` to `pulse:PlatformEnumeration`. The substrate anchors every entity to the `pulse:ExtractionOutput` for the source that asserted it, via `pulse:partOfRun` — but a ROR-identified organization has no platform, so it got no anchor and fell into the graph reserved for extraction metadata. `ExtractionOutputShape` is `sh:closed` over exactly three properties, so `pulse:platform` is the only place an output can name its source. `pulse:ORCID` is already a member on the same reading — a registry, not a code forge — so this follows existing practice rather than widening the concept. |
| `06-raw-contribution-shape.patch` | §2.8 | Adds `RawContributionShape`. `RawPersonShape` already declares `pulse:hasContribution` with `sh:class pulse:Contribution`, so the raw layer expects these nodes — but no raw shape targeted the class, making `pulse:Contribution` the one entity type the raw layer referenced and could not validate. The consequence was not a warning but a silent hole: contributions were excluded from the substrate as "derived", the canonical `ContributionShape` requires `pulse:contributionCount`, and so the store-side canonical graph had **zero** contributions while `/v2/extract` returned 46. The count is reported by GitHub, not computed by us. |
| `07-source-platform-enumerations.patch` | §2.9 | Adds `pulse:OpenAlex`, `pulse:ETHZResearchCollection`, `pulse:SNSF`, `pulse:RenkuLab`, `pulse:SWISSUbase` and `pulse:EPFLGraph` to `pulse:PlatformEnumeration`. The substrate anchors every entity to the `pulse:ExtractionOutput` for the source that asserted it, and `ExtractionOutputShape` is `sh:closed` with `pulse:platform` as the only place an output can name that source — so a source with no enumeration member cannot be attributed at all. All six are read by this service today (`providers/*_rag.py`) and had no term. **DuckDuckGo is deliberately excluded**: a web search engine is a discovery mechanism rather than a source of record, so giving it a member would widen the concept rather than complete it. The EPFL Graph was excluded on the same reading until 2026-09-23, on the grounds that it supplies discipline vocabulary rather than entities — but it also holds EPFL person, unit and publication records keyed by sciper, which is entity data this service can attribute, so it is a source of record for those. Follows patch 05's reading, on which ORCID and ROR are registries rather than code forges. |
| `08-source-snapshot.patch` | §2.10 | Adds `pulse:SourceSnapshot` (`rdfs:subClassOf prov:Entity`), `prov:used`, a `SourceSnapshotShape`, and wires `prov:used` into `ExtractionOutputShape`. Before it, an output could name *which* source it read but never *which version of that source's data* — `ExtractionOutputShape` is `sh:closed` over exactly three properties, so there was nowhere to put it. That makes a result impossible to reproduce against the same data rather than merely re-request: the ORCID index is rebuilt, the same query returns more, and nothing in the graph distinguishes the two runs. The snapshot carries `schema:softwareVersion` — the `open_pulse_sources` build that produced the index, which is already the cross-repo pin — and its IRI embeds that version, so two runs against one build share a snapshot while producing different outputs. Reuses `schema:softwareVersion` rather than minting `pulse:indexVersion`: the index's version *is* the version of the software that built it. |
| `09-same-as-is-not-identity.patch` | §2.11 | Retargets `pulse:samePersonAs` from `owl:sameAs` to `skos:exactMatch`. Adopted from [PR #170](https://github.com/Imaging-Plaza/git-metadata-extractor/pull/170) ask #12, which takes it in turn from the rete `scholar.ttl` rule *"link them with `skos:exactMatch` (safe) rather than `owl:sameAs` (which merges all statements)"*. `owl:sameAs` licenses a reasoner to merge every statement of both nodes — including the per-source facts the raw layer exists to keep separable, so two profile-bearing persons linked by the unifier would carry two biographies, two follower counts and two `pulse:company` values asserted of one individual with nothing recording which came from where. That is a reasoner-level contradiction produced *by* the unifier's own output. Patch 02 mints the organization twin with the corrected superproperty; this patch fixes the upstream person term. |

Two earlier asks were **withdrawn** after reading
`src/ontology/ontology-definitions-provenance.ttl` rather than only the shapes:
`ExtractionRun rdfs:subClassOf prov:Activity` is already declared upstream, and
the RDF-star provenance decision is already documented there with the library
evidence behind it. See §0 of `ONTOLOGY_V3_REQUIREMENTS.md`.

## Applying

```bash
just ontology-prepare      # checkout submodule + apply patches, idempotent
```

Or by hand, in order, from the repo root:

```bash
git -C vendor/open-pulse-ontology checkout -- .
for p in ontology/patches/0*.patch; do
    git -C vendor/open-pulse-ontology apply "../../$p"
done
```

## Upstreaming

When the generated models are green, each patch becomes one commit on PR #25:

```bash
cd vendor/open-pulse-ontology
git checkout -b feat/platform-instance
git apply ../../ontology/patches/01-platform-instance.patch
git commit -am "feat(shapes): add pulse:platformInstance for self-hosted platforms"
```

Suggested branch/commit per patch:

| Patch | Branch | Commit subject |
|---|---|---|
| 01 | `feat/platform-instance` | `feat(shapes): add pulse:platformInstance for self-hosted platforms` |
| 02 | `feat/same-organization-as` | `feat(prov): add pulse:sameOrganizationAs alongside samePersonAs` |
| 03 | `fix/coarse-discipline-tiers` | `fix(enumerations): declare the coarse discipline tiers as instances` |
| 04 | `fix/ignore-inferred-identifier` | `fix(shapes): ignore the schema:identifier inferred from doi and ror` |
| 05 | `feat/ror-platform` | `feat(enumerations): add pulse:ROR so registry lookups can be attributed` |
| 06 | `fix/raw-contribution-shape` | `fix(shapes): add RawContributionShape so the raw layer can validate what it references` |
| 07 | `feat/source-platform-enumerations` | `feat(enumerations): add the five indexed sources that had no platform term` |
| 08 | `feat/source-snapshot` | `feat(shapes): let an ExtractionOutput name the source version it read` |

Delete the patch file once its commit is merged. An empty `ontology/patches/`
means the submodule pin alone describes the ontology, which is the goal.
