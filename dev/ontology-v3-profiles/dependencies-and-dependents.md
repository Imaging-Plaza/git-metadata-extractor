# Dependencies and dependents — what we have, and what maps

Answers "can we add these too?" for the four dependency terms the raw profile
introduces. Short version: **three of the four are nearly free, and the fourth
cannot be expressed with the data we have** — which turns the "no package
concept" gap in [`README.md`](README.md) from a wishlist item into a concrete
blocker.

Verified 2026-07-30 against GME `3.0.0` and `open_pulse_sources` v0.1.2.

---

## What the raw profile defines

| Term | Type | Notes from `ontology-definitions-raw.ttl` |
|---|---|---|
| `pulse:dependencyCount` | `xsd:integer` | "number of packages this repository depends on" |
| `pulse:dependentCount` | `xsd:integer` | "number of packages/repositories that depend on this repository" |
| `pulse:dependsOn` | object | `rdfs:domain schema:SoftwareSourceCode`, `rdfs:range schema:SoftwareSourceCode`, `owl:inverseOf pulse:dependencyOf` |
| `pulse:dependencyOf` | object | the inverse edge |

Note the asymmetry in their own wording: the *counts* talk about "packages",
while `pulse:dependsOn` is typed repository → repository.

## What we already have

**Outbound (dependencies) — in this repo, cheap.**
`providers/github_provider.py::get_repository_sbom` calls
`GET /repos/{owner}/{repo}/dependency-graph/sbom` and normalises it to
`[{name, ecosystem, version, spdxId}]`, cached through `ProviderCache`.
Ecosystems follow purl conventions (`pypi`, `npm`, `cargo`, `maven`,
`githubactions`, …). It is exposed to the repository LLM agent as the
`query_dependencies` tool (`agents/llm/repository/agent.py:244`).

**Inbound (dependents) — in the child repo, expensive, not wired in.**
`open_pulse_sources.module.dependents` provides `scraper` (Selenium fetch +
pagination + HTML parse), `service.list_dependents(full_name, kind=…)` and
`tool.make_query_dependents_tool`. `kind` is `REPOSITORY` or `PACKAGE` —
GitHub's dependents page has both tabs, and our fixtures cover both. The result
carries what we need to be honest about partial data:

```
DependentsResult{full_name, kind, total_count, fetched_count, truncated,
                 items, available, fetched_at, pages_fetched, warnings}
```

`total_count` is GitHub's own reported total, so we get the true count even
when we only fetch one page. Failures degrade to `available=False` plus
warnings rather than raising.

**Neither is emitted.** There is no `_dependencies` / `_dependents` internal
field, nothing in `schema/json/strict/repository.schema.json`, and the
dependents tool has **zero references** in `git_metadata_extractor/` — this
repo keeps only the fixtures and parser tests, inherited from the pre-split
monolith. Both data sources exist today purely to inform an LLM agent's
reasoning; nothing reaches the graph.

---

## Mapping verdict

| Term | Can we emit it? | How |
|---|---|---|
| `pulse:dependencyCount` | **Yes, trivially** | `len(get_repository_sbom(...))`. One cached API call, already implemented. |
| `pulse:dependentCount` | **Yes** | `DependentsResult.total_count`. Needs the child tool wired in; one Selenium round-trip. |
| `pulse:dependencyOf` | **Yes** | dependents with `kind=REPOSITORY` are `owner/name` handles → real `schema:SoftwareSourceCode` IRIs. Natively typed. |
| `pulse:dependsOn` | **No** | see below |

### Why `pulse:dependsOn` does not map

Its range is `schema:SoftwareSourceCode`, but an SBOM gives **package
coordinates**, not repositories: `pypi:requests`, `npm:lodash`,
`maven:org.apache.commons:commons-lang3`. Emitting `dependsOn` would require
resolving each package to its source repository, which is:

- **lossy** — many packages have no public repository, and the SBOM does not
  carry one;
- **expensive** — one registry lookup per package, on repos with hundreds of
  dependencies;
- **wrong-shaped** — "depends on `pypi:requests@2.31`" is a statement about a
  released artifact at a version. Collapsing it to "depends on
  `github.com/psf/requests`" discards the version and asserts something
  subtly different.

The same applies in reverse to `kind=PACKAGE` dependents: those inbound edges
are package-shaped too, so they cannot be `dependencyOf` edges either.

**This is the strongest argument for the missing package/artifact concept.** Our
dependency data is inherently package-shaped, and the ontology currently accepts
only repository-shaped dependency edges. Options, in preference order:

1. **Ask for a package/artifact node** (`pulse:Package` with a purl-style
   identifier, ecosystem, version) and a `dependsOn` range that admits it. This
   also gives `_conda_channel`, `_maven_group_id`, `_maven_artifact_id` and
   `_latest_version` a home, and unblocks the registry specs in
   `dev/superpowers/specs/`.
2. **Emit counts only** for v4.0.0 — `dependencyCount` and `dependentCount` plus
   `dependencyOf` for repository-kind dependents. Honest, cheap, no new terms
   needed.
3. Resolve packages to repositories. Not recommended: lossy and expensive for a
   weaker statement.

---

## If we implement option 2

Work, roughly in order:

1. Emit `dependencyCount` from the existing SBOM call on the repository entity
   (deterministic, not LLM-derived — same treatment as stars/forks).
2. Wire `make_query_dependents_tool` from the child library into the repository
   agent, and add a small stage or provider call for the count.
3. Emit `dependencyOf` edges from `kind=REPOSITORY` items, respecting
   `max_items` — and when `truncated` is true, emit the count but be explicit
   that the edge set is partial rather than silently shipping a subset.
4. Add the three properties to the repository schema in all three copies,
   regenerate models, extend the JSON-LD context.

**Gate the dependents path.** It is Selenium scraping of an HTML page against
documented selectors (`SELECTORS.md` in the fixture directory), so it is slow,
rate-limited and will break when GitHub changes markup. It belongs behind an
env flag, defaulted **off**, like `link_veracity` — whereas the SBOM call is a
single cached API request and can be on by default.

Until the ontology lands, both counts can ship as `gme-internal:` terms without
waiting for it.
