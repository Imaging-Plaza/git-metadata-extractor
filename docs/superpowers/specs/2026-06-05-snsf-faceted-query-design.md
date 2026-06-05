# SNSF faceted query — design spec

**Status:** approved decisions, pending implementation plan.
**Date:** 2026-06-05.

## Purpose

Surface the SNSF P3 data we already hold (90k+ grants with abstracts, lay
summaries, disciplines, fields of research, funding schemes, institutions,
persons, and 7 output types) as a **faceted query** that mirrors the
`https://data.snf.ch/grants` search — the same filters, facet counts, and
result rows — over our local DuckDB store, with no calls to data.snf.ch.

This is **not** a re-ingest. The audit (below) shows every facet on the SNF
site already maps to a column we ingest from the bulk CSV. The gap is a query
surface: today `snsf/query.py` is only semantic RAG (Qdrant); there is no
faceted SQL query.

## Facet → existing column map (audit)

| data.snf.ch facet | source in our store |
|---|---|
| Funding scheme | `grants.funding_instrument` |
| Research institution | `grants.research_institution` (+ `_type`) |
| Status | `grants.state` |
| Start / End date | `grants.start_date` / `grants.end_date` |
| Call | `grants.call_full_title`, `grants.call_decision_year` |
| Discipline | `grants.main_discipline` (+ `discipline_taxonomy`) |
| Fields of Research | `grants.main_field_of_research` (+ `_la` / `_lb`) |
| Countries of collaboration | `output_collaborations.country` |
| Applicants / Project partners / Employees | `persons.{responsible_applicant,co_applicant,project_partner,practice_partner,employee,contact_person,applicant_abroad}_grants` (JSON grant-number arrays) |
| Output data | the 7 `output_*` tables (presence/counts) |
| free-text | `grants.title` / `grants.abstract` / `grants.keywords` |

All `grant_number` values are the canonical SNSF grant URL
(`https://data.snf.ch/grants/grant/<n>`) after the v3.0.0 re-PK.

## Decisions (locked)

1. **Materialized facet tables** (rebuilt by a `build_facets()` step after
   ingest), not SQL views — fast faceting/counts over 90k rows and they ride
   into the `.ro` snapshot the Hub reads.
2. **DuckDB FTS / ILIKE** for free-text on title+abstract+keywords, combined
   with facet filters in one SQL query (deterministic, no embeddings). The
   existing Qdrant semantic search stays a separate mode; an optional hybrid
   phase comes last.
3. **CLI + HTTP endpoints** both.

## New derived tables (the "more tables")

Built in `src/index/snsf/storage/facets.sql` + a `build_facets(store)` step,
all keyed on `grant_number` (the URL). Idempotent (DELETE + INSERT from source
tables; safe to re-run after any reload).

1. **`grant_persons`** (`grant_number TEXT`, `person_number INTEGER`,
   `role TEXT`, PK `(grant_number, person_number, role)`) — flattens each
   `persons.*_grants` JSON array into rows via `json_each`, tagging the `role`
   (responsible_applicant / co_applicant / project_partner / practice_partner /
   employee / contact_person / applicant_abroad). Enables the Applicants /
   Project partners / Employees facets and "who is on this grant".
2. **`grant_output_counts`** (`grant_number TEXT PRIMARY KEY`,
   `n_publications`, `n_datasets`, `n_collaborations`, `n_academic_events`,
   `n_knowledge_transfers`, `n_public_communications`, `n_use_inspired`, all
   INTEGER) — per-grant rollups (`GROUP BY grant_number`) across the 7
   `output_*` tables. Enables the "Output data" facet + the "N scientific
   publications" result line.
3. **`grant_countries`** (`grant_number TEXT`, `country TEXT`, PK
   `(grant_number, country)`) — distinct `output_collaborations.country` per
   grant. Enables the Countries-of-collaboration facet.

Indexes on the join/filter columns (`grant_persons.person_number`,
`grant_countries.country`).

`build_facets()` hooks into the snsf ingest flow (run after `load_persons` +
the `load_output_*` loaders) and is exposed as a CLI subcommand
(`python -m src.index.snsf build-facets`).

## Query module — `src/index/snsf/facet_query.py`

```python
@dataclass
class GrantFilters:
    funding_instrument: list[str] | None = None
    research_institution: list[str] | None = None
    state: list[str] | None = None
    main_discipline: list[str] | None = None
    main_field_of_research: list[str] | None = None
    call_decision_year: list[int] | None = None
    country: list[str] | None = None          # via grant_countries
    person_number: int | None = None          # via grant_persons
    person_role: str | None = None            # narrows the person join
    has_output: list[str] | None = None        # e.g. ["publications","datasets"]
    start_from: date | None = None
    start_to: date | None = None
    end_from: date | None = None
    end_to: date | None = None

def query_grants(store, filters: GrantFilters, *, text: str | None = None,
                 sort: str = "start_date_desc", limit: int = 50,
                 offset: int = 0) -> GrantQueryResult: ...

def facet_counts(store, filters: GrantFilters, *, text: str | None = None
                 ) -> dict[str, list[FacetCount]]: ...
```

- `query_grants` builds one parameterized SQL `SELECT` over `grants` LEFT
  JOIN `grant_output_counts`, with `EXISTS` subqueries against `grant_persons`
  / `grant_countries` for those facets, an FTS/ILIKE predicate on
  title+abstract+keywords when `text` is given, `ORDER BY` the `sort`, and
  `LIMIT/OFFSET`. Returns rows + a `total` (windowed count).
- `facet_counts` returns, per facet, the value→count list **with the other
  filters applied** (standard faceted-search semantics — the "fold" numbers on
  the SNF site). Implemented as one `GROUP BY` per facet over the filtered set.
- All SQL is parameterized (no string interpolation of user input).

### Result row shape (mirrors the search page)

`grant_number` (URL), `title` (+ `title_english`), `responsible_applicant`,
`research_institution`, `main_discipline`, `funding_instrument`, `keywords`,
`state`, `start_date`, `end_date`, `amount_granted`, and the
`grant_output_counts` (e.g. `n_publications`).

## Surfaces

1. **CLI** — `python -m src.index.snsf facet-search [--scheme … --institution …
   --status … --discipline … --field … --call-year … --country … --has-output
   … --start-from … --q …] [--sort …] [--limit/--offset]` → JSON results;
   `--facets` flag adds the facet counts. Plus `build-facets`.
2. **HTTP** (mirrors the page's URL-driven filters):
   - `GET /v2/indices/snsf/grants` — query params for every facet + `q`,
     `sort`, `limit`, `offset` → `{ total, results }`.
   - `GET /v2/indices/snsf/grants/facets` — same params → `{ facet: [{value,
     count}, …] }`.
   - Token-gated, `tags=["Indices"]`, mirroring the existing snsf endpoints.
   - Reads the snsf store read-only (the `.ro` snapshot in serving).

## Delivery (PR per phase, full `tests/v2/` gate each)

- **Phase A** — `facets.sql` + `build_facets()` + the 3 tables; CLI
  `build-facets`; tests asserting the flattening/rollups against a small
  fixture store.
- **Phase B** — `facet_query.py` (`GrantFilters`, `query_grants`,
  `facet_counts`) + FTS; tests for each facet filter, free-text, sort,
  pagination, and facet-count semantics.
- **Phase C** — the CLI `facet-search` + the two HTTP endpoints + endpoint
  tests.
- **Phase D (optional)** — hybrid: intersect facet filters with the existing
  Qdrant semantic search for conceptual queries.

## Testing

Per the existing snsf tests, with a small hand-built fixture store (a handful
of grants + persons + outputs): `build_facets` produces the right rows;
`query_grants` honours each filter, free-text, sort, paging; `facet_counts`
returns the right per-facet counts under an active filter set; endpoints return
the documented shapes and are auth-gated.

## Risks / notes

- Facet tables are derived → must be rebuilt after any grants/persons/output
  reload. `build_facets()` is wired into the ingest flow + the maintenance
  story (the `.ro` snapshot picks them up).
- `persons.*_grants` arrays hold the grant **URL** ids post-v3.0.0; the
  flattening + the `grants` join both use the URL, so no id-shape mismatch.
- DuckDB FTS extension availability — fall back to `ILIKE` on
  title+abstract+keywords if the FTS extension isn't loadable in a given env.
