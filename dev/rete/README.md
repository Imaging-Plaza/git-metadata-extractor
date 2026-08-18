# rete Scholarly-Graph alignment

`scholar.ttl` (`https://w3id.org/rete/scholar#`) ties the sibling
scholarly datasets — **DBLP, DataCite, Crossref, OpenAIRE, OpenCitations, ORCID,
ROR, Zenodo, CORDIS, GoTriple** (10 datasets) — into one coherent, queryable
graph **without merging their schemas**.
Each dataset ontology still stands alone; this thin upper layer just lets one
query span all of them, and defines the instance-IRI rule that makes
cross-dataset links free.

**Crossref is the Funder-ID authority and the citation-graph contributor.** Its
`cx:funderId` (a DOI under `10.13039/…`) feeds `scholar:fundref`, and it brings
the ~2B-edge reference/citation network — `cx:cites ⊑ cito:cites` for DOI-matched
edges, plus a reified `cx:Reference` (⊑ `bibo:BibliographicReference`) that keeps
each reference's provenance and raw string. Its `cx:citedDOI` (the cited endpoint)
is also `⊑ scholar:doi`, so citation targets join the other datasets by DOI.

**Zenodo is DataCite-based.** A Zenodo record is DataCite kernel-4.5 metadata
(CERN.ZENODO is a DataCite client), so `zen:Record` ⊑ `dcite:Resource` and
Zenodo reuses the DataCite relation vocabulary; `zenodo.ttl` adds only what
DataCite drops (communities, concept DOIs, IIIF, usage stats, biodiversity
treatments). Under the IRI policy a Zenodo record and its DataCite twin mint to
the same `https://doi.org/{doi}` node and merge for free.

**ROR is the organization authority.** Its orgs are minted at
`https://ror.org/{id}`, and the IRI policy below mints every other dataset's
ROR-identified org at the same IRI — so ROR's rich org node (name, geo, type,
parent/child) is the shared target that ORCID affiliations, DataCite funders and
OpenAIRE orgs merge into.

**CORDIS (EURIO) adds the projects & funding dimension.** It uses EURIO's native
`s66:` terms (like DBLP's `dblps:`) and introduces two hub classes —
`scholar:Project` and `scholar:Grant` — that also unify OpenAIRE's and ORCID's
project/funding entities. CORDIS publications carry a DOI, so they merge with
DataCite/OpenAIRE/Zenodo works at `https://doi.org/{doi}`.

**GoTriple adds the SSH dimension.** GoTriple (OPERAS/TRIPLE) indexes ~6M Social
Sciences & Humanities publications with full-text links — a thematic complement
to the STEM-heavy siblings. `gtr:Document ⊑ scholar:Work`, `gtr:Author ⊑
scholar:Person`, `gtr:doi ⊑ scholar:doi` (bare, so ~69% of records merge on the
DOI IRI), plus a 27-discipline SKOS scheme and linked SSH-LCSH subjects.
`go-triple.ttl` declares its hub alignment *inline* (so it works standalone) and
scholar.ttl repeats it — redundant but harmless.

## 1. Hub classes & join-key properties

Every dataset's class/property is declared a **subclass / subproperty** of a
shared hub term (via `rdfs:subClassOf` / `rdfs:subPropertyOf`, *not*
`owl:equivalentClass` — the datasets are similar, not identical):

| Hub term | Subsumes |
|---|---|
| `scholar:Work` | `dblp:Publication`, `dcite:Resource`, `cx:Work`, `oc:BibliographicResource`, `oaire:ResearchProduct`, `orcid:Work`, `zen:Record` (also ⊑ `dcite:Resource`), `s66:Result`, `gtr:Document` |
| `scholar:Agent` | `dblp:Creator`, `dcite:Agent`, `cx:Agent`, `oc:Agent` |
| `scholar:Person` | `dblp:Person`, `orcid:Researcher`, `oaire:Person`, `gtr:Author` |
| `scholar:Organization` | `oaire:Organization`, `orcid:Organization`, `ror:Organization`, `s66:Organisation` |
| `scholar:Funder` | `dcite:Funder`, `cx:Funder`, `ror:Funder`, `s66:FundingAgency` |
| `scholar:Project` | `oaire:Project`, `s66:Project` |
| `scholar:Grant` | `oaire:Funding`, `orcid:Funding`, `cx:Funding`, `s66:Grant` |
| `scholar:doi` | `dblp:doi`, `dcite:doi`, `cx:doi`, `cx:citedDOI`, `oc:doi`, `orcid:doi`, `gtr:doi` |
| `scholar:orcid` | `dblp:orcid`, `dcite:orcid`, `cx:orcid`, `oc:orcidId`, `orcid:orcidId` |
| `scholar:ror` | `dcite:rorId`, `ror:rorId` |
| `scholar:grid` / `scholar:fundref` / `scholar:isni` / `scholar:wikidata` | `ror:grid` / `ror:fundref` (+ `cx:funderId`) / `ror:isni` / `ror:wikidata` (ROR + Crossref are the crosswalk authorities) |
| `scholar:pmid` | `oc:pmid` |
| `scholar:isbn` | `dblp:isbn`, `cx:isbn`, `oc:isbn` |

So `SELECT ?w WHERE { ?w a scholar:Work }` returns works from every dataset, and
`?w scholar:doi ?d` matches any dataset that exposes a DOI (Zenodo's version DOI
is `dcite:doi` ⊑ `scholar:doi`; its concept/all-versions DOI is `zen:conceptDoi`,
kept separate so the two don't conflate).

> OpenAIRE keeps DOIs inside `oaire:Pid` (`oaire:pidScheme "doi"` +
> `oaire:pidValue`), not a direct property — it joins via that pattern or via
> the canonical IRI below, not `scholar:doi`.

## 2. Canonical instance-IRI policy (the "free join")

Apply this in **every `.rete` build** so the same real-world entity gets the
**same IRI** in every dataset — then a union graph merges them with no
`owl:sameAs` needed:

| Entity | Canonical IRI |
|---|---|
| Work with a DOI | `https://doi.org/{doi}` (lowercased, bare) |
| Person with an ORCID | `https://orcid.org/{orcid}` |
| Organization with a ROR | `https://ror.org/{ror}` |
| Funder (Crossref Funder ID) | `https://doi.org/10.13039/{id}` (else its ROR) |
| Serial/venue with an ISSN | `https://portal.issn.org/resource/ISSN/{issn}` |
| Work with only a PMID | `https://pubmed.ncbi.nlm.nih.gov/{pmid}` |
| No shared PID | `https://w3id.org/rete/{dataset}/{kind}/{native-id}` |

Always **also** keep the native id as the dataset's own datatype property
(`dcite:doi`, `dblp:recordKey`, `oc:omid`, …) so native lookups still work.

When two records clearly match but you can't mint one canonical IRI, link them
with **`skos:exactMatch`** (safe) rather than **`owl:sameAs`** (which merges all
statements and can over-collapse on version-group DOIs). `owl:sameAs` is safe
for person ORCIDs.

## Validation

`scholar.ttl` + the ten base ontologies load into one graph (4,145 triples)
with **zero broken cross-references** (every rete-namespaced
`subClassOf`/`subPropertyOf`/`equivalent*`/`inverseOf`/`domain`/`range` target
resolves to a defined term). Regenerate/verify with the union check in the
session notes.

## 3. Review considerations

Each dataset ontology was reviewed with the same checks: it must parse; every
cross-reference into the rete namespaces must resolve (the broken-reference
guard above); and every **external** term it maps to — SPAR (FaBiO / CiTO / PRO /
DataCite), FRAPO, schema.org, PRISM, W3C Org, FOAF — was verified to actually
exist against its published spec. That last check matters: a mistyped
`owl:equivalentClass`/`rdfs:subPropertyOf` target silently points at a
non-existent term and the alignment quietly does nothing.

### Design decisions (why it's built this way)

- **`rdfs:subClassOf` / `rdfs:subPropertyOf`, not `owl:equivalentClass`.** The
  datasets are *similar, not identical*; a shared hub subsumes them without
  asserting they are the same thing.
- **Linking is at the instance level, via the canonical-IRI policy** (§2), not
  via dense dataset-to-dataset axioms. Same PID ⇒ same IRI ⇒ free merge.
- **No `owl:AllDisjointClasses` across the hub.** Aggregated real data is messy
  (e.g. an id typed as two things); disjointness + a stray triple would make the
  whole union *inconsistent* under reasoning. Omitting it is the safe choice.
- **Reuse the source ontology where one exists** rather than reinventing: DBLP →
  its own `dblps:` schema, CORDIS → EURIO `s66:`, OpenCitations → the OCDM/SPAR
  model, ROR → W3C Org + schema.org.
- **`skos:exactMatch`, not `owl:sameAs`, for fuzzy matches** without a shared IRI
  (owl:sameAs merges *all* statements and over-collapses version-group DOIs).

### Per-dataset status & caveats

| Dataset | Status | Notes / caveats |
|---|---|---|
| **DataCite** | clean | reified `dcite:PidRelation` for the PID-Links edges; correctly avoids the non-existent CiTO terms (uses `cito:cites` + `dcterms:relation`) |
| **OpenCitations** | clean | mirrors the OCDM; the natural DOI+PMID+OpenAlex+OMID crosswalk hub |
| **DBLP** | clean | reuses DBLP's own `dblps:` schema (best-aligned); no CiTO/FRAPO used |
| **ROR** | clean | the **organization authority**; orgs mint at `https://ror.org/{id}` |
| **Zenodo** | clean | DataCite-based (`zen:Record ⊑ dcite:Resource`); the concept/all-versions DOI is kept separate as `zen:conceptDoi` so it doesn't conflate with the version DOI |
| **ORCID** | **fixed** | FRAPO prefix was `purl.org/spar/frapo/` (non-existent) → corrected to `purl.org/cerif/frapo/`; also filled full column coverage |
| **OpenAIRE** | **fixed** | 6 props mapped to non-existent CiTO terms (`references`, `isSupplementTo`, `continues`, + inverses) → remapped to `dct:references` / `dct:relation`. DOIs live inside `oaire:Pid` (`pidScheme`/`pidValue`), **not** a direct property → joins via that pattern or the canonical IRI, not `scholar:doi` |
| **CORDIS** | clean* | uses EURIO `s66:` natively. `s66:doi` is a full DOI **URL** (not bare) → intentionally **not** `⊑ scholar:doi`; CORDIS works still merge at the `https://doi.org/{doi}` IRI. CORDIS orgs carry no ROR → join at hub-class level only. *Minor: `s66:Concept` is used as a range but never declared (should be `skos:Concept`) |
| **Crossref** | **fixed** | `cx:hasReference ⊑ fabio:hasReference` — `fabio:hasReference` doesn't exist → super-property dropped (kept standalone). *Minor left as-is: `cx:containerTitle` (a literal) `⊑ schema:isPartOf` (normally relates to the container resource, not its title) — a slight type/semantic smell* |
| **GoTriple** | clean | SSH (Social Sciences & Humanities) complement (`gtr:Document ⊑ scholar:Work/fabio:Expression`); 27-discipline SKOS scheme + linked SSH-LCSH subjects. `gtr:doi` is bare → correctly `⊑ scholar:doi`. Declares its hub alignment both inline (usable standalone) and in scholar.ttl — redundant but harmless |

### External-vocabulary gotchas (verified against the specs)

- **FRAPO's namespace is `http://purl.org/cerif/frapo/`** — *not* the `purl.org/spar/frapo/` the other SPAR ontologies use. `Grant` / `Funding` / `Payment` / `FundingAgency` / `FundingProgramme` / `isSupportedBy` / `hasGrantNumber` / `hasFundingAgency` all exist there.
- **CiTO does not define** `references` / `isReferencedBy`, `isSupplementTo` / `isSupplementedBy`, `continues` / `isContinuedBy`, `obsoletes` / `isObsoletedBy`. It *does* have `cites`, `isCitedBy`, `reviews`, `documents`, `compiles`, `describes`, `citesAsRelated` (+ inverses). Use Dublin Core (`dct:references`, `dct:relation`) for the rest.
- **FaBiO has no `hasReference`** (reference lists are BiRO/BIBO territory). It *does* have `TechnicalStandard`, `hasPublicationYear`, `ProceedingsPaper`, `ReportDocument`, `AcademicProceedings`, `ReferenceEntry`, etc.
