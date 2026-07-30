# Git author identities — all names and emails from the repository

Requirement: capture **every** author name and email obtainable from the git
repository, not just the GitHub account behind it.

Verified 2026-07-30 against GME `3.0.0`.

---

## The blocker is the shape, not the data

Canonical `pulse:ContributionShape` already defines `pulse:gitAuthorName` and
`pulse:gitAuthorEmail` — but both are **`sh:maxCount 1`**, described as *"the
author name as it literally appeared in the aggregated commits"* (singular). A
Contribution is one (person, repository) pair, and **one person commits under
several (name, email) pairs**: work laptop vs personal, a `noreply` address
after enabling email privacy, a typo'd name, a changed employer address. That
plurality is the entire reason `.mailmap` exists.

With `maxCount 1` we must pick one identity and discard the rest, which is
precisely the data being asked for. So this needs an ontology change — proposed
as `pulse:GitIdentity` in [`ontology.ttl`](ontology.ttl) section 9.

## What we throw away today

**We already fetch commit author identities and drop them on the floor.**
`providers/github_provider.py` calls
`GET /repos/{full_name}/commits?author={login}` and reads only the date and the
page count (`github_provider.py:1752-1757`) — while the same payload carries
`commit.author.name`, `commit.author.email`, and the equivalent `committer`
block. Nothing else in the pipeline looks at commit-level identity.

What we *do* have is unrelated to git:

| Source | What it gives | Current treatment |
|---|---|---|
| `/contributors` | GitHub **logins** | drives person fan-out |
| GitHub profile `email` | the *profile* email, usually null | anonymised (`person_agent.py:463`) |
| `CITATION.cff` | author names + emails | parsed; `parsers/citation_cff.py` flags it PII-relevant |
| reconciliation | any person email | anonymised (`reconciliation.py:1883`) |

Anonymisation is `pipeline/stages/privacy.py::anonymize_email`:
`local@domain` → `<sha256(local)[:12]>@domain`, idempotent (it detects an
already-hashed local part). Domain survives, which is what the ROR resolvers
match on.

## How to obtain all of them

**Option A — GitHub commits API.** Walk `/repos/{o}/{r}/commits?per_page=100`.
Each commit yields author name/email/date, committer name/email/date, and the
linked GitHub login when GitHub can resolve one. No clone. Costs one request per
100 commits, so a 10k-commit repository is ~100 requests — cacheable, but it
competes with our rate-limit budget.

**Option B — git log (recommended).** Clone (shallow is not enough; full history
is needed for complete authorship) and run:

```bash
git shortlog --summary --numbered --email --all   # counts per identity
git log --use-mailmap --format='%aN|%aE|%aI'      # author, mailmap-applied
git log --format='%B' | grep -i '^Co-authored-by:' # trailer identities
```

One network operation, complete history, and `--use-mailmap` collapses identity
variants when the repository declares them. `git shortlog -sne` gives the commit
count per identity directly, which is what feeds `pulse:contributionCount`.

The gimie sidecar **already clones every repository** we extract, and gimie
parses git history for authors — so the cheapest route is likely an endpoint on
our own sidecar (`tools/gimie-api/`) that returns the identity table, rather
than a second clone in GME.

**Recommendation:** Option B via the sidecar, with Option A as the fallback when
cloning is unavailable. Cache keyed on repository + HEAD sha, so a re-extraction
of an unchanged repo costs nothing.

## Caveats that must be modelled, not silently swallowed

1. **`noreply` addresses.** `12345+login@users.noreply.github.com` appears when
   a user enables email privacy. It is pseudonymous, not a contact address —
   worth flagging as such rather than presenting it as an email.
2. **Bots.** `dependabot[bot]`, `github-actions[bot]`, `renovate[bot]`,
   `pre-commit-ci[bot]`. They are commit authors and they are not people. They
   need filtering or their own type, otherwise contributor counts inflate and
   Person entities get created for machines.
3. **Author ≠ committer.** Rebases, squash-merges and web-UI edits set a
   different committer; `GitHub <noreply@github.com>` is a common committer.
   Both are obtainable; they answer different questions.
4. **Co-authored-by trailers** carry identities that never appear in `%aE`.
   Pair-programmed and reviewed commits are exactly where collaboration data
   lives, so skipping trailers loses real co-authorship.
5. **Mailmap.** When present it is the maintainer's own statement about which
   identities are the same person — better evidence than anything our dedup can
   infer, and worth recording as such.
6. **Squashed/imported history** can attribute everything to one identity.

## Privacy position

Commit emails are personal data. They are published in the git objects, but
aggregating them into a queryable graph is a separate processing act from their
existing in a repository — which is why the current pipeline hashes them by
default. That is a data-protection decision for whoever operates the
deployment, not something to settle in code, so the design should make it
explicit rather than implicit:

- **Default stays anonymised** — `<hash>@domain`, as today.
- **Full identities are opt-in**, mirroring `?include_internal_fields=true`:
  an env flag plus a request parameter, so a deployment can refuse it outright.
- **Emit the domain unconditionally** (`pulse:gitAuthorEmailDomain`): it carries
  the affiliation signal our resolvers actually use, with no personal
  identifier.
- **Emit a stable hash unconditionally** (`pulse:gitAuthorEmailHash`): lets
  consumers join identities across records without holding addresses.

That way "all names and emails" is available where it is wanted, and the graph
we publish by default is not an email list. Whoever turns the flag on owns that
decision and should record the lawful basis for it.

## Implementation sketch

1. **Sidecar endpoint** returning the identity table per repository:
   `[{name, email, commits, first_commit, last_commit, is_bot, is_noreply,
   mailmap_applied}]`, plus trailer-derived co-author identities.
2. **Provider method** `get_repository_git_identities(full_name)` with
   provider-cache integration keyed on HEAD sha; degrade to `None` on failure
   like every other provider.
3. **Emit** one `pulse:GitIdentity` node per (name, email) pair, linked from the
   Contribution — see [`ontology.ttl`](ontology.ttl) §9. Keep
   `contributionCount` per identity so the aggregate stays derivable.
4. **Feed dedup**: identity variants sharing a mailmap entry, or a name, are
   strong evidence for `pulse:samePersonAs`. This is the same information the
   raw/provenance split wants and we currently discard.
5. **Gate**: `V2_GIT_IDENTITIES_ENABLED` (off by default) for collection;
   `?include_git_emails=true` for un-hashed output. Bots filtered by default
   with an explicit `is_bot` flag retained.
