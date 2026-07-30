# Bug 08 — badges stored as opaque hashes (emit type/label)

**Severity:** low (data quality) · **Status:** Investigated — plan ready (no code changed) · **Area:** GitHub enrichment / badges

## Symptom

README/repo badges surface in the RDF / SHACL graph as opaque blank-node
identifiers (e.g. `_:n7aa4dde…` / `a7aa4dde…`) rather than as human-readable
records. A consumer querying the graph sees a bare node under
`gme-internal:badges` with **no** `label`, `image_url`, or `link_url`
attached — so they cannot tell what badge it is (build status? coverage?
a PyPI version badge?) without a side lookup that does not exist, because the
descriptive fields have been dropped entirely on JSON-LD → RDF conversion.

Crucially, the loss is **not** in the badge parser. The parser produces good,
human-readable records; they are destroyed downstream at graph-emit time.

## Current badge model (verified, file:line)

**1. Parsing — already structured and human-readable.**
`parse_badges(readme)` in `src/v2/agents/rule_based/_repo_signals.py:357-399`
extracts Markdown badges and returns a de-duped, order-preserving list of
records:

```python
result.append({"label": alt, "image_url": img, "link_url": link})
```
(`_repo_signals.py:380`, inside `_add`). It recognises both linked badges
`[![alt](img)](link)` (`_BADGE_LINKED_RE`, `_repo_signals.py:347-350`) and
plain image badges `![alt](img)` (`_BADGE_IMAGE_RE`, `_repo_signals.py:352-354`,
`link_url=None`). The list is capped at `_MAX_BADGES = 100`
(`_repo_signals.py:344`, `:394-398`).

So at parse time every badge carries: **label** (the Markdown alt text),
**image_url** (the badge image / shields.io URL), and **link_url** (the
click-through target, or `None`). No hash is involved here.

**2. Storage on the repository entity.**
`context_gather` calls `parse_badges` on the **raw** README and stores the list
under `repository_metadata["badges"]`:
`src/v2/pipeline/stages/context_gather.py:295-297`
```python
badges = parse_badges(readme)
if badges:
    repository_metadata["badges"] = badges
```

**3. Emission onto the internal field.**
`repository_agent.py:586-595` copies the list verbatim onto the `_badges`
internal field plus a `_badge_count` scalar:
```python
"_badges": (repository.get("badges") or None),
"_badge_count": (len(repository["badges"]) if isinstance(...) else None),
```
At this point the data is **still** the full `{label, image_url, link_url}`
list. It is human-readable here.

**4. Where the hash appears — JSON-LD build + rdflib parse (root cause).**
`build_jsonld_output` (`src/v2/pipeline/stages/jsonld_build.py:191-256`) renames
each `_`-prefixed field to a `gme-internal:` term via `_internal_term`
(`jsonld_build.py:139-142` → `_badges` → `gme-internal:badges`) and registers
the prefix in `@context` (`GME_INTERNAL_NAMESPACE =
"https://openpulse.science/git-metadata-extractor#"`, `jsonld_build.py:20-21`,
`:292`). The list value is passed through `_normalize_jsonld_value`
(`jsonld_build.py:89-127`), which recurses into the list and into each dict but
**leaves the inner keys `label` / `image_url` / `link_url` untouched** — it does
not map them to any term (`jsonld_build.py:117-126`).

The resulting JSON-LD is then parsed into an RDF graph with rdflib:
`_jsonld_to_graph` → `graph.parse(data=..., format="json-ld")`
(`src/v2/api.py:391-395`, called at `api.py:1639` and `:1703`).

This is where the human-readable content is lost. `gme-internal:badges` has no
`"@type": "@id"` in the context, so each badge dict expands to a **fresh blank
node**, and rdflib assigns it an opaque auto-generated bnode identifier (the
`a7aa4dde…`-style hash in the symptom). The inner keys `label` / `image_url` /
`link_url` are **undefined terms** in the `@context` — JSON-LD expansion drops
undefined properties — so the blank node ends up with effectively no usable
predicates. The badge becomes a content-free opaque node.

This is the **identical, already-documented failure mode** described for
`_releases` and `_container_images` at `repository_agent.py:543-547` and
`:554-559`:
> "The raw `_releases` list-of-objects collapses to empty blank nodes on
> JSON-LD expansion (inner keys unmapped in @context), so these single-value
> `gme-internal:…` triples are what downstream consumers actually query."

Those two list-of-object fields were rescued by **also** emitting flat
`gme-internal:*` scalars (`summarize_releases`, `summarize_packages`). Badges
were never given that treatment — `_badges` is emitted only as the raw nested
list, so it hits exactly the collapse the comment warns about.

## Proposed representation & fix

The hash is **not** a deliberate content/dedup ID we chose — it is an
incidental rdflib blank-node label produced because the badge object's inner
keys are unmapped. So the fix is not "expose the hash + a label"; it is
"stop collapsing the badge into a content-free bnode and surface the
human-readable fields as real triples." Dedup is already handled upstream by
the `seen` set in `parse_badges` (`_repo_signals.py:376-380`) — we do not need a
hash key for stable identity.

Recommended approach — **flat parallel scalar lists** (mirrors the
`_releases` / `_container_images` rescue, lowest risk, no `@context` surgery):

In `repository_agent.py` (right after the existing `_badges` / `_badge_count`
block at `:586-595`), add a small summariser (new helper, e.g.
`summarize_badges(badges)` in `_repo_signals.py` next to `summarize_releases` /
`summarize_packages`) that derives flat, RDF-friendly scalar lists from the
badge records and emit them as `gme-internal:*` terms:

- `gme-internal:badge_labels` — list of `label` strings (the human-readable
  type/label, e.g. `"build: passing"`, `"PyPI version"`, `"coverage"`).
- `gme-internal:badge_image_urls` — list of `image_url` strings.
- `gme-internal:badge_links` — list of `link_url` strings (drop / keep `None`s
  consistently).
- Keep `gme-internal:badge_count` (already exists via `_badge_count`).

These are plain string lists, so rdflib emits them as literal-valued triples on
the repository node directly — no blank nodes, no dropped predicates, fully
queryable. A consumer can read `gme-internal:badge_labels` and immediately know
what each badge is.

Optionally also add a single derived convenience field for the most common
intent (telling badges apart at a glance): a `gme-internal:badges_summary`
string joining `label` values, but the parallel-list form is the primary fix.

**Keep `_badges` (the nested raw list) as-is** for consumers that read the
JSON-LD document before rdflib parsing (the nested form is intact in the JSON,
it is only lost on RDF conversion) — i.e. emit the flat scalars **in addition
to** the raw list, not instead of it. This matches the established pattern for
`_releases` (raw list + flat scalars coexist).

Alternative (heavier, not recommended for a low-severity fix): register
`label` / `image_url` / `link_url` as proper `gme-internal:` predicates in the
`@context` (`src/v2/schema/json/context/v2.0.jsonld`) and give each badge a
deterministic `@id` so it becomes a typed, addressable sub-node instead of a
bnode. This preserves per-badge structure in RDF but requires `@context`
changes, an ontology term decision, and possibly SHACL-shape consideration —
more surface area than the data-quality bug warrants.

### Where badge labels come from

The `label` is the Markdown **alt text** (`m.group("alt")`,
`_repo_signals.py:387`, `:392`) — i.e. the text between `![` and `]`. This is
the most reliable human-readable source for well-authored READMEs (shields.io
badges typically carry alt text like `Build Status`, `coverage`, `PyPI`).

When alt text is empty (`![](…)`), a useful fallback is to **parse the
shields.io image URL**: `https://img.shields.io/badge/<label>-<message>-<color>`
encodes the label as the first path segment (see the existing coverage-badge
parser `_SHIELDS_COVERAGE_RE`, `_repo_signals.py:70-97`, which already mines
`coverage-87%` out of shields URLs). The summariser can fall back to this label
segment, and finally to the `link_url` host/path as a last resort. This
fallback is optional polish; the primary fix is surfacing the existing `label`.

## Schema / ontology surface impact

- These are **internal** fields on the `gme-internal:` vocabulary
  (`GME_INTERNAL_NAMESPACE`, `jsonld_build.py:20-21`), surfaced **only** when
  `include_internal_fields=True` (`jsonld_build.py:195`, `:245-250`). They are
  **not** on the closed/strict Open-Pulse SHACL surface — strict validation
  always strips `_`-prefixed fields (`build_jsonld_output` docstring,
  `jsonld_build.py:213-217`; `_drop_internal_keys`, `:130-136`). So adding
  more `gme-internal:badge_*` scalars cannot break SHACL conformance.
- The recommended flat-scalar approach needs **no** `@context` change: the
  `gme-internal` prefix is already registered, and new terms under it expand to
  IRIs automatically. Only the heavier alternative touches
  `src/v2/schema/json/context/v2.0.jsonld`.
- Public (non-internal) responses are unaffected — badges only appear when the
  internal profile is requested.

## Risks & considerations

- **Low blast radius.** Additive scalar fields on an opt-in internal surface;
  no public-API or SHACL-shape change in the recommended approach.
- **Backward compatibility.** Keep `gme-internal:badges` (raw list) and
  `gme-internal:badge_count` so existing consumers don't break; the new
  `gme-internal:badge_labels` / `_image_urls` / `_links` are purely additive.
- **List alignment.** If parallel lists are used, they must stay index-aligned
  (same order, same length, `None`/empty placeholder for missing `link_url`).
  Building all three from a single pass over `badges` (as `summarize_releases`
  does) guarantees this.
- **Truncation already handled** at `_MAX_BADGES = 100` (`_repo_signals.py:344`)
  — the summariser inherits the already-capped list.
- **Empty / noisy alt text.** Some badges have empty alt; the URL-parsing
  fallback mitigates but is optional. Worst case the label is an empty string,
  which is still strictly better than an opaque bnode hash.

## Test / verification plan

- **Unit (parser → summariser):** extend
  `tests/v2/test_badges_multi_registry.py` with a `summarize_badges` case:
  given a known badge list, assert `badge_labels` / `badge_image_urls` /
  `badge_links` are correct, index-aligned, and that `badge_count` matches.
- **Agent scalars:** assert `repository_agent` emits the new
  `_badge_labels` / `_badge_image_urls` / `_badge_links` (the existing test
  file already checks "agent scalars" per the badges commit message — add the
  new keys there).
- **End-to-end RDF assertion (the real regression):** build the JSON-LD with
  `include_internal_fields=True`, run it through `_jsonld_to_graph`
  (`api.py:391`), and assert the repository node has literal
  `gme-internal:badge_labels` triples — and that **no** content-free blank node
  remains as the only badge representation. A README fixture with a shields.io
  build-status badge should yield a queryable `"build: passing"`-style label in
  the graph.
- **Fallback:** a fixture badge with empty alt text but a
  `img.shields.io/badge/<label>-…` URL should still produce a non-empty label
  (if the URL fallback is implemented).
- Confirm `tests/v2` stays green (the badges commit baseline was 1566 passed).

## Effort estimate

**Small — ~0.5 day.** One new pure helper (`summarize_badges`, mirroring
`summarize_releases`), ~4 lines wired into `repository_agent.py:586-595`, and
test additions. No `@context` / ontology / SHACL changes in the recommended
path. The optional shields-URL label fallback adds maybe an hour. The heavier
typed-sub-node alternative would be ~1.5–2 days (context + ontology + SHACL).

## Open questions

- Do any downstream consumers already query the raw nested
  `gme-internal:badges` list from the **JSON** document (pre-RDF), where the
  fields are intact? If so, the bug is purely an RDF-graph concern and the
  flat-scalar fix fully resolves it; if a consumer needs per-badge addressable
  RDF nodes, the heavier typed-sub-node alternative is warranted.
- Should `link_url=None` badges be included in `gme-internal:badge_links` (as
  empty/placeholder to preserve alignment) or filtered (breaking alignment)?
  Recommendation: preserve alignment with an explicit empty value.
- Is a single joined `gme-internal:badges_summary` string desired in addition
  to the parallel lists, for cheap human display?
- Is the shields.io-URL label fallback worth shipping now, or defer until we
  see how many real badges have empty alt text?
