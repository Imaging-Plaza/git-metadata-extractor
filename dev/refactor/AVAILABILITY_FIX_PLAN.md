# Membership routing and source availability — 2026-11-16

Follow-up to the November 13 enrichment review, authorized as items 1 and 2.
Keep both repositories and all verification artifacts local.

1. Make late membership writers register the same accepted entity with strict
   validation and its person's inverse relationship. Reuse existing bodies and
   preserve their identifiers and source evidence. Cover company ROR, company
   GitHub, bio ROR and hybrid/LLM bio paths through substrate projection.
2. Handle GitHub's explicit oversized contributor-history refusal with one
   bounded recent-commit request. Linked authors are a partial sample; counts
   remain unknown unless obtained independently. Never retry this fallback for
   authentication or quota failures.
3. Serialize Infoscience requests across the existing thread/async bridge,
   respect Retry-After within a bounded budget and propagate availability
   failures without caching empty successes. Optional enrichment consumers must
   preserve their base entities; documented 404 lookup fallback must remain.
4. Bootstrap the configured Zenodo community catalog in the sources repository,
   atomically publish the database, record discovery limits and verify actual
   authenticated HTTP/MCP queries. GME continues to access it only over HTTP.
5. Review independently, run focused regressions and the complete V2 suite,
   then run bounded live probes. Preserve historical benchmark data. Document
   measured results and upstream limitations, commit sources documentation,
   update the exact pairing lock and commit GME independently. No push.

Acceptance: no duplicate or stranded memberships in the new producer-path
regressions; no fabricated contribution totals; unavailable Infoscience is a
warning, not silent success or loss of GitHub entities; real Zenodo records
available through authenticated service routes; all relevant regression checks
pass. Live availability is reported as observed, not guaranteed by tests.
