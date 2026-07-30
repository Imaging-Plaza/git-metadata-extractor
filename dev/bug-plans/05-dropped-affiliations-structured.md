# Bug 05 — dropped_affiliations parked as opaque hashes (preserve unresolved signal)
**Severity:** low-medium (data quality) · **Status:** Investigated — plan ready (no code changed) · **Area:** reconciliation / affiliations

## Symptom
Affiliations that were parsed from a profile (company / bio / ORCID biography / blog /
email, plus agent-emitted membership links) but could **not** be anchored to a confirmable
ROR / Infoscience / GitHub identifier are parked as opaque hash-style references rather than
as structured, human-readable text with an explicit "unresolved" marker. Two related effects:

1. An unanchored affiliation Organization is minted with `idSource = "uuid"` and at
   output time the JSON-LD layer prefixes that UUID with `urn:pulse:`, so the operator
   sees a `urn:pulse:<uuid>` node in the SPARQL store — an opaque hash bucket. The original
   affiliation string survives only as that node's `schema:name`
   (`src/v2/pipeline/stages/resolve_placeholder_orgs_to_ror.py:1-41`,
   `src/v2/pipeline/stages/jsonld_build.py:54-59`).
2. When the membership evidence floor later drops the link entirely (no role, no dates, no
   ORCID+ROR anchor), the breadcrumb stamped on the Person under `_dropped_affiliations`
   records the opaque `org_id` (the same `urn:pulse:<uuid>`/uuid token) plus a *best-effort*
   `org_name` that is frequently `None` — so the only durable copy of the parsed affiliation
   text can be lost, and there is **no explicit `unresolved` flag** and **no source/provenance**
   on the entry (`src/v2/pipeline/stages/reconciliation.py:1739-1759`).

The `gme-internal:dropped_affiliations` term already exists for exactly this evidence
(`docs/gme-internal.ttl:278-280`: "Affiliations collected but dropped during reconciliation
for lack of a confirmable identifier anchor"), but the payload it carries is identifier-shaped,
not text-shaped.

## Current behaviour (verified, file:line)
Trace of an unresolved affiliation:

- **Raw text origin.** Affiliation strings come from Person-side fields (`_company`, `_bio`,
  `_orcid_biography`, `_blog`, `_email`) resolved by the company/bio/placeholder ROR stages,
  and from the rule-based / LLM organization + membership agents and the rescue refiner
  (`src/v2/pipeline/stages/resolve_placeholder_orgs_to_ror.py:3-14`).
- **No-match → opaque org id.** When an agent sees an affiliation string but cannot anchor it
  to a ROR/Infoscience/GitHub id, it emits an Organization with `idSource = "uuid"`; the
  original string is kept as `schema:name`
  (`resolve_placeholder_orgs_to_ror.py:9-20`, `_placeholder_orgs` at lines 75-88). At JSON-LD
  output, `_normalize_node_id` turns a bare uuid id into `urn:pulse:<uuid>`
  (`jsonld_build.py:54-59`, prefix `ENTITY_URI_PREFIX = "urn:pulse:"` at line 9). The late
  re-resolution stage re-queries ROR against `schema:name`; on success it rewrites the node to
  a `pulse:ror` id and stashes the old display string under `_original_name`
  (`resolve_placeholder_orgs_to_ror.py:91-120`). **On failure the node intentionally stays as
  `urn:pulse:<uuid>`** (documented "What we DON'T do here", lines 22-31) — this is the opaque
  hash bucket.
- **Membership evidence floor → `_dropped_affiliations` stamp.** `_normalize_membership_entities`
  applies the softened evidence floor: keep iff role OR dates OR (Person has ORCID AND Org has a
  registered id) (`reconciliation.py:1679-1725`). Memberships that fail are dropped, but the
  evidence is stamped onto the Person:
  ```python
  # reconciliation.py:1739-1747
  dropped_affiliations_by_person.setdefault(person_id, []).append({
      "org_id": org_id,                                    # urn:pulse:<uuid> / uuid token — opaque
      "org_name": org_name if org_name != "<unknown org>" else None,  # often None
      "membership_id": membership.get("id"),
      "reason": "no role / no dates / no ORCID+ROR anchor",
  })
  ```
  Stamped onto `person["_dropped_affiliations"]` at `reconciliation.py:1754-1759`.
  - **Key gap:** the entry has **no `text` field** (it relies on `org_name`, which is `None`
    whenever the org entity had no `schema:name`), **no explicit `unresolved: true` flag**, and
    **no `source`** (which profile field / which agent produced it). When `org_name` is `None`,
    the affiliation is effectively reduced to an opaque `org_id` plus a generic reason string —
    lost signal.
- **Internal field, stripped from the public surface (confirmed).** `_dropped_affiliations` is
  `_`-prefixed: strict SHACL/JSON-schema validation drops all `_`/`None` keys before validating
  (`src/v2/validation/schema_validation.py:96-100`), and JSON-LD output drops `_`-prefixed keys
  unless `include_internal_fields=True`, in which case `_dropped_affiliations` is renamed to the
  `gme-internal:dropped_affiliations` term and expands to a real IRI predicate
  (`jsonld_build.py:139-141`, `184-188`, `241-256`; documented at `191-218`). So changes to the
  entry shape stay entirely on the internal/`gme-internal` surface and never touch the closed
  Open Pulse SHACL shapes or `additionalProperties:false` strict schema.

## Proposed representation & fix
Keep the existing `gme-internal:dropped_affiliations` term (no ontology change required) and
enrich the **entry shape** so each dropped affiliation is a self-describing structured literal
that preserves the original text with an explicit unresolved flag.

Target entry shape (per Person, list of dicts under `_dropped_affiliations`):
```jsonc
{
  "text": "AdaptiveMotorControlLab",   // original affiliation string (NEVER null when we have it)
  "org_id": "urn:pulse:<uuid>",        // keep for round-tripping / rescue re-instantiation
  "org_name": "AdaptiveMotorControlLab", // canonical/display name if distinct from text
  "membership_id": "<person>__<org>",
  "source": "membership_evidence_floor", // which path dropped it (extensible)
  "reason": "no role / no dates / no ORCID+ROR anchor",
  "unresolved": true                    // explicit flag — this is unanchored-but-real
}
```

Concrete targets:
1. **`reconciliation.py:1739-1747`** — when building the dropped entry, populate `text` from the
   best available original string. Resolve it in priority order:
   `org_entity.get("_original_name")` → `org_entity.get("schema:name")` → existing `org_name`.
   This makes `text` survive even when the org's display name was overwritten or absent. Add
   `"unresolved": True` and `"source": "membership_evidence_floor"`. Keep `org_id`,
   `membership_id`, `reason` for backward compatibility and rescue.
   - Note: `org_name` is read from `org_entity.get("schema:name")` at
     `reconciliation.py:1731-1733`; the placeholder-org `schema:name` *is* the original
     affiliation text (`resolve_placeholder_orgs_to_ror.py:16-18`), so `text` is recoverable
     here without new plumbing.
2. **Optional second emitter — placeholder orgs that never enter a Membership.** Consider a small
   helper invoked from the placeholder resolver's failure path
   (`resolve_placeholder_orgs_to_ror.py:22-27`, the "Drop the placeholder — we DON'T" branch) to
   ALSO stamp `_dropped_affiliations` (`text` = `schema:name`, `unresolved: true`,
   `source: "ror_unresolved_placeholder"`) on the linked Person(s), so an unanchored affiliation
   that has no surviving Membership is still represented as text rather than only as a bare
   `urn:pulse:<uuid>` node. Lower priority than (1); confirm whether such orgs are otherwise
   pruned by `prune_dangling_refs`.

Representation decision: a **structured `gme-internal:dropped_affiliations` entry** (option A),
NOT a new public ontology term. Rationale: the term already exists and is documented for this
exact purpose; the data is provenance/evidence, not canonical graph facts; and keeping it under
`gme-internal` avoids any SHACL/strict-schema churn (the entries serialize as JSON-literal values
on the `gme-internal:dropped_affiliations` predicate, only when `include_internal_fields=True`).

## Schema / ontology surface impact
- **No change to public SHACL shapes or strict JSON schema.** The field is `_`-prefixed and is
  stripped before strict validation (`schema_validation.py:96-100`); strict/closed shapes
  (`pulse:MembershipShape sh:closed`, `additionalProperties:false`) never see it.
- **`docs/gme-internal.ttl:278-280`** already declares `gme-internal:dropped_affiliations`; the
  comment stays accurate. Optionally tighten the `rdfs:comment` to note entries now carry
  `{text, unresolved}` structure. The v3.0.0 release mirror
  (`docs/releases/v3.0.0/open-pulse-ontology-v3.0.0.ttl:1013` and the generated `index.html`)
  would only need regeneration if the comment text changes — not required for the fix.
- **JSON-LD surface:** entries appear under `gme-internal:dropped_affiliations` only when
  `include_internal_fields=True` (`jsonld_build.py:191-256`). Because the value is a list of
  JSON objects, confirm `_normalize_jsonld_value` serializes nested dicts acceptably for the
  consumer (it currently does for other `_`-list fields); if RDF-literal flattening is desired,
  the entries can be emitted as a JSON string blob, matching the `gme-internal:publiccode`
  pattern (`jsonld_build.py:258-289`).

## Risks & considerations
- **Rescue refiner round-trip (downstream consumer).** `_run_rescue_pass` reads
  `_dropped_affiliations` and builds `RescueCandidate(membership_id, person_id, person_name,
  org_id, org_name, reason)` (`refine_with_llm.py:1724-1761`; model at
  `src/v2/agents/llm/refiners/rescue/agent.py:38-47`). Adding `text`/`source`/`unresolved` keys
  is **additive and safe** — the consumer reads keys explicitly and ignores extras. Improvement:
  feed `text` (now reliably non-null) to the rescue LLM so it has the verbatim affiliation string
  even when `org_name` was `None`, improving rescue precision.
- **`existing + dropped` accumulation** at `reconciliation.py:1757-1759` appends across passes —
  watch for duplicate entries on re-runs; consider dedup by `membership_id` if the stage can run
  twice. (Pre-existing behaviour; flag only.)
- **Backward compatibility:** keep `org_id`, `org_name`, `membership_id`, `reason` so any existing
  consumer / snapshot test that reads them keeps working; new keys are purely additive.
- **No PII change:** `text` is the same affiliation string already stored as `schema:name` on a
  graph node, so this surfaces no data not already collected.

## Test / verification plan
- **Unit (reconciliation):** craft a Membership that fails the evidence floor with an Org whose
  `schema:name = "AdaptiveMotorControlLab"`; assert the stamped `_dropped_affiliations` entry has
  `text == "AdaptiveMotorControlLab"`, `unresolved is True`, `source == "membership_evidence_floor"`,
  and that `text` is populated even when `org_name` would have been filtered to `None`.
  (Mirrors the deeplabcut audit example cited at `reconciliation.py:1681-1702`.)
- **Unit (no display name):** Org with `idSource="uuid"` and only `_original_name` set, no
  `schema:name` — assert `text` still resolves from `_original_name`.
- **JSON-LD output:** build with `include_internal_fields=True`; assert the Person node carries
  `gme-internal:dropped_affiliations` with the structured entries; build with the flag off and
  assert the key is absent (`jsonld_build.py:241-256`).
- **Strict validation:** assert a Person carrying `_dropped_affiliations` still passes strict
  SHACL/JSON-schema (field stripped at `schema_validation.py:96-100`).
- **Rescue round-trip:** assert `_run_rescue_pass` still produces `RescueCandidate`s and now also
  threads `text` through (no regression; additive).
- **Regression:** update any snapshot test that asserts on the old 4-key entry shape.

## Effort estimate
**Small — ~0.5 day.** Core change is localized to the entry-builder at
`reconciliation.py:1739-1747` (add `text`/`source`/`unresolved`, resolve text from
`_original_name`/`schema:name`) plus 3-4 unit tests. The optional placeholder-failure emitter
(fix item 2) and the `gme-internal.ttl` comment tweak add ~0.5 day if included.

## Open questions
- Should an unanchored affiliation that never produces a surviving Membership (placeholder ROR
  miss) also be stamped on the Person (fix item 2), or is the `urn:pulse:<uuid>` graph node with
  `schema:name` considered sufficient signal on its own?
- Do we want `unresolved` to also distinguish *reasons* (evidence-floor drop vs ROR-miss) via the
  `source` field only, or add a small enum?
- Should the placeholder Org node itself carry an explicit `gme-internal:unresolved true` marker
  so SPARQL can filter `urn:pulse:` affiliation buckets directly, in addition to the Person-side
  breadcrumb?
- Serialize entries as nested JSON objects vs a JSON string blob under
  `gme-internal:dropped_affiliations` (consistency with the `gme-internal:publiccode` pattern,
  `jsonld_build.py:258-289`)?
