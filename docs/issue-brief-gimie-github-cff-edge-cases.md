# Issue brief: GIMIE / PyYAML failures on GitHub repos

This document summarizes failures when calling **`GET /v1/repository/gimie/json-ld/{url}`** (git-metadata-extractor + GIMIE 0.7.x) against certain GitHub repositories. Use it to open issues on **gimie**, **repository templates**, or internal trackers.

---

## Summary

The endpoint returned **HTTP 400** with bodies like `{"message":"Expecting value: line 1 column 1 (char 0)"}` or `{"message":"month must be in 1..12"}`. The API maps uncaught **`ValueError`** (and related errors) to that JSON shape.

Root causes fall into four areas:

1. **GIMIE** mishandles GitHub **`204 No Content`** on the contributors REST endpoint (empty body).
2. **GIMIE** assumes GraphQL **`repository.object`** is always non-null for `HEAD:` (empty / no-tree repos).
3. **PyYAML** parses invalid **`date-released`** (and similar) values in **`CITATION.cff`** as YAML 1.1 timestamps, raising **`ValueError`** during **`safe_load`**.
4. **GitHub** may return **secondary (or primary) rate limit** errors on GraphQL contributor queries; GIMIE surfaces these as **`ConnectionError`**.

---

## Environment

| Component | Notes |
|-----------|--------|
| Consumer | git-metadata-extractor GIMIE JSON-LD route |
| Library | `gimie==0.7.2` (PyPI) |
| YAML | PyYAML `safe_load` in `gimie.parsers.cff` |
| GitHub | Authenticated API (`GME_GITHUB_TOKEN`) |

---

## Issue A — Empty repository: contributors endpoint returns 204

### Reproduction

- Repository with **no commits** (example: `raj921/dream-home-ai-`).
- GIMIE requests `GET https://api.github.com/repos/{owner}/{repo}/contributors`.
- GitHub responds with **204 No Content** and an **empty body** (contrast: normal repos return **200** and a JSON array).

### Root cause

In `gimie.extractors.common.queries.send_rest_query`, any `status_code != 200` path calls `resp.json().get("message", "")`. For **204**, `resp.json()` runs on an empty body and raises **`json.JSONDecodeError`**, which surfaces as **“Expecting value: line 1 column 1 (char 0)”**.

### Suggested upstream fix (gimie)

- Treat **204** for list-style reads as an **empty list** `[]`, or avoid calling `resp.json()` when the body is empty.

---

## Issue B — Empty / no-tree repository: GraphQL `object` is null

### Reproduction

- Same class of repository (no real `HEAD` tree).
- GraphQL `repository.object(expression: "HEAD:")` returns **`null`**.

### Root cause

`GithubExtractor.list_files` uses `self._repo_data["object"]["entries"]`, which raises **`TypeError`** when `"object"` is **`None`**.

### Suggested upstream fix (gimie)

- If `object` is **`None`**, return **no files** (e.g. `[]`) and skip file-based parsers.

---

## Issue C — `CITATION.cff` dates: PyYAML timestamp constructor + invalid templates

### Reproduction

Repositories ship **`CITATION.cff`** with **`date-released:`** (or related keys) set to **invalid calendar values**. Examples observed in **`sdsc-ordes`** repositories (from raw `CITATION.cff`):

| Repository | Example `date-released` |
|------------|-------------------------|
| debates-analytics | `2025-28-04` (ISO day/month reversed) |
| nds-lucid-web-app | `2025-13-14` |
| history-rewrite | `2025-19-16` |
| mava-api | `2025-53-05` |
| repository-template-rust | `2025-15-27` |
| open-pulse-hackalysis | `2026-39-13` |

### Root cause

PyYAML (YAML 1.1) treats **unquoted** scalars matching `YYYY-M-D` as **timestamps** and constructs `datetime.date(year, month, day)`. Invalid months or impossible dates raise **`ValueError: month must be in 1..12`** (or similar) inside **`yaml.safe_load`**, invoked from **`gimie.parsers.cff`** (e.g. `get_cff_doi`, `get_cff_authors`).

**Important:** GIMIE’s CFF logic only **uses** identifiers and authors from the parsed dict; it does **not** consume `date-released` for emitted triples. However, **`safe_load` parses the entire document**, so a broken date still **fails extraction** before DOI/author handling.

### Suggested upstream fixes

**GIMIE (defensive):**

- Preprocess CFF text before `safe_load`, use a loader that does not auto-coerce timestamps for these scalars, or wrap `safe_load` and degrade with a warning (skip CFF) on `ValueError`.

**Templates / authoring (organizational):**

- Emit valid **`YYYY-MM-DD`** per the [Citation File Format](https://github.com/citation-file-format/citation-file-format) spec.
- If a field must remain free-form, **quote** the value in YAML so it is loaded as a **string**, not a timestamp.

---

## Issue D — GitHub secondary / primary rate limits

### Reproduction

- Many **`force_refresh=true`** calls or high crawl concurrency against **`GET /v1/repository/gimie/json-ld/...`** with the same **`GME_GITHUB_TOKEN`**.
- GIMIE runs GraphQL **`query_contributors`**; GitHub responds with an error body such as *“You have exceeded a secondary rate limit…”*.

### Root cause

`gimie.extractors.common.queries.send_graphql_query` raises **`ConnectionError`** when the HTTP status is not success or the GraphQL payload reports failure.

### Mitigation

- Prefer **cached** responses; avoid **`force_refresh=true`** on every repository in a batch crawl.
- **git-metadata-extractor** maps **`ConnectionError`** on the GIMIE JSON-LD route to **HTTP 429** when the message indicates a rate limit, and **HTTP 503** for other GitHub **`ConnectionError`** cases (clearer than an uncaught stack trace).

---

## Consumer-side mitigation (git-metadata-extractor)

For production resilience, this repository may apply **local monkeypatches** (not a substitute for fixing upstream gimie or templates):

- Patch **`send_rest_query`** so **204** → **`[]`**.
- Patch **`GithubExtractor.list_files`** so **`object is None`** → **`[]`**.
- Wrap **`CffParser.parse`**: normalize fixable reversed day/month dates; **quote** remaining invalid `YYYY-M-D` tokens on known keys (`date-released`, `date-published`, `date-last-released`) so PyYAML returns strings.
- **GIMIE route** (`src/api.py`): explicit **`ConnectionError`** handling → **429** / **503** with GitHub’s message in **`detail`**.

---

## How to verify a fix

After deploying gimie or API changes:

1. **Empty repo:**  
   `GET .../v1/repository/gimie/json-ld/https%3A%2F%2Fgithub.com%2Fraj921%2Fdream-home-ai-?force_refresh=true`  
   Expect **200** and JSON-LD, not 400.

2. **Bad CFF dates:**  
   `GET .../v1/repository/gimie/json-ld/https%3A%2F%2Fgithub.com%2Fsdsc-ordes%2Fnds-lucid-web-app?force_refresh=true`  
   Expect **200** and JSON-LD, not `month must be in 1..12`.

---

## Suggested issue split

| Audience | Scope |
|----------|--------|
| **gimie** (e.g. sdsc-ordes/gimie) | Issues **A**, **B**, and defensive handling for **C** in one or two GitHub issues. Optional: retries/backoff for **D** (coordinate with GitHub ToS / abuse guidelines). |
| **Template / org owners** | Issue **C** only: correct `CITATION.cff` `date-released` (and generator scripts) across org templates. |
| **API consumers / crawlers** | Issue **D**: backoff, concurrency limits, and avoid unconditional **`force_refresh`**. |

---

## References (in-tree)

- GIMIE integration and workarounds: `src/gimie_utils/gimie_methods.py`
- Regression tests: `tests/test_gimie_empty_github_repo.py`, `tests/test_gimie_cff_date_normalize.py`
