# Plan — Maven, NuGet, and Go module discovery

**Date:** 2026-06-07
**Status:** plan (not yet implemented)
**Extends:** the package-discovery machinery shipped in #138 (npm/PyPI) and #139
(conda/crates/RubyGems + badges).

## Reuse, don't reinvent
All three slot into the **existing pattern** with no new architecture:

- **Provider** — add `get_maven_package` / `get_nuget_package` / `get_go_module`
  to `PackageRegistryProvider` (injectable `session`, `ProviderCache`,
  best-effort None, descriptive `User-Agent`). Same thin-dict shape as the others
  (`name, latest_version, versions, latest_release_date, repository_url,
  registry_url` — Maven adds `group_id`/`artifact_id`).
- **Discovery helpers** — `parse_maven_coords(aux_files)`, `parse_go_module(aux_files)`,
  and NuGet via badges/manifest, in `_repo_signals.py` (pure).
- **Linking** — same `repo_url_matches` + the verified / mismatch-DROP / name_only
  policy in `context_gather`.
- **Emission** — splat `summarize_registry_package(...)` with `_maven_` / `_nuget_`
  / `_go_` prefixes in `repository_agent.py` → `gme-internal:{maven,nuget,go}_*`.
- **Tests** — `tests/v2/` with a fake session (no real network), mirroring
  `test_badges_multi_registry.py`.

The only genuinely new work per ecosystem is (a) the discovery source, (b) the
registry's API + field mapping, and (c) the back-reference verification. Below,
per ecosystem, ranked by effort (Go easiest → NuGet hardest).

---

## 1. Go modules — LOW effort, cleanest verification

**Discovery.** `go.mod` is **already fetched** into `aux_files` (the github
provider's curated manifest list includes `go.mod`). Its first line is
`module <module-path>` — e.g. `module github.com/CSBDeep/CSBDeep` or
`github.com/owner/repo/v2`. `parse_go_module(aux_files)` returns that path.

**Registry — the Go module proxy** (`proxy.golang.org`, no auth):
- versions: `GET https://proxy.golang.org/<esc-module>/@v/list` → newline-separated
  version list.
- latest: `GET https://proxy.golang.org/<esc-module>/@latest` → `{"Version","Time"}`.
- registry_url (human): `https://pkg.go.dev/<module>`.
- **Case escaping gotcha:** the proxy lowercases capitals via `!` escaping —
  `github.com/BurntSushi/toml` → `github.com/!burnt!sushi/toml`. Implement
  `_go_escape(path)` (each uppercase `X` → `!x`).

**Verification — trivial and strong.** The module path *is* the repo for the
GitHub case: verify `module_path == github.com/<full_name>` (optionally a
trailing `/vN` major-version suffix). No second fetch needed → always `verified`
for github-hosted modules; vanity-import domains (custom host that redirects to a
repo via `go-import` meta tags) are out of scope for v1 → `name_only` or skip.

**Emits:** `gme-internal:go_module`, `go_latest_version`, `go_versions`,
`go_latest_release_date`, `go_registry_url`, `go_link`.

**Effort:** ~half a day. No auth, manifest already available, self-verifying.

---

## 2. Maven (Java/Kotlin/Scala) — MEDIUM effort

**Discovery.** `pom.xml` is **already fetched** into `aux_files`.
`parse_maven_coords(aux_files)` parses it (stdlib `xml.etree.ElementTree`,
namespace-aware) → `(groupId, artifactId)`. Notes: `groupId` may be inherited
from `<parent>` (fall back to `<parent><groupId>`); Gradle projects
(`build.gradle`) usually don't carry the coordinates in-repo (group in
`build.gradle`, name in `settings.gradle`) → Gradle discovery is best-effort /
badge-driven (`img.shields.io/maven-central/v/<group>/<artifact>`).

**Registry — Maven Central search** (`search.maven.org`, no auth):
- existence + latest: `GET https://search.maven.org/solrsearch/select?q=g:"<group>"+AND+a:"<artifact>"&rows=1&wt=json`
  → `response.docs[0].latestVersion` + `timestamp` (ms epoch).
- versions: same with `&core=gav&rows=200` → one doc per version (`v` field,
  `timestamp` per version).
- registry_url: `https://central.sonatype.com/artifact/<group>/<artifact>`.

**Verification — weaker; two options:**
- *(a, recommended v1)* The coordinates come from the **repo's own `pom.xml`**, so
  existence-on-Central + coordinates-from-our-repo is already a reasonably strong
  link → store `verified` when the repo pom yielded the coords, `name_only` when
  coords came from a badge.
- *(b, stronger, +1 fetch)* Fetch the released POM
  `https://repo1.maven.org/maven2/<group/path>/<artifact>/<ver>/<artifact>-<ver>.pom`
  and parse `<scm><url>` / `<url>`, then `repo_url_matches`. Adds latency; defer.

**Emits:** `gme-internal:maven_package` (`group:artifact`), `maven_group_id`,
`maven_artifact_id`, `maven_latest_version`, `maven_versions`,
`maven_latest_release_date`, `maven_registry_url`, `maven_link`.

**Effort:** ~1 day (XML parsing + parent-groupId fallback + Solr response shape).

---

## 3. NuGet (.NET) — MEDIUM/HIGH effort (discovery is the hard part)

**Discovery.** NuGet package id lives in `*.csproj` `<PackageId>` (or defaults to
the assembly name), `*.nuspec` `<id>`, or `Directory.Build.props` — **none are in
the fetched `aux_files` curated list, and they're globs**. Options:
- *(a, recommended v1)* **badge-driven** — `extract_registry_coords` already runs;
  add NuGet patterns (link `nuget.org/packages/<id>`,
  image `img.shields.io/nuget/v/<id>` | `img.shields.io/nuget/vpre/<id>`). Most
  .NET READMEs carry a NuGet badge. Zero manifest-fetch changes.
- *(b, later)* extend the github provider's aux-file fetch to grab the first
  `*.csproj`/`*.nuspec` (needs a glob-list step, since the filename is unknown) and
  parse `<PackageId>`/`<id>`. More plumbing; do only if badge coverage proves thin.

**Registry — NuGet v3** (`api.nuget.org`, no auth):
- versions: `GET https://api.nuget.org/v3-flatcontainer/<id-lower>/index.json`
  → `{"versions":[...]}` (latest = last non-prerelease).
- metadata + repo/project URL + dates:
  `GET https://api.nuget.org/v3/registration5-gz-semver2/<id-lower>/index.json`
  (gzip; `catalogEntry.projectUrl`, `.repository`, `.published`), or the search
  endpoint `https://azuresearch-usnc.nuget.org/query?q=packageid:<id>` →
  `projectUrl`.
- registry_url: `https://www.nuget.org/packages/<id>`.

**Verification.** `catalogEntry.repository.url` (when the package opted into
Source Link / repository metadata) or `projectUrl` → `repo_url_matches`. Many
packages omit repository metadata → `name_only` fallback common.

**Emits:** `gme-internal:nuget_package`, `nuget_latest_version`,
`nuget_versions`, `nuget_latest_release_date`, `nuget_registry_url`, `nuget_link`.

**Effort:** ~1–1.5 days (gzip registration handling + the manifest-vs-badge
discovery decision + verification gaps).

---

## Suggested phasing
1. **Go** first (fast win, manifest present, self-verifying).
2. **Maven** (manifest present; medium).
3. **NuGet** (badge-driven v1; revisit manifest fetch if coverage is thin).

Each is an independent PR following the #138/#139 shape: provider method + pure
helper(s) + context_gather wiring + `repository_agent` splat + fake-session
tests, gated through the full `tests/v2/` suite. No new dependencies (stdlib
`xml.etree`, `gzip`, `json`).

## Cross-cutting notes
- **Badge coords already generalise.** `extract_registry_coords` is the natural
  home for `maven-central`, `nuget`, and (rarely) Go badges — add the patterns
  there so badge-only repos are covered uniformly.
- **`repo_url_matches` is github-only today.** Go/Maven/NuGet back-refs are
  github URLs in the common case, so it applies as-is; if we later want GitLab
  back-refs, generalise the host check then.
- **Rate/UA.** All three public APIs are unauthenticated; keep the descriptive
  `User-Agent` and the best-effort None-on-failure contract. Maven Central Solr
  and the NuGet search endpoint are the most rate-sensitive — the existing
  `ProviderCache` covers re-extracts.
