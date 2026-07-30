# Bug 06 — V2_GITHUB_RAG_AUTO_INGEST not firing (bug vs narrower-than-named)
**Severity:** low-medium · **Status:** Investigated — plan ready (no code changed) · **Area:** config / auto-ingest

## Symptom
Operators set `V2_GITHUB_RAG_AUTO_INGEST=true` (the name documented in
`.env.example`) and expected GitHub repos extracted via `/v2/extract` to be
auto-added to the `github_repos` RAG index. They were not — the index stayed
empty until `/v2/indices/github_repos/ingest` was called explicitly. Auto-ingest
silently never fired, with no error and no log line explaining why.

## What the flag actually does (verified, file:line)

**There are two different names involved, and they don't match.**

- **Documented name (operator-facing):** `.env.example:402` is the only place
  `V2_GITHUB_RAG_AUTO_INGEST` appears in the entire repo. The comment block
  above it (`.env.example:386-401`) describes precisely the github_repos
  auto-ingest behavior (skip-if-indexed, fetch metadata + README, persist to
  DuckDB, embed README chunks into the `github_repos` Qdrant collection).
  This is the name operators copy.

- **Name the code actually reads:** `src/v2/api.py:1879`
  ```python
  if os.getenv("V2_GITHUB_REPOS_RAG_AUTO_INGEST", "false").strip().lower() != "true":
      return
  ```
  Note the extra `_REPOS_`. A `grep` across `src/` and `tests/` for the bare
  documented name `V2_GITHUB_RAG_AUTO_INGEST` returns **zero** source hits — no
  code path ever reads it. The regression tests confirm the real name:
  `tests/v2/test_github_auto_ingest_client_signature.py:129` and `:167` both
  set/clear `V2_GITHUB_REPOS_RAG_AUTO_INGEST`.

**Where the hook is wired and what it gates.** After a successful
`/v2/extract`, once the response model and query log are written, four
fire-and-forget hooks run (`src/v2/api.py:1786-1801`):
- `_maybe_schedule_github_repos_auto_ingest` → reads `V2_GITHUB_REPOS_RAG_AUTO_INGEST` (`api.py:1879`)
- `_maybe_schedule_github_users_auto_ingest` → reads `V2_GITHUB_USERS_RAG_AUTO_INGEST` (`api.py:1976`)
- `_maybe_schedule_github_orgs_auto_ingest` → reads `V2_GITHUB_ORGS_RAG_AUTO_INGEST` (`api.py:2064`)
- `_maybe_schedule_huggingface_papers_auto_ingest` → reads `V2_HF_PAPERS_RAG_AUTO_INGEST` (`api.py:2162`)

**Exact conditions for the github_repos auto-ingest to fire** (all must hold,
`src/v2/api.py:1879-1892`):
1. `V2_GITHUB_REPOS_RAG_AUTO_INGEST == "true"` (default `"false"`) — `api.py:1879`.
2. `classification.detected_type.value.lower() == "repository"` — `api.py:1883`.
   So it fires **only for repository targets**, not users/orgs/articles (those
   have their own separate flags above).
3. `normalized_url` contains `github.com/` — `api.py:1886`.
4. The path is exactly `owner/repo` (`full_name.count("/") == 1`) — `api.py:1891`.

Then, inside the background task (`api.py:1894-1953`):
5. A running asyncio event loop must exist; `asyncio.create_task` raising
   `RuntimeError` is swallowed silently (`api.py:1955-1961`). Under uvicorn this
   is fine; in synchronous contexts it no-ops.
6. `cfg.require_github()` requires `GME_GITHUB_TOKEN` to be set, else it raises
   `ValueError` (`src/index/github_repos/config.py:85-87`) which is caught and
   logged as "...: failed" (`api.py:1944-1948`).
7. Already-indexed repos return `skipped_already_indexed` (`api.py:1924-1926`);
   private/unreachable repos return `skipped_404` (`api.py:1935-1936`).
8. Embedding into Qdrant happens via `embed_repos(...)` (`api.py:1937`), which
   uses the Qdrant config from `load_github_config()`.

So the behavior, as coded, **does match the documented description** (public
repos, README embedding, skip-if-indexed). It is *not* narrower than the name
in scope — the only discrepancy is the **flag name itself**.

## Verdict: bug or semantics/doc gap

**This is a real, operator-visible bug — specifically a config/documentation
name mismatch.** It is not a semantics or "narrower than named" issue: the code
path, when reached, does exactly what the docs promise. The defect is that the
*only documented way to turn it on* (`V2_GITHUB_RAG_AUTO_INGEST`, `.env.example:402`)
is a name **no code ever reads**. The runtime gate at `src/v2/api.py:1879`
requires `V2_GITHUB_REPOS_RAG_AUTO_INGEST` instead. Setting the documented flag
leaves the env var the code checks unset → it defaults to `"false"` → the guard
returns early → auto-ingest never runs, with no log line.

Code evidence:
- Documented name appears only at `.env.example:402`; zero source readers.
- Real gate reads `V2_GITHUB_REPOS_RAG_AUTO_INGEST` at `api.py:1879` (confirmed
  by tests at `test_github_auto_ingest_client_signature.py:129,167`).

Severity is **low-medium**: no data corruption, no crash, and an explicit
manual ingest workaround exists — but the feature is effectively dead for any
operator who follows the documentation.

Secondary silent no-op worth flagging (not the primary bug): even with the
correct flag, if `GME_GITHUB_TOKEN` is unset, `require_github()` raises and the
ingest fails — currently surfaced only as a generic "failed" log
(`api.py:1944-1948`), not a clear "token missing" message.

## Proposed fix

Primary fix is a **documentation/name reconciliation**. Two viable options;
**Option A is recommended** (least surprising, keeps the `_REPOS_` family
consistent with users/orgs/HF flags).

**Option A — Fix the docs to match the code (recommended).**
Update `.env.example` so the documented flag is the one the code reads. Change
`.env.example:402` from:
```
# V2_GITHUB_RAG_AUTO_INGEST=false
```
to:
```
# V2_GITHUB_REPOS_RAG_AUTO_INGEST=false
```
Also document the three sibling flags that are currently **completely
undocumented** in `.env.example` (none of `V2_GITHUB_USERS_RAG_AUTO_INGEST`,
`V2_GITHUB_ORGS_RAG_AUTO_INGEST`, `V2_HF_PAPERS_RAG_AUTO_INGEST` appear there),
so operators can discover the full family:
```
# V2_GITHUB_USERS_RAG_AUTO_INGEST=false
# V2_GITHUB_ORGS_RAG_AUTO_INGEST=false
# V2_HF_PAPERS_RAG_AUTO_INGEST=false
```

**Option B — Accept the documented name in code (back-compat alias).**
If operators may already have `V2_GITHUB_RAG_AUTO_INGEST` baked into deployment
env, also accept it as an alias at `api.py:1879`. Replace the single getenv
read with a small helper that honors both names (new name wins, old name
warns):
```python
def _auto_ingest_enabled(canonical: str, *aliases: str) -> bool:
    for name in (canonical, *aliases):
        raw = os.getenv(name)
        if raw is not None and raw.strip().lower() == "true":
            if name != canonical:
                logger.warning(
                    "auto-ingest enabled via deprecated env var %s; "
                    "rename to %s", name, canonical,
                )
            return True
    return False
```
and call `if not _auto_ingest_enabled("V2_GITHUB_REPOS_RAG_AUTO_INGEST", "V2_GITHUB_RAG_AUTO_INGEST"): return`.

**Recommendation:** ship **Option A** unconditionally (doc correctness is
mandatory), and add **Option B's alias** only if telemetry/support history
shows operators already set the old name. Doing both is cheap and fully removes
operator confusion.

## Observability improvement (logging)
Today the decision point at `src/v2/api.py:1879-1892` returns early in **total
silence** for every reason (flag off, not a repository, malformed URL). An
operator cannot tell "I set the flag wrong" from "auto-ingest correctly skipped
this non-repo target." Add an explicit log at the decision point:

- When the flag resolves to disabled: `logger.debug` (avoid noise on every
  extract) — `"github auto-ingest skipped (run_id=%s): disabled"`.
- When the flag is **on** but a precondition fails (not a repository / bad
  URL): `logger.info` — `"github auto-ingest skipped (run_id=%s, url=%s): not a repository target"`.
- When the flag is **on** and the task is scheduled: `logger.info` —
  `"github auto-ingest scheduled (run_id=%s, repo=%s)"` (just before
  `asyncio.create_task` at `api.py:1956`).
- Promote the missing-token case to a clear `WARN`: in `_do_ingest`
  (`api.py:1918-1920`) catch the `require_github()` `ValueError` specifically
  and log `"github auto-ingest (run_id=%s, repo=%s): GME_GITHUB_TOKEN not set — skipping"` rather than the generic "failed".

These give operators a single grep (`auto-ingest`) to answer "did it run, and
if not, why?" Apply the same pattern to the users/orgs/HF hooks for symmetry.

## Risks & considerations
- **Doc-only change (Option A)** is zero runtime risk. The only "risk" is that
  existing deployments that somehow set `V2_GITHUB_REPOS_RAG_AUTO_INGEST`
  already keep working unchanged (they do).
- **Alias (Option B)** must let the new name take precedence and must not flip
  default-off behavior; the helper above preserves "off unless explicitly true."
- Promoting skip reasons to `INFO` adds one log line per extract when the flag
  is on. That is acceptable (extracts are not hot-loop frequency); keep the
  disabled-case at `debug` to avoid noise on the common path.
- No schema/index changes; the ingest pipeline itself is unchanged and already
  serialized by `_GITHUB_REPOS_AUTO_INGEST_LOCK` (`api.py:1806,1921`).

## Test / verification plan
1. **Unit (env name):** extend
   `tests/v2/test_github_auto_ingest_client_signature.py` with a case asserting
   that setting `V2_GITHUB_RAG_AUTO_INGEST=true` (old name) **without** the new
   name is a no-op under Option A — documents that only the canonical name
   works — and, if Option B is adopted, a case asserting the alias *does* fire
   and emits the deprecation `WARNING`.
2. **Doc lint / grep guard:** add a test (or CI grep) asserting every
   `*_RAG_AUTO_INGEST` name read in `src/v2/api.py` also appears in
   `.env.example`, preventing future drift for all four flags.
3. **Logging:** assert (via `caplog`) that an INFO "scheduled" line is emitted
   when the flag is on and the target is a repository, and an INFO "skipped …
   not a repository target" line when the flag is on but the target is a
   user/org.
4. **Manual smoke:** set the correct flag + `GME_GITHUB_TOKEN`, POST a fresh
   public repo to `/v2/extract`, confirm a new row in the github_repos DuckDB
   and chunks in the Qdrant `github_repos` collection, and confirm the INFO log.
5. **Regression:** existing tests at `:123` and `:164` must still pass
   unchanged.

## Effort estimate
- Option A (doc fix + document 3 sibling flags): ~15 min.
- Decision-point + token-missing logging across the 4 hooks: ~45–60 min.
- Tests (env-name + doc-drift guard + logging asserts): ~45 min.
- Optional Option B alias + deprecation test: ~30 min.
- **Total: ~1.5–2.5 hours**, low complexity, no migration.

## Open questions
1. Do any production deployments already set `V2_GITHUB_REPOS_RAG_AUTO_INGEST`
   correctly, or did everyone copy the broken `.env.example` name? (Determines
   whether the Option B alias is worth shipping.)
2. Should the four `*_RAG_AUTO_INGEST` flags collapse into one master switch
   (`V2_RAG_AUTO_INGEST`) plus per-type overrides, or stay independent? The
   per-type split is intentional today but the naming inconsistency suggests the
   docs never kept up.
3. Should a missing `GME_GITHUB_TOKEN` with the flag on be a startup-time WARN
   (fail-loud at boot) rather than a per-request log, so operators learn before
   the first extract?
