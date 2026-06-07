# npm + PyPI package discovery (manifest-linked)

**Date:** 2026-06-07
**Branch:** `feat/npm-pypi-packages`
**Goal:** Surface a repo's published **npm** and **PyPI** packages as flat
`gme-internal:` triples — discovered by reading the repo's manifest for the
package name, querying the public registry, and verifying the registry's
declared repo URL points back to this repo.

## Why
GitHub Packages does not cover public npmjs.com (only `npm.pkg.github.com`) and
has no PyPI support at all. So we query the registries directly and link by
manifest name + back-reference verification. Mirrors the flat-scalar pattern of
releases (#135) and GHCR packages (#136).

## Existing facts to build on
- `get_repository_aux_files(full_name)` already fetches `package.json`,
  `pyproject.toml`, `setup.cfg`, `setup.py` (and others) into the `aux_files`
  dict (`{filename: content}`), available in `context_gather` and passed to the
  repository agent as `compiled_context["aux_files"]`.
- `context_gather` already stores best-effort extras into `repository_metadata`
  (`releases`, `container_images`, `has_ci`) which the repository agent reads via
  `repository.get(...)` and emits as `_`-prefixed internal fields.
- `ProviderSet` (`src/v2/agents/models.py`) is the DI bundle; real instances are
  built in `src/v2/dependencies.py` (mock path ~L351, real paths ~L358/L445).
- Flat-scalar pattern: `summarize_releases` / `summarize_packages` in
  `_repo_signals.py`, splatted into the entity dict in `repository_agent.py`,
  emitted as `gme-internal:*` by `jsonld_build.py` under `?include_internal_fields=true`.
- Python 3.12 → `tomllib` is stdlib; `configparser` for setup.cfg.

## Design

### 1. Manifest name parsing — pure helpers in `_repo_signals.py`
- `parse_npm_name(aux_files) -> str | None`: `json.loads(aux_files["package.json"])`,
  return `name` (keep scoped names `@scope/pkg` intact). None on missing/malformed/
  empty/`private: true`.
- `parse_pypi_name(aux_files) -> str | None`: try `pyproject.toml` via `tomllib`
  → `[project].name` then `[tool.poetry].name`; fallback `setup.cfg` via
  `configparser` → `[metadata] name`. (Skip `setup.py` — unsafe to exec; a
  conservative `name=["']...["']` regex is optional, low priority.) None when no
  name found. Do NOT PEP503-normalise (PyPI accepts the project name as-is).
- `repo_url_matches(candidate_url, full_name) -> bool`: normalise `candidate_url`
  (strip `git+`, leading `git://`/`ssh://git@`/`git@github.com:`, trailing `.git`
  and `/`, lowercase host) and return True iff it resolves to
  `github.com/<full_name>` (case-insensitive on owner/repo). False on None/empty/
  non-github/mismatch.

### 2. Registry provider — `src/v2/ingest/providers/package_registry_provider.py` (NEW)
`class PackageRegistryProvider` with an injectable `session` (a
`requests.Session`-like with `.get(url, timeout=...)`; default `requests`) and
optional `ProviderCache` (cache by method+name, like the github provider). Both
methods are **best-effort**: return `None` on 404 / non-200 / parse / transport
error; never raise.

- `get_npm_package(name) -> dict | None` — GET `https://registry.npmjs.org/<name>`
  (URL-encode scoped: `@scope/pkg` → `@scope%2Fpkg`). Extract:
  - `name`
  - `latest_version` ← `dist-tags.latest`
  - `versions` ← sorted list of `versions` keys (cap ~200, log if truncated)
  - `latest_release_date` ← `time[<latest_version>]` (ISO 8601)
  - `repository_url` ← `repository.url` (top-level), else the latest version's
    `repository.url`
  - `registry_url` ← `https://www.npmjs.com/package/<name>`
- `get_pypi_package(name) -> dict | None` — GET `https://pypi.org/pypi/<name>/json`.
  Extract:
  - `name` ← `info.name`
  - `latest_version` ← `info.version`
  - `versions` ← sorted list of `releases` keys (cap ~200)
  - `latest_release_date` ← max `upload_time_iso_8601` across `releases[latest]`
    (or `urls`)
  - `repository_url` ← `info.project_urls` (`Source`/`Repository`/`Code`/`Homepage`,
    in that priority), else `info.home_page`
  - `registry_url` ← `https://pypi.org/project/<name>/`

### 3. `context_gather` wiring
After the aux-files block, gated on `providers.package_registry is not None`,
best-effort (try/except → `warnings`):
- `npm_name = parse_npm_name(aux_files)`; if set, `pkg = providers.package_registry
  .get_npm_package(npm_name)`. Link policy:
  - `repository_url` present AND `repo_url_matches(..., full_name)` → store with
    `pkg["link"] = "verified"`.
  - `repository_url` present AND mismatch → **drop** (false match; do not store).
  - `repository_url` absent → store with `pkg["link"] = "name_only"`.
  Store as `repository_metadata["npm_package"]`.
- Same for `parse_pypi_name` → `get_pypi_package` → `repository_metadata["pypi_package"]`.

### 4. `repository_agent.py` + `_repo_signals.py` flat scalars
Add `summarize_registry_package(pkg) -> dict` in `_repo_signals.py` returning the
always-present keys: `package` (name), `latest_version`, `versions` (list|None),
`latest_release_date`, `registry_url`, `link` — all None when `pkg` is None/not a
dict. In `repository_agent.py`, splat twice with prefixes:
```python
**{f"_npm_{k}": v for k, v in summarize_registry_package(repository.get("npm_package")).items()},
**{f"_pypi_{k}": v for k, v in summarize_registry_package(repository.get("pypi_package")).items()},
```
→ emits `gme-internal:npm_package / npm_latest_version / npm_versions /
npm_latest_release_date / npm_registry_url / npm_link` and the `pypi_*` mirror.

### 5. `dependencies.py`
Instantiate `PackageRegistryProvider` for the **real** ProviderSet paths, gated by
env `V2_PACKAGE_REGISTRY_ENABLED` (default `true`; `false`/`0`/`no`/`off` → None).
The mock path stays `package_registry=None` (mock extractions never hit network).
Add `package_registry: Any | None = None` to `ProviderSet`.

## Tests (`tests/v2/`)
- `parse_npm_name` / `parse_pypi_name`: valid, scoped npm, poetry table, setup.cfg
  fallback, missing, malformed, `private: true`.
- `repo_url_matches`: `git+https://github.com/o/r.git`, `ssh://git@github.com/o/r`,
  `https://github.com/o/r/`, case-insensitive, non-github → False, mismatch → False.
- `PackageRegistryProvider` with an injected fake session returning canned npm +
  PyPI JSON → asserts the thin dict (incl. versions/latest/date/repository_url).
  404 → None. **No real network.**
- `summarize_registry_package`: full dict, None input (all-None).
- `context_gather`: fake `package_registry` provider → `npm_package`/`pypi_package`
  populated; verified vs name_only; **mismatch dropped**.
- `repository_agent`: emits flat `_npm_*` / `_pypi_*`; all-None when manifests
  absent or provider None.

## Gate
Full `tests/v2/` green. No real network/LLM in tests (inject session / mock
provider). Match house lint (ruff is not a CI gate; keep new files clean).
