# Open Pulse Ontology — v3.0.0 (proposed)

!!! warning "Draft / proposed"
    This is a **proposed v3.0.0 draft**, not a released version. The current
    production ontology is
    [v2.1.2](https://sdsc-ordes.github.io/open-pulse-ontology/versions/v2.1.2/).

<div class="md-button-group" markdown>
[:material-file-document-outline: Open the full reference](v3.0.0/index.html){ .md-button .md-button--primary }
[:material-graph-outline: JSON schema graphs](v3.0.0/json-schemas.html){ .md-button }
[:material-download: Download the TTL](v3.0.0/open-pulse-ontology-v3.0.0.ttl){ .md-button }
</div>

The **full ontology reference** is a self-contained, pyLODE-style page that
documents every class, property and named individual — including the internal
provider fields. It merges three sources:

| Source | Contents |
|---|---|
| `src/v2/validation/open-pulse-ontology-v2.1.2.ttl` | All current (v2.1.2) terms, carried forward |
| `.internal/ontology-v3/07-ttl-draft.md` | The proposed v3 additions |
| `docs/gme-internal.ttl` | The internal (`gme-internal:`) provider vocabulary |

## What's in the reference

- **Classes** — every v2.1.2 class plus the new v3 ones: `pulse:GitIdentity`,
  `pulse:ExtractionRun`, `pulse:Observation`, `pulse:Venue`,
  `pulse:OrgRelationship`, and the new publication-type / open-access /
  organization-relationship enumerations.
- **Object & datatype properties** — with IRI, description, domain, range,
  sub-property / inverse / `sameAs`, and a namespace badge.
- **Named individuals** — all enumeration instances (disciplines, repository &
  organization types, publication types, OA statuses, org-relationship types).
- **SHACL validation constraints** — rendered per class (property path,
  cardinality, datatype/class, pattern) from the carried-forward v2.1.2 shapes.

## Internal fields (`gme-internal:`)

Terms badged **GME internal** are an auxiliary, **non-normative** vocabulary
emitted only when an extract is requested with
`?include_internal_fields=true`. They are valid RDF but are intentionally **not**
conformant to the closed Open Pulse SHACL shapes. In the reference they are
listed inline alongside the Open Pulse properties, distinguished by their badge.

## What changes from v2.1.2

### Breaking

- **`org:unitOf` becomes an array** — organizations can have multiple parent
  organizations. Consumers reading it as a single string must wrap on read.
- **`pulse:githubUsername` is deprecated** in favour of `pulse:githubLogin`
  (current handle) + `pulse:githubAccounts` (all known handles). It remains as
  `owl:sameAs pulse:githubLogin` through the transition; removed in v3.1.0.
- **`schema:citation` on a repository is clarified as a URI**; the DOI string
  belongs in `schema:identifier`.

### Non-breaking additions

- **Stable identity** — `pulse:githubUserId` (immutable numeric ID) as the
  anchor, and `pulse:GitIdentity` separating commit authorship from the
  platform account.
- **Provenance & history** — `pulse:ExtractionRun` and `pulse:Observation`
  support an append-only observation log; entities carry `pulse:firstObservedOn`,
  `pulse:lastConfirmedOn`, `pulse:observationCount`.
- **Cross-platform identifiers** for people, organizations, articles and
  repositories (OpenAlex, HuggingFace, Zenodo, RenkuLab, SWISSUbase, arXiv,
  Wikidata, …).
- **Publications** — `pulse:publicationType`, `pulse:openAccessStatus`,
  `pulse:citationCount`, preprint links, `pulse:Venue`, grant IDs, and
  paper → code → data links.
- **Organizations** — typed relationships (`pulse:administrativeParent`,
  `pulse:fundingBody`, `pulse:spinoffOf`, `pulse:OrgRelationship`).

See `.internal/ontology-v3/08-migration-notes.md` for the full migration path
and deprecation timeline.

## Regenerating

Both the reference HTML and the merged TTL are generated — edit the sources
above, not the generated files, then run:

```bash
python3 scripts/build_ontology_v3_docs.py
```
