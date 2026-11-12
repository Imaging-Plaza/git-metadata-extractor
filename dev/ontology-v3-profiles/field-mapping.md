# Field mapping — GME 3.0.0 → raw profile

Companion to [`README.md`](README.md). Left column is what GME collects today
(internal `_`-prefixed metadata, stripped before output, or an emitted
property). Right column is the raw-profile term from
`ontology-{definitions,shapes}-raw.ttl` on `feature/platform-profiles`.

Verified 2026-07-30 by inventorying `_`-prefixed fields across
`agents/`, `pipeline/stages/` and `providers/` (~120 distinct fields) against
the raw shapes' property paths.

---

## Person → `pulse:PlatformProfile`

Everything here currently sits on the Person entity or in its
`gme-internal:` block; in the raw profile it belongs on a per-platform profile
node.

| GME field | Raw profile term | Note |
|---|---|---|
| `_bio`, `_orcid_biography` | `pulse:biography` | maxCount 1 — two sources need a precedence rule |
| `_company` | `pulse:company` | explicitly "not validated against any Organization node" — matches our `_company` semantics exactly |
| `_location` | `pulse:location` | |
| `_avatar_url` | `schema:image` | |
| `_blog`, `_twitter_username`, `_orcid_researcher_urls` | `pulse:socialLink` | multi-valued, IRI — our three sources collapse cleanly |
| `_followers_count` | `pulse:followerCount` | |
| `_following_count` | `pulse:followingCount` | |
| `_public_repos` | `pulse:publicRepositoryCount` | |
| `_public_gists` | `pulse:publicGistCount` | |
| `_github_created_at` | `schema:dateCreated` | |
| `_github_updated_at` | `schema:dateModified` | |
| `_email` | `schema:email` on the profile | **ours is hashed** — see the privacy conflict in README |
| `pulse:githubUsername` (emitted) | `pulse:platformUsername` | relocation, not rename |
| `_id` (GitHub numeric id) | `pulse:platformInternalId` | |
| — (GraphQL node id) | `pulse:platformNodeId` | we fetch GraphQL for accounts; may already be available |
| `_orcid_country` | `pulse:country` | on Person |
| `_orcid_keywords` | `pulse:keyword` | on Person |
| `_orcid_external_identifiers` | `pulse:hasExternalIdentifier` → `pulse:ExternalIdentifier` | + `pulse:identifierScheme`; **Infoscience missing from the scheme enum** |
| `_github_account_type` | (decides Person vs Organization profile) | already used by the orchestrator's fan-out filters |

## Repository → `schema:SoftwareSourceCode` (raw)

| GME field | Raw profile term | Note |
|---|---|---|
| `_visibility` | `pulse:visibility` | enum: Public/Private/Internal |
| `_archived` | `pulse:archived` | boolean; `_archived_at` has no term |
| `_default_branch` | `pulse:defaultBranch` | |
| `_watchers_count`, `_subscribers_count` | `pulse:watcherCount` | GitHub's two counters, one term |
| `_open_issues_count` | `pulse:openIssueCount` | |
| `_has_discussions` | `pulse:hasDiscussions` | |
| `_has_wiki` | `pulse:hasWiki` | |
| `_has_pages` | `pulse:hasPages` | |
| `_releases` | `pulse:releaseCount` | |
| `_git_tag_count`, `_git_tags` | `pulse:gitTagCount` | count only; the tag list has no term |
| `_labels` (GitHub topics) | `pulse:tag` | |
| `_pushed_at` | `pulse:pushedDate` | distinct from `dateModified`, as we treat it |
| `_updated_at` | `schema:dateModified` | |
| `_primary_language` | `schema:programmingLanguage` | loses "primary" |
| `_publiccode_url` | `pulse:publicCodeManifest` | the `publiccode:` namespace hoist becomes unnecessary |
| `_security_url` | `pulse:securityPolicy` | |
| `_code_of_conduct_url` | `pulse:codeOfConduct` | |
| `_funding_urls` | `pulse:fundingConfig` | |
| `_citation_cff_url`, `_citation_cff` | `schema:citation` | already emitted |
| README | `pulse:readme` | also HF model/dataset cards |
| SBOM packages / scraped dependents | `pulse:dependencyCount`, `pulse:dependentCount`, `pulse:dependencyOf` | collected but never emitted — see [dependencies-and-dependents.md](dependencies-and-dependents.md) |
| — | `pulse:dependsOn` | **does not map**: range is a repository, our dependency data is package-shaped |
| `_is_template` | `pulse:isTemplate` | |
| `pulse:githubRepoStars` / `Forks` (emitted) | `pulse:repositoryStars` / `pulse:repositoryForks` | rename |
| `pulse:githubRepositoryHandle` (emitted) | `pulse:repositoryHandle` | rename |

**HuggingFace repository kinds** — raw adds a large block we do not populate
today from the extract path (`pulse:likeCount`, `pipelineTag`, `task`,
`library`, `framework`, `language`, `downloadCount`, `gated`, `modality`,
`datasetSplit`, `configuration`, `sdk`, `hardware`, `runtimeStatus`,
`fileCount`, `lfsObjectCount`, `xetObjectCount`, `externalReference`,
`relatedRepository`). The `hf_models` / `hf_datasets` / `hf_spaces` indices in
open-pulse-sources already hold most of these, so this is a wiring job from the
RAG read side rather than new collection. `pulse:externalReference` is the home
for HF base-model lineage (`lineage_huggingface`).

## Organization → `pulse:OrganizationProfile`

| GME field | Raw profile term | Note |
|---|---|---|
| `pulse:githubOrganizationHandle` (emitted) | `pulse:organizationHandle` | relocation |
| `pulse:githubOrgFollowers` (emitted) | `pulse:followerCount` | relocation |
| `_description` | `schema:description` | |
| `_location` | `pulse:location` | |
| `_html_url` | `schema:url` | |
| `pulse:ror` (emitted) | `@id` is the ROR IRI | already our convention |

## Membership / Article

| GME field | Raw profile term | Note |
|---|---|---|
| ORCID employment vs education | `pulse:membershipType` (`Employment`/`Education`/`SocietyMembership`) | we already distinguish these |
| `_infoscience_position` | `org:role` or `pulse:department` | needs a decision |
| — | `pulse:qualification` | ORCID education degree; not collected yet |
| `_keywords` | `pulse:keyword` | on Article |
| `_disciplines`, `_concepts` | `pulse:discipline` | from `concept_tagging` |
| Zenodo concept DOI | `pulse:conceptDoi` | not collected |
| Zenodo version | `pulse:version` | not collected |
| Zenodo community | `pulse:memberOfCommunity` → `pulse:Community` | `zenodo_communities` index exists |

---

## Unmapped — gaps to feed back

GME fields with **no term** in the raw or canonical profiles. Grouped by theme;
this is the source list for README section "What their side is missing".

**Packaging / distribution** (no artifact concept exists at all)
`_conda_channel`, `_maven_group_id`, `_maven_artifact_id`, `_latest_version`,
plus the npm/PyPI/NuGet/Go/crates/RubyGems designs in `dev/superpowers/specs/`.

**Containers / deployment**
`_docker_hub_url`, `_container_images`, `_compose_files`, `_compose_images`,
`_compose_image_urls`, `_compose_file_count`.

**Health / maturity signals**
`_badges`, `_badge_count`, `_has_ci`, `_test_coverage`,
`_community_health_percentage`, `_has_issue_template`,
`_has_pull_request_template`, `_has_issues`, `_has_projects`,
`_has_repository_projects`, `_has_organization_projects`, `_contributing_url`,
`_documentation_urls`, `_authors_url`.

**Registry / organization metadata**
`_ror_established`, `_ror_status`, `_ror_types`, `_ror_links`, `_acronyms`,
`_unit_code`, `_parent_acronym`, `_director_name`, `_org_type_dspace`,
`_infoscience_code`, `_infoscience_url`, `_infoscience_employment_status`,
`_is_verified`.

**Identity variants**
`_aliases`, `_original_name`, `_orcid_other_names`, `_proposed_author_names` —
`skos:altLabel` would cover these; nothing models them today.

**Repository facts**
`_size_kb` (bytes; only `fileCount` exists), `_network_count`, `_archived_at`
(timestamp for the `archived` boolean), `_profile_readme`, `_homepage`,
`_hireable`, `_disabled`, `_license_url` / `_license_name` (beyond
`schema:license`).

**Correctly internal — do not propose these**
`_person_ref`, `_embedded`, `_stub`, `_id`, `_source_index`, `_collection`,
`_dropped_affiliations`, `_reclassified_from_person`, `_score`. Pipeline
bookkeeping; they should keep being stripped.

**Belongs in the provenance profile, not raw**
`_source`, `_agent`, `_ror_match_confidence`, `_ror_match_tier`,
`_discovery_confidence`, `_discovery_reason`, `_rescue_confidence`,
`_rescue_reason`, `_org_resolver_confidence`, `_org_resolver_reason` — these map
onto `prov:wasDerivedFrom`, `pulse:observationKind` and
`pulse:observationConfidence`.
