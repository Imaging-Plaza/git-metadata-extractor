# Repository enrichment fields (`gme-internal:*`)

The v2 repository pipeline collects a broad layer of provider metadata that the
Open Pulse ontology does not (yet) model. These surface under the auxiliary
`gme-internal:` JSON-LD vocabulary
(`https://openpulse.science/git-metadata-extractor#`).

!!! note "How to see them"
    They are **stripped by default** (ontology-compliant output). Add
    **`?include_internal_fields=true`** to `GET /v2/extract/{path}` to keep them.
    Each `_`-prefixed entity field becomes a `gme-internal:<field>` term that
    expands to a real IRI, so the payload still loads into an RDF store. Strict
    SHACL validation runs identically either way — the flag only changes what the
    consumer sees. Fields read "no data" (absent / `null`) until a repository is
    (re-)extracted.

All fields below are **deterministic** (rule-based, computed in `context_gather`
+ the repository agent) unless noted, so they are independent of the
`agent_runtime` (`rule_based` / `llm` / `hybrid`).

## Repository metadata

From the GitHub repository object.

| Field | Meaning |
|---|---|
| `description` | Repo tagline |
| `homepage` | Project website set on the repo |
| `primary_language` | GitHub's primary language |
| `keywords` | Repository topics |
| `default_branch`, `visibility`, `archived`, `disabled`, `size_kb` | Repo state |
| `pushed_at`, `updated_at` | Last push / metadata update |
| `watchers_count`, `subscribers_count`, `network_count`, `open_issues_count` | Activity counters |
| `avatar_url` | Owner avatar |
| `license_name`, `license_url` | License (also `schema:license`) |
| `has_wiki`, `has_pages`, `has_discussions`, `has_issues`, `has_projects` | Enabled GitHub features |

## CI, coverage & community health

| Field | Source |
|---|---|
| `has_ci` | repo-root listing (`.github/`, `.travis.yml`, …) |
| `test_coverage` | README coverage badge / text (LLM fallback via `repo_signals` refiner) |
| `community_health_percentage` | `GET /community/profile` |
| `has_code_of_conduct`, `has_issue_template`, `has_pull_request_template` | community profile |

## Releases & tags

| Field | Meaning |
|---|---|
| `releases` | Raw GitHub releases list (full detail) |
| `latest_version` | Newest release tag |
| `release_count`, `first_release_date`, `latest_release_date` | Flat scalars for **release frequency** |
| `git_tags`, `git_tag_count` | Git tags (for repos that tag without cutting Releases) |

> The raw `releases` list-of-objects collapses to blank nodes on RDF expansion;
> the flat `release_count` / `first_release_date` / `latest_release_date` triples
> are the queryable form.

## Container distribution

| Field | Meaning |
|---|---|
| `docker_hub_url` | Docker Hub repo parsed from README / compose |
| `container_images` | Raw GHCR container packages (needs `read:packages` scope) |
| `package_count`, `package_names`, `package_image_refs`, `package_tags`, `latest_package_updated_at` | Flat GHCR scalars |

## Published packages (per registry)

For each ecosystem **`<eco>` ∈ {`npm`, `pypi`, `conda`, `crates`, `rubygems`, `maven`, `go`, `nuget`}**:

| Field | Meaning |
|---|---|
| `<eco>_package` | Package / coordinate name (Go: module path; Maven: `group:artifact`) |
| `<eco>_latest_version` | Newest published version |
| `<eco>_versions` | All versions (capped) |
| `<eco>_latest_release_date` | Latest release timestamp |
| `<eco>_registry_url` | Human-facing registry page |
| `<eco>_link` | `verified` (registry's repo URL back-references this repo) or `name_only` |

Extras: `conda_channel`, `maven_group_id`, `maven_artifact_id`.

**Discovery sources:** npm/PyPI from `package.json` / `pyproject.toml` (+ badge
fallback); conda/RubyGems/NuGet from README badges; crates from `cargo.toml` (+
badge); Maven from `pom.xml` (+ badge); Go from `go.mod`. A registry result is
**dropped** when its declared repository URL points at a *different* repo
(name-collision guard).

## Documentation, badges & governance

| Field | Meaning |
|---|---|
| `documentation_urls` | Documentation-site URLs (`gme-internal:hasDocumentation`; README + LLM `repo_signals` refiner) |
| `badges`, `badge_count` | Parsed README badges (`{label, image_url, link_url}`) |
| `funding_urls` | Sponsorship URLs from `.github/FUNDING.yml` (GitHub Sponsors, Patreon, Open Collective, …) |
| `citation_cff` | Parsed `CITATION.cff` payload |
| `publiccode` | Parsed `publiccode.yml` payload (scalars also hoisted to `publiccode:*`) |
| `citation_cff_url`, `authors_url`, `contributing_url`, `code_of_conduct_url`, `security_url`, `publiccode_url` | URL pointers to repo-root governance files |

## Not captured (by design)

- `is_template` / `allow_forking` — trivial booleans, negligible signal.
- `security_and_analysis` (Dependabot / secret-scanning state) — requires a
  push/admin token scope, so not collected.
- npm/PyPI for GitHub's own `npm.pkg.github.com` registry — we target the public
  registries (npmjs.com / PyPI / etc.) instead.
