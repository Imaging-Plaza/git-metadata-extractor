# Task 11 — GIMIE sidecar JSON-LD integration is broken (route never existed)

**Severity:** P0 (production data quality) ·
**Status: fix option 1 implemented 2026-07-14 (uncommitted) — see bottom** ·
**Repository:** parent · **Discovered:** 2026-07-14 live e2e extraction tests
· **Not a split regression** — predates the split; affects every deployment
since the gimie-sidecar migration.

## Confirmed problem

`src/v2/ingest/providers/gimie_api_client.py` requests
`GET {GIMIE_API_URL}/gimie/jsonld/{repo_url}` for
`serialization_format="json-ld"` (the format `github_provider.py` always
asks for: `_resolve_gimie_extractor()(repository_url, "json-ld")`).

**That route does not exist on gimie-api.** Verified against the running
sidecar's `/openapi.json` on BOTH the digest pinned in the compose files
(`sha256:7a8a59b7…`, resolved 2026-06-07) and `ghcr.io/sdsc-ordes/gimie-api:latest`:

```
routes: /  /test/{string}  /gimie/project/{full_path}  /gimie/ttl/{full_path}
```

- `/gimie/jsonld/...` → HTTP 404 on every request.
- The client treats any failure as degrade-to-`None`, and the pipeline's
  `context_gather` proceeds without a gimie payload — so the breakage is
  **silent** (log line: `gimie-api returned 404 for <url>` at WARNING).
- `/gimie/project/{path}` is not a substitute: it returns a **Python repr**
  of the gimie `Project` object, not JSON.
- `/gimie/ttl/{path}` works and returns Turtle.

## Observable impact (from the live tests)

Extractions "succeed" but without gimie enrichment:

- `schema:description`, `schema:codeRepository`, `schema:keywords` empty on
  every repo (rule_based AND llm runtimes).
- **No contributors discovered** → `person_agents starting — 0 item(s)` →
  no Person/Membership/Contribution entities beyond the owner-fallback stub.
- Secondary artifacts from the empty-author compensation paths: the
  "KNOWN BUG (schema:author empty after reconciliation)" stub
  `https://github.com/https:` and a double-prefixed org IRI
  `https://github.com/https://github.com/sdsc-ordes`.

What still works (verified live): GitHub REST (license/stars/forks/dates),
Infoscience live queries (7 publications found + correctly filtered), ROR
resolution (SDSC → `https://ror.org/02hdt9m26`), LLM enrichment
(language/discipline).

## Deeper finding (2026-07-14, later): the upstream image cannot work at all

Fixing the route alone is insufficient — exercising `/gimie/ttl/` live
returned `output: {}` for every repo. Three compounding upstream defects
(verified by reading `/app/main.py` inside the container):

1. The image (pinned digest AND `:latest`) ships **gimie 0.6.1**, but its
   app calls `proj.serialize(...)` — a **0.7.x API** that doesn't exist in
   0.6.1 → `AttributeError` on every ttl request.
2. The error path does `return {"output": e}` with the raw Exception
   object, which FastAPI JSON-encodes as **`{}`** — the error is
   structurally unreadable (and doesn't even match the documented
   "error message as string" contract).
3. `ACCESS_TOKEN` — the variable the compose files fed — is consumed by
   **nothing except a debug `print`**. The gimie library reads
   `GITHUB_TOKEN`, and 401s on comma-separated PAT pools.

Consequence: the sidecar migration never worked against any published
upstream image; `scripts/v2/gimie_api_parity.py`'s validation claim needs
re-examination.

**Resolution: GME now maintains its own sidecar** (`tools/gimie-api/`:
`Dockerfile` + FastAPI `main.py`, ~120 lines) with gimie **0.7.2** pinned,
the extraction calls mirroring the proven in-process reference
(`Project(url)` → `proj.extract()` → `graph.serialize()`), both `ttl` and
`jsonld` routes, an error contract that returns the message as a string,
and token normalization (accepts `GITHUB_TOKEN`/`ACCESS_TOKEN`, promotes
the first PAT of a comma pool). Both compose files build it from context
(`GIMIE_API_IMAGE` still overrides). Upstreaming the fix to
sdsc-ordes/gimie-api remains desirable but is no longer blocking.

## Fix options

1. **Client-side TTL bridge (recommended, self-contained):** request
   `/gimie/ttl/`, parse with `rdflib` (already a dependency), serialize to
   the JSON-LD shape `_extract_repository_node` expects. Validate the shape
   against the in-process gimie output that the fixtures were captured from.
2. **Fix gimie-api upstream** (sdsc-ordes/gimie-api): add a
   `/gimie/jsonld/{full_path}` route returning the JSON-LD serialization,
   release, re-pin the digest. Cleanest long-term; requires an upstream PR.
3. Both: (2) upstream + (1) as compatibility fallback keyed on a 404.

## Also fixed during diagnosis (uncommitted, keep)

- `tools/deploy/docker-compose.yml`: `ACCESS_TOKEN` for the sidecar now
  prefers `GIMIE_ACCESS_TOKEN` — required when `GME_GITHUB_TOKEN` is a
  comma-separated PAT pool (the extractor round-robins the pool; the
  sidecar needs a single PAT and silently fails to auth with the list).
  Document `GIMIE_ACCESS_TOKEN` in `.env.example` when fixing this task.

## Implementation (2026-07-14, uncommitted)

Option 1 (client-side TTL bridge) is implemented:

- `src/v2/ingest/providers/gimie_api_client.py`: json-ld requests now fetch
  `/gimie/ttl/{path}` and convert with rdflib —
  `json.loads(graph.serialize(format="json-ld"))`, the exact serialization
  the in-process reference (`src/v1/gimie_utils/gimie_methods.py:268`) used,
  so the payload shape is unchanged for every downstream consumer
  (`_extract_repository_node` handles the expanded list natively). The
  HTTP-200 error contract still degrades to `None` (an error message won't
  parse as Turtle).
- `tests/v2/test_gimie_api_client.py` rewritten to the real contract
  (13 pass), including a test that the converted payload survives
  `_extract_repository_node`.
- `.env.example` documents `GIMIE_ACCESS_TOKEN` (compose fix from the
  diagnosis section).

Remaining for this task: the upstream `jsonld` route (option 2, optional),
extending `gimie_api_parity.py` to fail on degrade-to-None, and the
startup/CI route-contract check.

## Acceptance criteria

- A live `/v2/extract` on an org-owned repo yields: non-empty
  `schema:description`, contributor Person entities, Memberships and
  Contributions.
- The `https://github.com/https:` stub and double-prefixed org IRIs no
  longer appear for org-owned repos.
- `scripts/v2/gimie_api_parity.py` actually exercises the JSON-LD path it
  claims to validate (it passed while the route 404'd — extend it to fail
  on degrade-to-None).
- A sidecar route-contract check (openapi assertion) runs in CI or at
  startup so a route mismatch can never be silent again.
