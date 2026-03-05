# Agent Operating Guide for git-metadata-extractor

## Title + Purpose
This guide defines the operating contract for autonomous and semi-autonomous coding agents working in this repository.
The goal is safe, reproducible contributions with minimal human back-and-forth.

## Project Snapshot
- Language/runtime: Python project with package code under `src/`.
- Main runtime surfaces:
  - API: `src/api.py`
  - CLI: `src/main.py`
  - Analysis orchestration: `src/analysis/`
  - Agent pipelines: `src/agents/`
  - Data models/contracts: `src/data_models/`
  - Tests: `tests/`
- Core references:
  - `README.md`
  - `.internal/RISKS.md`
  - `docs/AGENT_STRATEGY.md`
  - `.internal/v2-plan/README.md`

## V2 Progress Track
- V2 work starts in `.internal/v2-plan/phase-0-tdd-foundation/`.
- Execute tasks in dependency order from `.internal/v2-plan/README.md`.
- V2B continuation work is tracked in `.internal/v2b-plan/` and executes in dependency order from `.internal/v2b-plan/README.md`.
- Current entry task: `.internal/plan-d/PD-02-runtime-enum-and-config.md` (Plan D wave-1 runtime migration is active: `rule_based|llm` runtime split with repository-first LLM rollout and hard-fail no-fallback policy when `agent_runtime=llm`.)
- Phase 8 live-provider snapshot work is tracked separately in `.internal/phase-8/` (not part of the dependency graph in `.internal/v2-plan/README.md`).
- Canonical Infoscience IDs in v2 should resolve to `https://infoscience.epfl.ch/server/api/core/items/{uuid}` while accepting `entities/*` and `core/items/*` input forms.
- For schema promotion tasks, schemas live in **three** locations that must stay byte-identical: `src/v2/schemas/{type}/{entity}.schema.json` (source), `dev/ontology-v2-json-response/a-001/json-schema/{type}/pulse_{Entity}Shape.schema.json` (promoted), and `tests/v2/fixtures/schema/{type}/{entity}.schema.json` (test fixture). After any schema edit, copy to all three locations and run `just v2-models-generate` to regenerate Pydantic models.
- Repository identifier DOI/citation is stored as `schema:citation` (not `schema:identifier`) in both `identifiers` and `idSource`. Articles continue to use `schema:identifier` for their canonical identifier.
- Entity `@id` values use canonical dereferenceable URLs when available: `https://orcid.org/{orcid}` for persons, `https://github.com/{handle}` for GitHub-identified entities, `https://ror.org/{id}` for organizations, `https://doi.org/{doi}` for DOI-identified entities. The fallback prefix for bare identifiers (UUID, pre-reconciliation handles) is `urn:pulse:` (not `urn:git-metadata-extractor:entity:`). Composite entities (memberships, contributions) use `{person_canonical_url}_{org_or_repo_canonical_url}` as their `@id`.
- Membership entities carry an internal `_person_ref` field (set during reconciliation) that stores the canonical person ID. This field is stripped before JSON-LD output, RDF serialization, strict validation, and graph storage. It is used by crossref validation and person-membership linking. All `_`-prefixed fields are treated as internal pipeline metadata and automatically excluded from output paths.
- In v2 repository-mode extracts, GitHub traversal is direct-only (source repo + direct owner + direct contributors). Keep ORCID/Infoscience/ROR enrichment enabled for discovered person/org entities.
- In v2 agent payloads, `identifiers.uuid` must be generated with `src/v2/agents/models.py::generate_uuid()` (UUIDv4 only); avoid deterministic UUIDv5 emitters for agent outputs.
- LLM agent tools live in `src/v2/agents/llm/agent_tools/`. Tool modules expose either static `Tool` instances or provider-capturing factory functions. Agents build/select tools at `run()` time and pass them as `tools=[...]` to `V2LLMRuntime.run_json_prompt`. Current tools:
  - `list_disciplines_tool` (`disciplines.py`) — static tool, returns the full `DisciplineV2` Wikidata-ID-to-name mapping.
  - `make_orcid_person_tool(orcid_provider)` (`orcid_person.py`) — factory; returns `get_orcid_record` tool that fetches name, employment, education, and affiliations by ORCID ID.
  - `make_infoscience_search_tool(infoscience_provider)` (`infoscience_search.py`) — factory; returns `search_infoscience_person` tool that searches Infoscience for person records by name/query.
  - `make_organization_identity_search_tool(ror_provider, infoscience_provider)` (`organization_identity.py`) — factory; returns `search_organization_identity` that queries ROR and Infoscience together and emits linked cross-provider candidates.
  - `make_ror_organization_search_tool(ror_provider)` (`ror_organization.py`) — factory; returns `search_ror_organizations` for organization lookup by name.
  - `make_infoscience_orgunit_tool(infoscience_provider)` (`infoscience_orgunit.py`) — factory; returns `search_infoscience_orgunit` for organization-unit lookup.
  - `generate_uuid_v4_tool` / `generate_uuid_v4_batch_tool` (`uuid.py`) — static tools for UUIDv4 generation in single or batch mode.
  - `fetch_link_content_via_selenium_tool` (`selenium_fetch.py`) — static Selenium-backed fetch tool returning rendered page text/title/final URL.
- `LLMLinkVeracityAgentV2` (`src/v2/agents/llm/link_veracity/agent.py`) runs independent yes/no relationship checks per link and uses `fetch_link_content_via_selenium_tool` to ground verdicts in fetched page content.
- `/v2/extract` in `agent_runtime=llm` always runs two global fail-open stages:
  - `llm_dedup` before reconciliation (LLM candidate clusters + deterministic constrained merge/remap with hierarchy-aware ID resolution and composite-ID propagation).
  - `llm_critic` after reconciliation and before strict validation (LLM drop suggestions + deterministic non-root prune/cascade cleanup).
  Both stages emit warnings on failure and continue the pipeline; they never hard-fail extract requests.
- When `include_intermediates=true`, `/v2/extract` persists LLM dedup/critic debug envelopes as `llm_dedup_candidates`, `llm_dedup_resolution`, `llm_critic_decisions`, and `llm_critic_applied`.
- `LLMRepositoryAgentV2` is a **single-call agent** (one pydantic-ai `Agent` run). V1 used two sequential LLM calls: a general extraction agent plus a dedicated classifier for `repositoryType`/`discipline`. The single-call approach tends to leave `pulse:discipline` null; a separate discipline sub-agent (wave 2) would improve classification reliability.
- `LLMPersonAgentV2` is a **single-call agent** that accepts any combination of person identifiers (GitHub username, ORCID, Infoscience ID, or display name). It optionally fetches the GitHub profile when a username is available, then builds live tool closures over the provided `ORCIDProvider` and `InfoscienceProvider` (omitted if the respective provider is `None`) and delegates to `V2LLMRuntime`. Top-level `None` optional fields are stripped from the output before strict validation to satisfy SHACL absent-field requirements.
- `LLMPersonAgentV2` enforces a hard per-call timeout via `llm_call_timeout_seconds` (default `180.0`) around the runtime call (`asyncio.wait_for(...)`) and raises `LLMRuntimeError` with identifier+seconds when exceeded.
- `PipelineOrchestrator` limits fanout concurrency via `max_concurrent_agents` (default `3`). A fresh `asyncio.Semaphore` is created per `_execute_stage` call and wraps each individual agent execution. This prevents unbounded parallelism from saturating the LLM endpoint when many person/org/article agents run simultaneously. Set `max_concurrent_agents` in the orchestrator constructor to tune throughput vs. latency.
- `PipelineOrchestrator` supports prompt-context propagation for downstream LLM agents:
  - `include_upstream_stage_outputs_in_prompt` (constructor flag or runtime-context override) injects `upstream_stage_outputs_json` containing serialized accumulated stage outputs.
  - `user_prompt_appendix` (constructor value or runtime-context override) injects verbatim text into each agent prompt without parsing. Use this for pre-concatenated multi-file text blocks.
- `PipelineOrchestrator` now defaults `include_upstream_stage_outputs_in_prompt=True`; downstream LLM stages receive serialized upstream JSON context unless explicitly disabled via runtime context/constructor override.
- v2 organization identity reconciliation is ROR-first and warning-only: canonicalization enforces `pulse:ror -> pulse:infoscienceOrganizationIdentifier -> pulse:githubOrganizationHandle -> uuid`, reconciliation merges high-confidence org duplicates (ROR/Infoscience/GitHub + contextual cross-source matches), remaps org references to canonical IDs, and prefers ROR-backed orgs on lookup-token collisions.
- Cross-source org merge equivalence normalizes common spelling variants (for example `center`/`centre`) before deciding whether ROR and Infoscience candidates represent the same organization.
- `/v2/extract` persists reconciliation diagnostics as an intermediate (`agent_name="reconciliation_debug"`) when `include_intermediates=true` with merge/remap and token-collision summary fields.
- LLM org/membership prompts include acronym-disambiguation guidance: acronym-only matches are insufficient, context grounding is required, and `pulse:ror` should remain null when candidates are ambiguous.
- Reconciliation ownership semantics are direct-only: `pulse:owns` is rebuilt from canonical `repository.pulse:ownedBy` links and is not propagated from GitHub org-account units to canonical parent organizations.
- GitHub organization accounts that own repositories are still modeled as `org:Organization` nodes and linked in hierarchy (`org:unitOf`/`org:hasUnit`) where applicable; synthesized GitHub org-account unit IDs use canonical URLs (`https://github.com/<handle>`).
- Organization lookup hints (`aliases`, `acronyms`, `labels`) are reconciliation-internal only and must be stripped before strict validation/output because strict organization schemas disallow extra properties.
- `V2LLMRuntime` extracts token counts by calling `result.usage()` — pydantic-ai 1.5.0 exposes `usage` as a method, not a property. Do not access it as `result.usage` without calling it, or counts will always be `None`/`0`.
- `V2LLMRuntime` also surfaces `usage.requests` and `usage.tool_calls` in `LLMRuntimeResult` and logs them for runtime observability.

## Environment & Prerequisites
Required environment variables (from `.env.dist` and `.env.example`):
- `OPENAI_API_KEY`
- `OPENROUTER_API_KEY`
- `GITHUB_TOKEN`
- `GITLAB_TOKEN`
- `INFOSCIENCE_TOKEN`
- `MODEL`
- `PROVIDER`
- `SELENIUM_REMOTE_URL`
- `CACHE_DB_PATH`
- `MAX_SELENIUM_SESSIONS`
- `MAX_CACHE_ENTRIES`
- `GUNICORN_CMD_ARGS`
- `V2_ALLOW_SYNTHETIC_FALLBACKS` (optional; defaults `false`)

Rules:
- Never print, log, or commit secrets.
- Never modify secret-bearing files (`.env`, `.env2`, similar secret files) unless explicitly asked.
- If required variables are missing for the requested task, fail fast and report exactly which variables are missing.
- For live-provider preflight/capture tasks, validate and report env var names only; never echo token values.
- Selenium checks require `SELENIUM_REMOTE_URL` when `selenium` is part of selected providers.
- V2 does not use the v1 TTL cache system. Providers always fetch fresh data. The `force_refresh` query parameter and `V2_DISABLE_CACHE` env var have been removed from v2.
- For `/v2/extract`, synthetic fallbacks are production-disabled by default. Enable only for explicit test/dev scenarios with `V2_ALLOW_SYNTHETIC_FALLBACKS=true`.

## Canonical Commands
`justfile` is the source of truth for routine operations. Prefer `just` commands over ad-hoc shell commands when equivalent recipes exist.

- Setup:
  - `just install-dev`
  - `just setup`
- Run API:
  - `just serve-dev`
  - `just serve`
- LLM debug pipelines:
  - `just v2-run-repo-persons-and-orgs <owner/repo>` (full 7-stage LLM repo pipeline)
  - `just v2-run-repo-full-llm <owner/repo>` (7-stage pipeline plus final `--verify-links` link-veracity pass)
- Tests:
  - `just test`
  - `just test-file tests/<file>.py`
- Quality:
  - `just lint`
  - `just type-check`
  - `just check`
- CI-like local validation:
  - `just ci`
- V2 schema validation checks:
  - `python -m json.tool src/v2/schemas/strict/*.json`
  - `python -m json.tool src/v2/schemas/agent/*.json`
  - `.venv/bin/python -m pytest tests/v2/ --collect-only`
  - `.venv/bin/python -m pytest tests/v2 -m v2 --collect-only`
  - `just test-file tests/v2/test_test_infrastructure.py`
  - `just test-file tests/v2/test_promoted_strict_schemas.py`
  - `just test-file tests/v2/test_promoted_agent_schemas.py`
- Phase 8 live-provider checks:
  - `just preflight-live` (defaults to `github`, `ror`, `orcid`, `infoscience`, `logfire`, `selenium`)
  - `just capture-live`
  - `just test-live` (runs `.venv/bin/python -m pytest -m live_provider`)
  - `just test-offline`
  - `python scripts/v2/check_provider_connectivity.py --providers github ror orcid infoscience logfire` (optional: skip Selenium)

Testing command guidance:
- Prefer `just` test recipes to avoid shell-specific setup.
- When running pytest directly, prefer `.venv/bin/python -m pytest ...` instead of `pytest ...`.
- Avoid ad-hoc `PYTHONPATH=...` unless explicitly required for a non-module script workflow.

## Architecture Map For Agents
- Repository analysis flow entrypoints: `src/analysis/repositories.py`
- User and organization analysis entrypoints:
  - `src/analysis/user.py`
  - `src/analysis/organization.py`
- Agent implementations:
  - `src/agents/`
  - Atomic subpipeline: `src/agents/atomic_agents/`
- Data contracts:
  - `src/data_models/`
- Context and external lookups:
  - `src/context/`
- Cache layer:
  - `src/cache/`

## Editing Rules (Strict)
- Keep diffs minimal and scoped to the requested task.
- Preserve existing code style, project conventions, and import patterns.
- Do not rename or move public modules unless explicitly requested.
- Do not modify `.env`, `.env2`, or other secret-bearing files unless explicitly requested.
- Never run destructive git/file operations unless explicitly requested.
- If unrelated local changes exist, do not revert them; work around them and report context in the completion summary.
- Record newly discovered high-impact operational risks in `.internal/RISKS.md`.

## Task Playbooks
### Bug Fix Playbook
1. Reproduce the issue with a targeted test (or nearest equivalent validation).
2. Patch the minimal root cause.
3. Run focused tests first; run broader checks if shared paths were touched.
4. Report behavior change and residual risk.

### Feature Playbook
1. Identify API/data-model impact before coding.
2. Implement required model, pipeline, and endpoint wiring.
3. Add or adjust tests in `tests/`.
4. Validate with `just test` plus relevant lint/type checks.

### Refactor Playbook
1. Preserve behavior unless behavior change is explicitly requested.
2. Keep API contracts stable.
3. Prove parity with tests and checks.

## Testing & Validation Requirements
Minimum before completion:
- Run the nearest relevant tests.
- Run lint/type checks for touched Python modules when feasible.

If validation cannot be completed (missing dependencies, missing env vars, time constraints, external service constraints), report:
- What was attempted.
- What failed and why.
- The exact command(s) to run later.

## API/Schema Change Rules
For changes to FastAPI endpoints in `src/api.py`, include:
- Updated request/response behavior notes.
- Compatibility or migration notes.
- Test coverage for changed endpoint behavior.

For changes to models in `src/data_models/`, include:
- Impact notes on downstream usage (`src/analysis/`, `src/agents/`, API surface).
- Tests for new/changed fields and validation behavior.

## Output/Reporting Contract For Agents
Completion reports must include:
- Files changed
- Behavior change
- Commands run and key results
- Risks / follow-ups

No vague "done" messages. Reports must include verifiable evidence.

## Definition of Done
- Requested scope implemented.
- Relevant tests/checks passed, or blockers explicitly documented.
- No secret leakage.
- No unrelated mutations.
- No undocumented behavior changes.

## Test Cases & Scenarios For This Guide
1. Discoverability
- Scenario: A new agent opens the repository root.
- Expectation: `AGENTS.md` is present with quick-start commands and architecture map.

2. Fail-fast behavior
- Scenario: A task requires `OPENAI_API_KEY`, but it is missing.
- Expectation: Agent halts and reports the missing variable explicitly; no fabricated results.

3. Workflow consistency
- Scenario: Bug fix in `src/agents/organization_enrichment.py`.
- Expectation: Agent follows the bug-fix playbook and runs targeted tests first.

4. Safety guardrails
- Scenario: Dirty worktree with unrelated changes.
- Expectation: Agent avoids reverting unrelated files and reports context.

5. Reporting quality
- Scenario: Agent completes a task.
- Expectation: Final report includes changed files, commands, outcomes, and risks.

## Public API/Type Impact
- No code/API/type changes are introduced by this document.
- This file defines a repository-local agent policy contract only.
