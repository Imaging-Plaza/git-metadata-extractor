"""Cold-start re-ingest driver for every v2 index.

For each known provider, this script:
  1. Optionally wipes the provider's DuckDB file, its Qdrant
     collection(s), and (by default) its ProviderCache rows by calling
     ``DELETE /v2/indices/{provider}/reset?wipe_qdrant=true&wipe_cache=true``.
  2. Loads ``seeds/<provider>.txt`` (one identifier per line, ``#``
     comments allowed, blank lines skipped, de-duplicated).
  3. Posts the parsed ids to ``POST /v2/indices/{provider}/ingest``
     with the field name matching that provider's IngestRequest
     schema in ``src/v2/api_models/contracts.py``.
  4. If ``--wait`` is set, polls ``GET /v2/indices/jobs/{job_id}``
     until the job hits ``completed`` or ``failed`` (or the timeout
     expires).

Five providers — ``zenodo_communities``, ``ror``, ``infoscience``,
``snsf``, ``epfl_graph`` — have a reset path but no HTTP ingest
endpoint (they're populated via other batch mechanisms). The script
resets them and skips ingest with an explanatory log line.

Designed to be safe to re-run: the only destructive action is the
explicit ``DELETE`` reset, which can be skipped with ``--no-reset``.

Why HTTP and not ``python -m src.index.<provider> ingest``
----------------------------------------------------------
DuckDB allows **N readers OR one writer**, and the running GME keeps a
long-lived read-WRITE handle cached on ``app.state`` for every index it
serves (``github_repos``, ``huggingface_models``, ``zenodo_records``,
…). A standalone ``python -m src.index.<provider> ingest`` is a
*separate process*: it would try to open the same DuckDB read-write and
fail with ``Could not set lock on file`` — so re-ingesting via the CLI
forces you to **stop GME for the whole ingest (hours)**.

This driver avoids that entirely. It posts to
``POST /v2/indices/{provider}/ingest``, whose job runner writes through
GME's *already-open* cached handle **in-process** — same lock, no
contention — so the API keeps serving reads (and publishes a refreshed
``.ro.duckdb`` snapshot) throughout. **Re-ingest the 17 HTTP-ingest
providers this way with zero downtime; never run their per-process CLIs
against a live GME.** The 5 reset-only catalogs above have no in-process
ingest route yet, so they remain CLI/batch-driven.
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

# ---------------------------------------------------------------------------
# Provider catalogue
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProviderSpec:
    """Static metadata for one v2 provider's ingest pipeline."""

    name: str
    seed_file: str | None
    ingest_field: str | None
    item_parser: str = "string"


def _identity_parser(line: str) -> str:
    return line


def _oamonitor_parser(line: str) -> dict[str, str]:
    """OAM monitor seeds carry `{entity, id}` pairs.

    Wire format on the seed file: ``<entity>:<id>``. We split on the
    *first* colon only — ids like OpenAlex URLs contain colons too.
    """
    head, sep, rest = line.partition(":")
    if not sep or not head or not rest:
        raise ValueError(
            f"oamonitor seed line must be '<entity>:<id>', got: {line!r}",
        )
    return {"entity": head.strip(), "id": rest.strip()}


_PARSERS = {
    "string": _identity_parser,
    "oamonitor_item": _oamonitor_parser,
}


# Order matters: this is the order operations execute by default.
# Providers with `ingest_field=None` are reset-only (no HTTP ingest
# endpoint exists for them in v2).
PROVIDERS: tuple[ProviderSpec, ...] = (
    ProviderSpec("zenodo_records",            "zenodo_records.txt",            "ids"),
    ProviderSpec("huggingface_models",        "huggingface_models.txt",        "repo_ids"),
    ProviderSpec("huggingface_datasets",      "huggingface_datasets.txt",      "repo_ids"),
    ProviderSpec("huggingface_spaces",        "huggingface_spaces.txt",        "repo_ids"),
    ProviderSpec("huggingface_users",         "huggingface_users.txt",         "slugs"),
    ProviderSpec("huggingface_organizations", "huggingface_organizations.txt", "slugs"),
    ProviderSpec("huggingface_papers",        "huggingface_papers.txt",        "arxiv_ids"),
    ProviderSpec("github_repos",              "github_repos.txt",              "repos"),
    ProviderSpec("github_users",              "github_users.txt",              "logins"),
    ProviderSpec("github_organizations",      "github_organizations.txt",      "orgs"),
    ProviderSpec("openalex",                  "openalex.txt",                  "ids"),
    ProviderSpec("orcid",                     "orcid.txt",                     "orcid_ids"),
    ProviderSpec("renkulab",                  "renkulab.txt",                  "project_ids"),
    ProviderSpec("swissubase",                "swissubase.txt",                "study_ids"),
    ProviderSpec("ethz_research_collection",  "ethz_research_collection.txt",  "uuids"),
    ProviderSpec("oamonitor",                 "oamonitor.txt",                 "items", "oamonitor_item"),
    ProviderSpec("dockerhub",                 "dockerhub.txt",                 "images"),
    ProviderSpec("zenodo_communities",        None,                            None),
    ProviderSpec("ror",                       None,                            None),
    ProviderSpec("infoscience",               None,                            None),
    ProviderSpec("snsf",                      None,                            None),
    ProviderSpec("epfl_graph",                None,                            None),
)

ALL_PROVIDER_NAMES = tuple(p.name for p in PROVIDERS)
PROVIDER_BY_NAME = {p.name: p for p in PROVIDERS}

# Terminal statuses returned by the IndexIngestJob state machine.
_TERMINAL_STATUSES = frozenset({"completed", "failed"})


# ---------------------------------------------------------------------------
# Seed parsing
# ---------------------------------------------------------------------------


def parse_seed_file(path: Path, *, item_parser: str) -> list[Any]:
    """Read a seed file into a list of typed items.

    Strips `#` comments, blanks, and dedupes while preserving order.
    Raises ValueError with the offending line number for malformed
    lines so users get a useful error instead of a 422 from the API.
    """
    parser = _PARSERS[item_parser]
    seen: set[str] = set()
    out: list[Any] = []
    for lineno, raw in enumerate(path.read_text().splitlines(), start=1):
        line = raw.split("#", 1)[0].strip()
        if not line:
            continue
        if line in seen:
            continue
        seen.add(line)
        try:
            out.append(parser(line))
        except ValueError as exc:
            raise ValueError(f"{path}:{lineno}: {exc}") from None
    return out


# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


class GmeClient:
    """Thin wrapper around the v2 ingest/reset/job endpoints.

    Keeps the bearer token + base URL in one place and surfaces
    HTTP errors with the response body included, which the default
    `httpx.HTTPStatusError` repr drops.
    """

    def __init__(self, base_url: str, token: str, *, timeout: float = 60.0) -> None:
        self._base = base_url.rstrip("/")
        self._client = httpx.Client(
            base_url=self._base,
            timeout=timeout,
            headers={"Authorization": f"Bearer {token}"},
        )

    def close(self) -> None:
        self._client.close()

    def __enter__(self) -> GmeClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def reset(self, provider: str, *, wipe_qdrant: bool, wipe_cache: bool) -> dict[str, Any]:
        r = self._client.delete(
            f"/v2/indices/{provider}/reset",
            params={
                "wipe_qdrant": str(wipe_qdrant).lower(),
                "wipe_cache": str(wipe_cache).lower(),
            },
        )
        return _unwrap(r)

    def ingest(self, provider: str, body: dict[str, Any]) -> dict[str, Any]:
        r = self._client.post(f"/v2/indices/{provider}/ingest", json=body)
        return _unwrap(r)

    def job(self, job_id: str) -> dict[str, Any]:
        r = self._client.get(f"/v2/indices/jobs/{job_id}")
        return _unwrap(r)


def _unwrap(response: httpx.Response) -> dict[str, Any]:
    if response.status_code >= 400:
        try:
            detail = response.json()
        except ValueError:
            detail = response.text
        raise RuntimeError(
            f"{response.request.method} {response.request.url.path} "
            f"→ HTTP {response.status_code}: {detail!r}",
        )
    return response.json()


# ---------------------------------------------------------------------------
# Per-provider driver
# ---------------------------------------------------------------------------


def run_provider(
    spec: ProviderSpec,
    *,
    client: GmeClient,
    seeds_dir: Path,
    do_reset: bool,
    do_ingest: bool,
    wipe_qdrant: bool,
    wipe_cache: bool,
    wait: bool,
    poll_interval: float,
    job_timeout: float,
    dry_run: bool,
) -> dict[str, Any]:
    """Run reset + ingest for a single provider, returning a summary dict."""
    summary: dict[str, Any] = {"provider": spec.name}

    # --- reset --------------------------------------------------------
    if do_reset:
        if dry_run:
            print(
                f"[{spec.name}] DRY-RUN reset: wipe_qdrant={wipe_qdrant} "
                f"wipe_cache={wipe_cache}",
            )
            summary["reset"] = {"dry_run": True}
        else:
            print(
                f"[{spec.name}] reset (wipe_qdrant={wipe_qdrant}, "
                f"wipe_cache={wipe_cache}) …",
            )
            try:
                summary["reset"] = client.reset(
                    spec.name,
                    wipe_qdrant=wipe_qdrant,
                    wipe_cache=wipe_cache,
                )
                _print_reset_result(spec.name, summary["reset"])
            except Exception as exc:  # noqa: BLE001 — record + continue
                summary["reset_error"] = str(exc)
                print(f"[{spec.name}] reset FAILED: {exc}", file=sys.stderr)
    else:
        summary["reset"] = "skipped (--no-reset)"

    # --- ingest -------------------------------------------------------
    if not do_ingest:
        summary["ingest"] = "skipped (--reset-only)"
        return summary

    if spec.ingest_field is None or spec.seed_file is None:
        print(f"[{spec.name}] reset-only provider, no HTTP ingest endpoint — skipping")
        summary["ingest"] = "skipped (reset-only provider)"
        return summary

    seed_path = seeds_dir / spec.seed_file
    if not seed_path.exists():
        print(f"[{spec.name}] no seed file at {seed_path} — skipping ingest")
        summary["ingest"] = "skipped (no seed file)"
        return summary

    items = parse_seed_file(seed_path, item_parser=spec.item_parser)
    if not items:
        print(f"[{spec.name}] seed file {seed_path.name} is empty — skipping ingest")
        summary["ingest"] = "skipped (empty seed file)"
        return summary

    body = {spec.ingest_field: items}
    if dry_run:
        print(f"[{spec.name}] DRY-RUN ingest: {len(items)} items via field {spec.ingest_field!r}")
        summary["ingest"] = {"dry_run": True, "items": len(items)}
        return summary

    print(f"[{spec.name}] ingest: posting {len(items)} items …")
    accepted = client.ingest(spec.name, body)
    job_id = accepted.get("job_id")
    print(f"[{spec.name}] accepted job_id={job_id} status_url={accepted.get('status_url')}")
    summary["ingest"] = {"job_id": job_id, "items": len(items)}

    if wait and job_id:
        final = poll_job(
            client,
            job_id,
            provider=spec.name,
            poll_interval=poll_interval,
            timeout=job_timeout,
        )
        summary["job_final"] = final

    return summary


def poll_job(
    client: GmeClient,
    job_id: str,
    *,
    provider: str,
    poll_interval: float,
    timeout: float,
) -> dict[str, Any]:
    """Block until the job hits a terminal status or `timeout` elapses."""
    deadline = time.monotonic() + timeout
    last_status: str | None = None
    while True:
        try:
            record = client.job(job_id)
        except Exception as exc:  # noqa: BLE001
            print(f"[{provider}] poll error: {exc}", file=sys.stderr)
            time.sleep(poll_interval)
            if time.monotonic() > deadline:
                return {"status": "poll_timeout", "error": str(exc)}
            continue

        s = record.get("status")
        if s != last_status:
            print(f"[{provider}] job {job_id}: {s}")
            last_status = s
        if s in _TERMINAL_STATUSES:
            return record

        if time.monotonic() > deadline:
            print(f"[{provider}] job {job_id}: poll timeout after {timeout}s", file=sys.stderr)
            return {"status": "poll_timeout", "last_seen": s}

        time.sleep(poll_interval)


def _print_reset_result(provider: str, result: dict[str, Any]) -> None:
    bytes_reclaimed = result.get("duckdb_bytes_reclaimed", 0)
    qdrant_dropped = result.get("qdrant_collections_dropped") or []
    cache_cleared = result.get("cache_cleared", False)
    print(
        f"[{provider}]   duckdb_deleted={result.get('duckdb_deleted')} "
        f"({bytes_reclaimed:,} bytes), "
        f"qdrant_dropped={qdrant_dropped}, "
        f"cache_cleared={cache_cleared}, "
        f"elapsed={result.get('elapsed_seconds', 0):.2f}s",
    )


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def _parse_args(argv: list[str] | None) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="reingest_indices",
        description="Cold-start re-ingest every v2 index from seeds/.",
    )
    p.add_argument(
        "--base-url",
        default=os.getenv("GME_API_BASE_URL", "http://localhost:8000"),
        help="gme API base URL (default: $GME_API_BASE_URL or http://localhost:8000).",
    )
    p.add_argument(
        "--token",
        default=os.getenv("API_TOKEN"),
        help="Bearer token (default: $API_TOKEN).",
    )
    p.add_argument(
        "--seeds-dir",
        type=Path,
        default=Path(__file__).resolve().parents[2] / "seeds",
        help="Directory holding <provider>.txt seed files (default: ./seeds).",
    )
    p.add_argument(
        "--providers",
        help=(
            "Comma-separated provider subset (default: all "
            f"{len(ALL_PROVIDER_NAMES)}). "
            "Valid: " + ", ".join(ALL_PROVIDER_NAMES)
        ),
    )

    # Reset / ingest gating
    p.add_argument("--no-reset", action="store_true", help="Skip the DELETE reset step.")
    p.add_argument("--reset-only", action="store_true", help="Run reset only, skip ingest.")
    p.add_argument("--no-cache-wipe", action="store_true", help="Keep the per-provider ProviderCache during reset.")
    p.add_argument("--no-qdrant-wipe", action="store_true", help="Keep Qdrant collections during reset.")

    # Polling
    p.add_argument("--wait", action="store_true", help="Poll each job until it reaches a terminal status.")
    p.add_argument("--poll-interval", type=float, default=5.0, help="Seconds between job polls (default 5).")
    p.add_argument("--job-timeout", type=float, default=1800.0, help="Per-job poll timeout in seconds (default 1800).")
    p.add_argument("--http-timeout", type=float, default=60.0, help="Per-HTTP-call timeout in seconds (default 60).")

    p.add_argument("--dry-run", action="store_true", help="Log what would happen, don't call the API.")
    p.add_argument("-y", "--yes", action="store_true", help="Skip the confirmation prompt before destructive ops.")
    return p.parse_args(argv)


def _select_providers(arg: str | None) -> list[ProviderSpec]:
    if not arg:
        return list(PROVIDERS)
    names = [n.strip() for n in arg.split(",") if n.strip()]
    out: list[ProviderSpec] = []
    for n in names:
        if n not in PROVIDER_BY_NAME:
            raise SystemExit(
                f"unknown provider {n!r}; valid: {', '.join(ALL_PROVIDER_NAMES)}",
            )
        out.append(PROVIDER_BY_NAME[n])
    return out


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    providers = _select_providers(args.providers)

    do_reset = not args.no_reset
    do_ingest = not args.reset_only
    wipe_cache = not args.no_cache_wipe
    wipe_qdrant = not args.no_qdrant_wipe

    if not args.token and not args.dry_run:
        raise SystemExit("API_TOKEN not set (use $API_TOKEN or --token).")

    print(f"base_url       = {args.base_url}")
    print(f"seeds_dir      = {args.seeds_dir}")
    print(f"providers      = {', '.join(p.name for p in providers)}")
    print(f"reset          = {do_reset}  (wipe_qdrant={wipe_qdrant}, wipe_cache={wipe_cache})")
    print(f"ingest         = {do_ingest}")
    print(f"wait           = {args.wait}")
    print(f"dry_run        = {args.dry_run}")

    if do_reset and not args.dry_run and not args.yes:
        confirm = input("\nThis will DELETE on-disk DuckDB files and Qdrant collections. Continue? [y/N] ")
        if confirm.strip().lower() not in {"y", "yes"}:
            print("aborted")
            return 1

    if args.dry_run:
        # No HTTP needed in dry-run; use a placeholder token so the
        # GmeClient constructor doesn't complain.
        client_ctx = GmeClient(args.base_url, token="dry-run", timeout=args.http_timeout)
    else:
        client_ctx = GmeClient(args.base_url, token=args.token, timeout=args.http_timeout)

    summaries: list[dict[str, Any]] = []
    with client_ctx as client:
        for spec in providers:
            print("")
            try:
                summary = run_provider(
                    spec,
                    client=client,
                    seeds_dir=args.seeds_dir,
                    do_reset=do_reset,
                    do_ingest=do_ingest,
                    wipe_qdrant=wipe_qdrant,
                    wipe_cache=wipe_cache,
                    wait=args.wait,
                    poll_interval=args.poll_interval,
                    job_timeout=args.job_timeout,
                    dry_run=args.dry_run,
                )
            except Exception as exc:  # noqa: BLE001 — collect + continue
                print(f"[{spec.name}] FATAL: {exc}", file=sys.stderr)
                summary = {"provider": spec.name, "fatal": str(exc)}
            summaries.append(summary)

    print("\n=== summary ===")
    for s in summaries:
        name = s["provider"]
        if "fatal" in s:
            print(f"  {name:32} FATAL  {s['fatal']}")
            continue
        ingest = s.get("ingest")
        if isinstance(ingest, dict):
            job_final = s.get("job_final") or {}
            ingest_str = (
                f"items={ingest.get('items')} "
                f"job={ingest.get('job_id', '-')} "
                f"final={job_final.get('status', '-')}"
            )
        else:
            ingest_str = str(ingest)
        print(f"  {name:32} ingest: {ingest_str}")

    fatal = [s for s in summaries if "fatal" in s or "reset_error" in s]
    return 1 if fatal else 0


if __name__ == "__main__":
    raise SystemExit(main())
