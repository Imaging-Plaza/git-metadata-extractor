# Sources MCP implementation plan

**Request:** Add MCP to the independent sources service, preserving existing
GME tools and HTTP routes. Keep both repositories on their local refactor branches.

**Design:** FastMCP 2.13.0.2 (already present in the sources lock) plus bounded
Pydantic models. Curated named tools reuse the same source operations as HTTP;
no loopback HTTP proxy, GME imports or broad automatic exposure of management
endpoints. Mount Streamable HTTP at `/mcp` with existing API_TOKEN authentication.
The application owns one provider pool and closes it after both transports stop.
MCP responses carry source version and the unchanged source payload under `data`.
Existing GME tools remain available, with a cross-repository parity check.

**Scope ruling:** Include indexed retrieval and direct source lookups by default;
an optional scope question was offered, with indexed + direct recommended.
Repository-corpus search, graph context, ontology taxonomy and privacy utilities
remain extraction tools. Administrative reset/ingest tools are not automatically
exposed. Optional source fact helpers can be read-only MCP tools.

**Execution:** Use subagent-driven implementation for direct lookup adapters and
independent review. Continue the already isolated named refactor branches. Root
implements the shared registry, transport, validation tests and documentation.

- [x] Inventory tool names and signatures; define the shared Pydantic tool spec.
- [x] Implement source retrieval MCP tools on shared HTTP operations, authenticate
  the mounted endpoint, and preserve provider/resource lifecycles.
- [x] Add direct source lookup tools and document intentional GME-only exclusions.
- [x] Test discovery, argument bounds, parity, errors, concurrent calls and auth.
- [x] Test actual MCP over isolated Docker HTTP, run both relevant suites, document
  connection/setup, commit sources and update the parent's compatibility lock.

**Verification:** Fake/temporary stores only; no live upstream/model calls. Use
real FastMCP client discovery/calls and HTTP initialize/list/call protocol tests.
Invalid/missing credentials must fail closed; invalid arguments must not call
backends; errors must not leak credentials. Existing HTTP tests must keep passing.
No pushes or image publication.

## Verification record

- Sources: 878 non-live tests passed with network disabled and cached tokenizer.
- Paired contracts: 3 passed, including 35 preserved tool names/signatures/defaults.
- Docker: frozen-lock sources image built as 0.2.0.dev1; authenticated MCP
  discovery and a real temporary DuckDB lookup passed beside existing HTTP and
  extractor/run-report smoke checks, on an internal network.
- Safe checkout: 10 tests passed. Changed Python files pass Ruff; workflow passes
  actionlint; documentation builds with MkDocs strict mode; import closure passes.
- Independent review found two direct-provider defects: Infoscience failures
  appeared empty and first concurrent lookups could leak ROR/ORCID sessions.
  Both were fixed with regressions; reviewer reran 67 MCP tests and cleared them.
- Integration ruling: keep both local refactor branches and independent commits,
  as already requested; no merge, push, image publication or live source calls.
