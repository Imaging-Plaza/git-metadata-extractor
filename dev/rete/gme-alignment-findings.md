# What the rete scholarly graphs mean for GME

Survey of the rete catalog (65 published datasets) and of
[`scholar.ttl`](scholar.ttl) — the local alignment ontology (v1.4.0,
2026-07-27) — against what git-metadata-extractor emits and what we are
proposing for v4.0.0.

Date: 2026-07-31.

---

## The headline: our IRIs already join

`scholar.ttl`'s canonical instance-IRI policy and GME's identifier conventions
are the same rule, arrived at independently:

| Entity | rete policy | GME 3.0.0 |
|---|---|---|
| Work with a DOI | `https://doi.org/{doi}` | `https://doi.org/{doi}` |
| Person with an ORCID | `https://orcid.org/{orcid}` | `https://orcid.org/{orcid}` |
| Organization with a ROR | `https://ror.org/{ror}` | `https://ror.org/{ror}` |
| No shared PID | `https://w3id.org/rete/{dataset}/{kind}/{id}` | `urn:pulse:{uuid}` |

So GME output merges with the rete scholarly graphs **with no `owl:sameAs` and
no mapping table** — same real-world entity, same IRI, union graph. That is the
"free join" the alignment ontology is built around, and we get it for nothing
because we already mint the same way. The only divergence is the no-PID
fallback, which is dataset-local on both sides and therefore harmless.

## ROR is the organization authority — externally corroborated

`scholar.ttl` makes ROR *the* organization authority: every other dataset's
ROR-identified org is minted at the ROR IRI, so ROR's node (name, geo, type,
parent/child) becomes the shared merge target for ORCID affiliations, DataCite
funders and OpenAIRE orgs. `README.md` marks ROR "clean — **the organization
authority**".

This is independent support for **ask #4** in
[`../v4.0.0/README.md`](../v4.0.0/README.md): PR #25's `PlatformEnumeration`
lists GitHub, GitLab, Bitbucket, Infoscience, ORCID, HuggingFace and Zenodo but
**not ROR** — the one registry the wider ecosystem treats as authoritative for
organizations, and the source of our own `org:Organization` `@id`s.

## A finding worth acting on: `samePersonAs` should not be a `sameAs`

`scholar.ttl` is explicit (lines 74-76):

> When two records clearly match but you cannot mint one canonical IRI, link
> them with `skos:exactMatch` (safe) rather than `owl:sameAs` (which merges all
> statements).

The provenance profile declares `pulse:samePersonAs rdfs:subPropertyOf
owl:sameAs`. Under an OWL reasoner that merges *every* statement of both
persons — including the raw platform facts that are deliberately allowed to
disagree. Two `PlatformProfile`-bearing persons unified by `samePersonAs` would
end up with two `pulse:company` values, two follower counts, two biographies,
all asserted of one individual, and no way to tell which came from where.

That is a reasoner-level contradiction produced *by* the unifier's own output,
and it defeats the reason the raw/canonical split exists.

**Proposed:** make `pulse:samePersonAs` a subproperty of `skos:exactMatch`, or
leave it standalone with a comment that it is a unifier assertion and not an
identity axiom. Tracked as ask #12 in
[`../v4.0.0/README.md`](../v4.0.0/README.md).

## An evaluation harness we do not have

`scholar` (51 KB) and `scholar-noisy` (50 KB) are the same synthetic scholarly
world, the second with 25% injected noise, and the noise is exactly our failure
surface. From the dataset's own example queries:

| Injected defect | What it exercises in GME |
|---|---|
| 16 authors with the ORCID stripped | the ID hierarchy fallback (ORCID → login → uuid) and `promote_failed_id_entities` |
| 298 papers citing later-dated papers | the date-order guard both membership agents apply (`hasBeginning <= hasEnd`) |
| 20 titles with stray leading whitespace | string normalisation in `canonicalization/` |
| citations rewired across fields; closure inflated 16 → 228 | `prune_dangling_refs`, and dedup precision generally |

Because the clean twin is ground truth, this pair lets us **measure**
reconciliation precision and recall rather than assert them. We currently have
no such fixture: our 1500 tests check that stages run and that shapes hold, not
how *accurate* the entity resolution is. Cheap to wire in — both files are
under 51 KB.

## Datasets that overlap our provider surface

Live in the catalog now:

| Dataset | Size | Relevance |
|---|---|---|
| `zenodo-records` | 2.09 GB | every Zenodo record (7.76M) as DataCite. Keeps the concept DOI separate from the version DOI as `zen:conceptDoi` — the same distinction PR #25 introduces as `pulse:conceptDoi`, independently arrived at. Our Zenodo RAG index covers a scoped subset of this. |
| `gotriple` | 832 MB | 2.7M Social-Sciences & Humanities publications, 27-discipline SKOS scheme — a discipline vocabulary to compare against EPFL Graph. |
| `opencitations` | 86 KB | citation neighbourhood; the DOI+PMID+OpenAlex+OMID crosswalk hub. Maps to `pulse:references`. |
| `openalex-astrocytes` | 172 KB | OpenAlex subset — the shape our OpenAlex index would take as RDF. |
| `orkg` | 393 KB | ORKG research contributions. |
| `biosyslit` | — | Zenodo Biodiversity Literature Repository; carries ORCID. |
| `wikidata`, `wikidata-100mb`, `wikidata-xxl`, `wikidata-ontology` | 100 MB → 600M triples | we already emit Wikidata QIDs for disciplines (`wd:Q428691` is our fallback). `wikidata-ontology` is every Wikidata class — a way to validate our discipline IRIs resolve to real classes. |

**Not published in this catalog**, despite being aligned in `scholar.ttl` and
reviewed in `README.md`: dedicated **ORCID, Crossref, DBLP, DataCite, OpenAIRE,
ROR and CORDIS** graphs. Their ontologies exist (prefixes under
`https://w3id.org/rete/…`, with a per-dataset review status table) but the
`.rete` files are not in this server's list — ORCID and ROR data appears only
*inside* other graphs (`zenodo-records`, `biosyslit`). So the alignment layer is
written ahead of the data for those seven.

If/when the ROR and ORCID graphs land, they are the two that matter most to us:
they are the authorities behind our two most important `@id` forms, and they
would let `resolve_bio_to_ror` and the ORCID lookups run against a local
range-read graph instead of live APIs.

## Also worth knowing

- **`hf:Model` / `DatasetRepo` / `Space` / `Paper` are `⊑ scholar:Work`** in the
  alignment, with Hub papers minted at `https://doi.org/10.48550/arxiv.{id}`.
  That treats HF repositories as scholarly outputs — the same position PR #25
  takes by giving HF repository kinds first-class properties. Also a hint for
  our `IdentifierSchemeEnumeration` ask: arXiv is already there.
- **CORDIS/EURIO adds projects and grants** (`scholar:Project`,
  `scholar:Grant`, unifying `oaire:Funding`, `orcid:Funding`, `cx:Funding`).
  That is the neighbourhood of PR #25's new `pulse:Funding` class, and of our
  SNSF index. If we emit `pulse:Funding`, aligning it to `scholar:Grant` costs
  one `rdfs:subClassOf`.
- **`open-pulse` (49 MB, 3.7M triples) is in the catalog** but built on an older
  ontology version, so it is not a reference for the current model. One detail
  is still informative: its vocabulary list includes
  `https://openpulse.science/git-metadata-extractor#` — i.e. `gme-internal:`
  terms travel into published graphs, which is the concrete argument for
  promoting them (that is what all of [`../v4.0.0/ontology.ttl`](../v4.0.0/ontology.ttl) is about).

## Appendix — the complete catalog, 65 datasets

All published (non-`local/`) datasets, every one accounted for. Relevance is
judged from key, label and tags — **not** from querying each graph, so treat the
"why" column as a lead to verify, not a finding.

### Directly relevant — scholarly outputs (9)

| Dataset | Triples | Why it matters to GME |
|---|---|---|
| `zenodo-records` | 215 M | every Zenodo record as DataCite; keeps concept DOI separate from version DOI |
| `biosyslit` | 106 M | Zenodo biodiversity literature; carries ORCID |
| `gotriple` | 57.7 M | 2.7 M SSH publications + a 27-discipline SKOS scheme |
| `orkg` | 37 K | research contributions |
| `openalex-astrocytes` | 24 K | OpenAlex as RDF — the shape our OpenAlex index would take |
| `opencitations` | 8.1 K | citation graph; the DOI+PMID+OpenAlex+OMID crosswalk hub |
| `scholar` | 7.0 K | synthetic clean scholarly world — ground truth |
| `scholar-noisy` | 6.7 K | same world, 25% injected noise — our failure surface |
| `open-pulse` | 3.7 M | our own output, older ontology version |

### Provenance and data-quality patterns (5) — *missed in my first pass*

Relevant because the provenance profile is the part of PR #25 we understand
least well, and these are worked examples of PROV-O in practice.

| Dataset | Triples | Why |
|---|---|---|
| `causenet` | 256 M | causality claims **with provenance** from web extraction, at scale — the closest published analogue to what `pulse:ExtractionOutput` + confidence annotations are for |
| `nidm` | 3.2 K | Neuroimaging Data Model — a real cohort modelled with **PROV**; small enough to read end to end as a reference for provenance modelling |
| `causalgraph` | 315 | causal graphs in KGs (Fraunhofer), OWL |
| `causal` | 170 | cardiometabolic causal model with confounders |
| `ontoneurolog` | 17.7 K | a data-*sharing* ontology — the metadata-about-metadata problem we have |

### Identity, mappings and authority files (7) — *mostly missed in my first pass*

Directly relevant to reconciliation and to ask #12: these are graphs whose whole
job is stating that two records are the same thing.

| Dataset | Triples | Why |
|---|---|---|
| `mira-wikidata` | 125 | an **SSSOM linkset** — the community standard for mapping sets, i.e. the disciplined version of what `pulse:samePersonAs` is trying to say |
| `databnf` | 673 M | the whole BnF linked data, **VIAF-aligned authorities** — person/org authority control at scale |
| `bne` | 267 M | Spanish National Library authorities, same pattern |
| `getty-ulan` | 205 K | ULAN artist authority + mentorship lineage — an authority file modelling person-to-person relations |
| `factgrid-illuminati` | 35 K | prosopography — person-centric historical graph |
| `wikidata-ontology` | 185.5 M | every Wikidata class; we emit `wd:` discipline IRIs (`wd:Q428691` fallback) and could validate they resolve |
| `wikidata` / `wikidata-100mb` / `wikidata-xxl` / `wikidata-zenodo` | 100 MB → 600 M | the QID substrate our disciplines point into; `wikidata-zenodo` adds DOIs |

### Swiss / EPFL-adjacent institutions (3)

| Dataset | Triples | Why |
|---|---|---|
| `bcul` | 117 M | BCU Lausanne as a graph (MARC catalogue); mentions ROR |
| `ecal` | 1.0 M | ECAL art & design school library |
| `vidy` | 457 K | Lausanne archives |

### Domain science / research data (5)

`gbif-birds` (334 M, Darwin Core), `chebi-full` (8.8 M, chemistry ontology),
`bioexplora` (7.9 M, natural history), `chemotion` (1.5 M — a chemistry
**ELN** with its own ontology, i.e. research-software-adjacent RDM),
`monarch` (7.8 K, biomedical Biolink).

### Not relevant to GME (36)

Cultural heritage, archives and rare books: `biblissima`, `mmm`, `bcn`,
`arxiu`, `albala`, `bph`, `ustc`, `jonas`, `mira`, `postscriptum`,
`fuero_juzgo`, `ramon_llull`, `peirce`, `mimotext`, `memoria`, `boe`,
`smithsonian3d`, `scrolls`, `lineara`, `nomisma`, `theographic-graph`,
`antarctic-expeditions`, `linked-jazz`. Geo: `geoadmin`, `geoadmin-tiles`,
`ohm-full`, `history`. Sport/games/media: `worldcup`, `worldcup2026`,
`tracking`, `mtg`, `mtg-game`, `subtitles`.

They are still useful as *modelling* references — `mmm` for CIDOC-CRM
provenance chains, `biblissima` for a full Wikibase in one file — but they carry
no data we would ingest.

### What I got wrong on the first pass

My initial sweep was a keyword filter over key/label/description for scholarly
terms, which surfaced 69 of 199 entries (including `local/` duplicates). It
missed the four categories above that matter on *pattern* rather than on
subject matter: `causenet` and `nidm` (provenance in practice),
`mira-wikidata` (SSSOM mappings), and `databnf` / `bne` / `getty-ulan`
(authority control). Those are arguably more useful to us than another
publication corpus, because provenance modelling and identity assertion are the
two hard parts of the v4 adaptation.

## Suggested next steps

1. Add ask #12 (`samePersonAs` → `skos:exactMatch`) to the proposal — done.
2. Wire `scholar` + `scholar-noisy` in as a reconciliation-accuracy fixture.
   Independent of the ontology decisions, and it measures something we
   currently only assume.
3. When exporting GME output as `.rete`, no IRI changes are needed — but a
   `rdfs:subClassOf` line per entity type (`schema:SoftwareSourceCode` and
   `pulse:Contribution` → `scholar:Work`, etc.) makes our graph queryable
   through the same hub as the other ten.
4. **Read `nidm` and `causenet` before implementing the provenance profile.**
   Both model provenance with PROV in anger — `nidm` at 3.2 K triples is small
   enough to read whole, `causenet` shows it at 256 M. Provenance is the part
   of PR #25 we have the least experience with, and these are the only worked
   examples in the catalog.
5. **Look at `mira-wikidata` (SSSOM) before settling ask #12.** If the
   ecosystem's answer to "these two records are the same" is an SSSOM linkset,
   that is a stronger precedent than either `owl:sameAs` or `skos:exactMatch`
   chosen on our own reasoning.
