# JSON Schemas — interactive graph

The project's JSON-Schema entity contracts (`src/v2/schema/json`) rendered with
the same interactive graph viewer as the ontology reference: pan, zoom, select a
node for details, switch layouts, and open full screen.

<div class="md-button-group" markdown>
[:material-graph-outline: Open the schema graph viewer](v3.0.0/json-schemas.html){ .md-button .md-button--primary }
[:material-file-document-outline: Ontology v3.0.0 reference](v3.0.0/index.html){ .md-button }
</div>

## What you can explore

- **Entity overview (references)** — the six entity schemas (Person, Organization,
  Repository, Article, Contribution, Membership) and the reference properties that
  link them (`pulse:hasContribution`, `pulse:owns`, `schema:author`, …).
- **Per-schema structure** — for every schema (each entity in both the **strict**
  and **agent** variants), the structure as a graph: root → properties → nested
  objects, annotated with type, `enum`/`const`, `pattern`, `format`, required-ness,
  and cross-schema references.

The viewer is generated from the schema files by
`scripts/build_ontology_v3_docs.py`; edit the schemas under
`src/v2/schema/json/` and re-run it to refresh.
