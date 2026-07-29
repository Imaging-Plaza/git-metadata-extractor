# Plan — replace in-process `gimie` with a `gimie-api` sidecar

**Date:** 2026-06-07
**Status:** plan (spike-first)
**Motivation:** `gimie==0.7.2` (the only release) and its `calamus` dependency
hard-pin `python-dotenv<0.22` and `marshmallow<3.24`, blocking 3 Dependabot
alerts that can't be fixed any other way. Moving gimie out of our Python process
into a sidecar HTTP service (`ghcr.io/sdsc-ordes/gimie-api`) lets us **drop
`gimie` from `pyproject.toml`** entirely, freeing those transitive pins — and
decouples a heavy, rarely-updated dependency from our tree.

## What gimie-api gives us (verified from `sdsc-ordes/gimie-api`)
- `GET /gimie/jsonld/{full_path:path}` → `{"link": <url>, "output": "<json-ld string>"}`.
  Internally `Project(full_path).extract().serialize(format='json-ld')` — i.e.
  **identical to our `extract_gimie`** (same gimie 0.7.2), except `output` is the
  serialized JSON-LD **string** (we already `json.loads` that string today).
- Also `/gimie/ttl/{path}` and `/`. Listens on **:15400** (uvicorn). GitHub token
  via env **`ACCESS_TOKEN`**.
- ⚠️ **Error contract:** on failure it returns **HTTP 200** with `output` set to
  the *error message string* (not valid JSON-LD, no error status). The client
  MUST detect this.

## Current seam (what we swap)
- `src/v1/gimie_utils/gimie_methods.py::extract_gimie(full_path, fmt)` →
  `json.loads(Project(full_path).extract().serialize("json-ld"))`. The single
  Project-based entry point.
- v2: `RealGitHubProvider._resolve_gimie_extractor()` lazy-imports `extract_gimie`
  and stores it as `self._gimie_extractor` (a `(url, fmt) -> dict` callable). The
  provider consumes the JSON-LD `@graph`.
- v1: `src/v1/analysis/repositories.py::run_gimie_analysis` calls `extract_gimie`.
- ⚠️ **Deeper imports:** `gimie_methods.py` also imports
  `gimie.extractors.github.GithubExtractor` and `gimie.parsers.cff.CffParser`
  directly. To fully drop the `gimie` package these must also be removed/replaced
  — see Phase 3.

## Phase 1 — Spike & parity test (no production change, low risk)
Goal: prove the API path returns byte-for-parity JSON-LD vs in-process gimie.

1. Add a **dev-only** `gme-gimie-api` service to `.devcontainer/docker-compose.yml`
   (mirror `gme-qdrant`/`gme-selenium-firefox`): image
   `ghcr.io/sdsc-ordes/gimie-api@<digest>` (pin a digest, not `:latest`),
   `networks: [dev]`, `environment: { ACCESS_TOKEN: ${GME_GITHUB_TOKEN} }`,
   no host port needed (internal `http://gme-gimie-api:15400`).
2. Add a thin client `extract_gimie_via_api(full_path, fmt="json-ld")` (new module,
   e.g. `git_metadata_extractor/providers/gimie_api_client.py`):
   - `GET {GIMIE_API_URL}/gimie/jsonld/{full_path}` (full_path is the repo URL with
     scheme — the `:path` converter handles `https://…`). Generous timeout
     (gimie extraction is slow: 30–120s) + bounded retry, mirroring the existing
     `_run_with_rate_limit` / malformed-JSON retry.
   - Parse: `payload = resp.json(); out = json.loads(payload["output"])`. If
     `payload["output"]` is not valid JSON / lacks `@graph` → treat as failure
     (the HTTP-200-error gotcha) and return `None`/`{}` exactly like the current
     extractor degrades.
3. **Parity harness** (throwaway script): for ~10 diverse repos, call both
   `extract_gimie` (in-process) and `extract_gimie_via_api`, normalise (sort
   `@graph` by `@id`) and diff. Expect zero semantic diff (same gimie version).
   Record any differences (ordering, blank-node ids).

Exit criteria: parity confirmed on the sample; client handles the error contract.

## Phase 2 — Wire behind a flag (opt-in)
- Gate the seam in `RealGitHubProvider._resolve_gimie_extractor()`: if
  `GIMIE_API_URL` is set → use `extract_gimie_via_api`; else keep the in-process
  `extract_gimie` (default, unchanged). Same for the v1 caller.
- Add `gme-gimie-api` to the **deploy** compose / k8s (wherever `gme-qdrant` is
  provisioned in prod — confirm with ops; the repo ships only the dev compose and
  the app image, so the production manifest lives outside this repo).
- Run the full `tests/v2` gate with the flag both off (unchanged) and on
  (client mocked — no real sidecar in CI). Add unit tests for
  `extract_gimie_via_api` (success, error-string-as-200, timeout) with a fake
  session, mirroring the registry-provider tests.
- Soak in dev/staging with `GIMIE_API_URL` pointed at the sidecar.

## Phase 3 — Drop the dependency (the actual win)
Once the API path is proven in prod:
1. Replace the remaining **direct gimie imports** in
   `src/v1/gimie_utils/gimie_methods.py` (`GithubExtractor`, `CffParser`) — either
   route them through the API too, or move that CFF parsing to our own
   `git_metadata_extractor/parsers/citation_cff.py` (which already exists). Audit every
   `import gimie` / `from gimie` site (Phase-0 grep found them in v1 only).
2. Remove `"gimie==0.7.2"` from `pyproject.toml` `dependencies`. Make the
   in-process `extract_gimie` import gimie lazily and raise a clear error if
   `GIMIE_API_URL` is unset and gimie isn't installed (so the fallback degrades
   gracefully rather than ImportError at module load).
3. `uv lock` → `python-dotenv` and `marshmallow` are now free to upgrade. Bump
   them (`python-dotenv>=1.2.2`, and let `marshmallow` resolve to ≥3.26.2) →
   the 3 remaining Dependabot alerts clear.
4. Full `tests/v2` + `tests/index` gate.

## Risks & open questions
- **Latency / availability:** every repo extraction is now a network round-trip to
  a slow service. Need a sane timeout + retry, and a **fallback** when the sidecar
  is down (degrade to empty gimie payload, like today's error path — the v2
  pipeline already tolerates an empty gimie graph).
- **Token:** gimie-api reads `ACCESS_TOKEN`; map our `GME_GITHUB_TOKEN` to it in
  compose. (No token pooling/rotation inside the sidecar — single token.)
- **Image pinning:** pin `gimie-api` by digest; `:latest` is gimie 0.7.2 today but
  could drift.
- **Prod topology:** the production deployment compose/k8s is not in this repo —
  Phase 2 needs an ops change to run the sidecar alongside the API.
- **v1 surface:** v1 still imports gimie submodules directly; Phase 3 must cover
  those or the package can't be removed.
- **Parity:** same gimie version ⇒ expected identical output, but blank-node ids
  and `@graph` ordering may differ run-to-run — normalise before diffing and in
  any downstream assumptions.

## Payoff
- Drops `gimie` + `calamus` from the dependency tree → unblocks the last 3
  Dependabot alerts (`python-dotenv`, `marshmallow`).
- Decouples a heavy, single-release, rarely-updated dependency; gimie upgrades
  become a sidecar image bump, not a Python re-resolve.
