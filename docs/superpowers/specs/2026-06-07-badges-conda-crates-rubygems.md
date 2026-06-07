# README badges + conda / crates.io / RubyGems discovery

**Date:** 2026-06-07
**Branch:** `feat/badges-multi-registry`
**Goal:** (1) Parse README badges into a `gme-internal:badges` field, and (2) use
badges (and manifests) to discover packages on **conda/Anaconda**, **crates.io**,
and **RubyGems**, emitted as flat `gme-internal:*` scalars — extending the
manifest-linked npm/PyPI discovery already shipped (#138).

## Build on existing pattern
- `PackageRegistryProvider` (`src/v2/ingest/providers/package_registry_provider.py`)
  already has `get_npm_package` / `get_pypi_package` (injectable `session`,
  optional `ProviderCache`, best-effort None-on-failure). Add the three new
  registries here, same shape.
- `_repo_signals.py`: `repo_url_matches`, `summarize_registry_package`
  (always-present keys: package, latest_version, versions, latest_release_date,
  registry_url, link). Reuse both. Add badge + coord helpers here (pure, no I/O).
- `context_gather.py`: `_enrich_repository_metadata_with_registry_packages`
  already does parse-name → query → link-policy (verified / mismatch-drop /
  name_only) → store on `repository_metadata`. Extend it.
- `repository_agent.py`: splats `summarize_registry_package(...)` with `_<eco>_`
  prefixes into the entity dict; `jsonld_build` emits `gme-internal:*`.
- `aux_files` already includes `cargo.toml` (crates name source). No gemspec is
  fetched → RubyGems is badge-driven. conda has no repo manifest → badge-driven.
- `repo_url_matches` currently only validates **github.com** URLs; conda/crates/
  RubyGems back-refs are github URLs in the common case, so it applies directly.

## Part 1 — Badge parsing (pure, `_repo_signals.py`)
- `parse_badges(readme) -> list[dict]`: match Markdown linked badges
  `[![alt](image_url)](link_url)` AND plain image badges `![alt](image_url)`
  (link_url None). Return de-duped, order-preserving list of
  `{"label": alt, "image_url": img, "link_url": link_or_None}`. Empty on
  None/empty. Cap ~100, log if truncated.
- `extract_registry_coords(badges) -> dict`: from the badge list, recognise
  registry coordinates (prefer `link_url`, fall back to `image_url`). Return the
  FIRST match per ecosystem:
  - `pypi`: link `pypi.org/project/<name>` | `pypi.python.org/pypi/<name>`;
    image `badge.fury.io/py/<name>` | `img.shields.io/pypi/v/<name>` →
    `{"pypi": "<name>"}`
  - `npm`: link `npmjs.com/package/<name>` (keep scoped `@scope/pkg`);
    image `badge.fury.io/js/<name>` | `img.shields.io/npm/v/<name>`
  - `conda`: link `anaconda.org/<channel>/<name>`; image
    `anaconda.org/<channel>/<name>/badges/...` | `img.shields.io/conda/v/<channel>/<name>`
    → `{"conda": ("<channel>", "<name>")}`
  - `crates`: link `crates.io/crates/<name>`; image `img.shields.io/crates/v/<name>`
  - `rubygems`: link `rubygems.org/gems/<name>`; image
    `img.shields.io/gem/v/<name>` | `badge.fury.io/rb/<name>`
  Return `{}` for nothing recognised. (URL-decode `%2F` etc. as needed; ignore CI
  badges like `github.com/.../workflows/...`.)
- `parse_crates_name(aux_files) -> str | None`: `cargo.toml` via `tomllib` →
  `[package].name`. None on missing/malformed.

## Part 2 — Registry providers (`package_registry_provider.py`)
All best-effort (None on 404 / non-200 / parse / transport; never raise),
cached, and send a descriptive `User-Agent` header (crates.io **requires** one;
set it for all). Each returns the same thin-dict shape as npm/PyPI
(`name, latest_version, versions, latest_release_date, repository_url,
registry_url`) plus conda adds `channel`.

- `get_conda_package(channel, name)` — GET `https://api.anaconda.org/package/<channel>/<name>`.
  Map: `latest_version` ← `latest_version`; `versions` ← `versions` (list, cap ~200);
  `repository_url` ← `dev_url` else `source_git_url` else `html_url`;
  `latest_release_date` ← best-effort from `files`/`ndownloads` if present, else None
  (Anaconda's per-version timestamps are under `files[].upload_time`; take the max,
  else None); `registry_url` ← `https://anaconda.org/<channel>/<name>`;
  add `channel`.
- `get_crates_package(name)` — GET `https://crates.io/api/v1/crates/<name>`.
  Map: `latest_version` ← `crate.max_stable_version` else `crate.newest_version`;
  `versions` ← `[v["num"] for v in versions]` (cap ~200);
  `repository_url` ← `crate.repository`;
  `latest_release_date` ← `crate.updated_at`;
  `registry_url` ← `https://crates.io/crates/<name>`.
- `get_rubygems_package(name)` — GET `https://rubygems.org/api/v1/gems/<name>.json`.
  Map: `latest_version` ← `version`; `repository_url` ← `source_code_uri` else
  `homepage_uri`; `registry_url` ← `https://rubygems.org/gems/<name>`;
  `versions` ← best-effort from a second GET
  `https://rubygems.org/api/v1/versions/<name>.json` → `[v["number"]]` (cap ~200;
  tolerate failure → None); `latest_release_date` ← from that versions payload
  (max `created_at`) else None.

## Part 3 — `context_gather` wiring
In `_enrich_repository_metadata_with_registry_packages`:
- Parse badges from the README (the stage already has README content — thread it
  in if not already a param) → store `repository_metadata["badges"]` when non-empty.
- `coords = extract_registry_coords(badges)`.
- **Strengthen npm/PyPI**: when `parse_npm_name`/`parse_pypi_name` returns None,
  fall back to `coords.get("npm")` / `coords.get("pypi")`.
- **conda**: if `coords.get("conda")` → `(channel, name)` → `get_conda_package`
  → link policy (verified / mismatch-drop / name_only) → store `conda_package`.
- **crates**: name from `parse_crates_name(aux_files)` else `coords.get("crates")`
  → `get_crates_package` → link policy → store `crates_package`.
- **rubygems**: name from `coords.get("rubygems")` → `get_rubygems_package` →
  link policy → store `rubygems_package`.
Same best-effort try/except → `warnings`. Keep the link policy identical to
npm/PyPI (`repo_url_matches` present+match → verified; present+mismatch → DROP;
absent → name_only).

## Part 4 — `repository_agent.py` flat scalars
- Emit raw badges + count:
  `"_badges": (repository.get("badges") or None)`,
  `"_badge_count": (len(repository["badges"]) if isinstance(repository.get("badges"), list) else None)`.
- Splat `summarize_registry_package(repository.get("<eco>_package"))` with
  prefixes `_conda_`, `_crates_`, `_rubygems_` (beside the existing
  `_npm_`/`_pypi_` splats). For conda, ALSO emit
  `"_conda_channel": (repository.get("conda_package") or {}).get("channel")`.

Resulting terms (under `?include_internal_fields=true`):
`gme-internal:badges` (raw list), `gme-internal:badge_count`,
`gme-internal:{conda,crates,rubygems}_{package,latest_version,versions,latest_release_date,registry_url,link}`,
`gme-internal:conda_channel`.

## Tests (`tests/v2/`, NO real network)
- `parse_badges`: linked + plain-image badges, dedupe, empty.
- `extract_registry_coords`: pypi/npm/conda/crates/rubygems via link AND via
  shields/fury image; CI badge ignored; conda channel+name tuple.
- `parse_crates_name`: cargo.toml `[package].name`, missing/malformed.
- Each provider with an injected fake session → thin dict (incl. versions,
  repository_url, conda channel, crates max_stable_version); 404 → None;
  crates User-Agent header asserted present.
- `context_gather`: fake provider + a README with the spec's CSBDeep badges →
  `badges` populated, `conda_package` discovered + verified (dev_url back-ref),
  mismatch dropped; crates from cargo.toml.
- `repository_agent`: emits `_badges`/`_badge_count` + `_conda_*`/`_crates_*`/
  `_rubygems_*` (+ `_conda_channel`); all-None when absent.

## Gate
Full `tests/v2/` green. No real network/LLM. New files ruff-clean; no new lint
in modified files. `repo_url_matches` github-only host check stays strict
(rejects lookalike subdomains).
