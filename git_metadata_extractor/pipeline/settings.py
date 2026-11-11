"""Transport-independent pipeline gates and shared output helpers."""

from __future__ import annotations

import json
import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as package_version
from typing import Any
from urllib.parse import urlparse

from rdflib import Graph as RDFGraph

SECONDS_PER_MINUTE = 60

MIN_SUPPORTED_PYTHON = (3, 10)

PACKAGE_NAME = "git-metadata-extractor"

try:
    PACKAGE_VERSION = package_version(PACKAGE_NAME)
except PackageNotFoundError:
    PACKAGE_VERSION = "unknown"

MIN_SUBRESOURCE_PATH_SEGMENTS = 3

SUBRESOURCE_SEGMENT_INDEX = 2

JSONLD_CONTEXT_FALLBACK = {
    "schema": "http://schema.org/",
    "pulse": "https://open-pulse.epfl.ch/ontology#",
    "org": "http://www.w3.org/ns/org#",
}

STAGE_PERMISSIVE_VALIDATION = "permissive_validation"

STAGE_STRICT_VALIDATION = "strict_validation"

STAGE_RECONCILIATION = "reconciliation"

STAGE_LLM_DEDUP = "llm_dedup"

STAGE_LLM_CRITIC = "llm_critic"

STAGE_SHACL_GATE = "shacl_gate"

STAGE_OUTPUT_ASSEMBLY = "output_assembly"

STAGE_JSONLD_BUILD = "jsonld_build"

STAGE_LINK_VERACITY = "link_veracity"

_TRUTHY_ENV_VALUES = {"1", "true", "t", "yes", "y", "on"}


def is_pipeline_cache_enabled() -> bool:
    """Read `V2_PIPELINE_CACHE_ENABLED` env var (default true).

    When false, `extract()` neither reads nor writes the pipeline-level cache,
    so every request runs the full pipeline. Sub-level caches (provider
    responses, agent verdicts, Selenium fetches, link-veracity) are unaffected.
    """
    raw = os.getenv("V2_PIPELINE_CACHE_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() in _TRUTHY_ENV_VALUES


def is_link_veracity_enabled() -> bool:
    """Read `V2_LINK_VERACITY_ENABLED` env var (default true).

    When false, the link-veracity LLM stage is skipped entirely. Useful for
    broad batch runs where you want fast extraction and don't need every
    URL re-verified against fetched page content. Saves ~1-3 minutes per
    repo and a chunk of LLM calls.
    """
    raw = os.getenv("V2_LINK_VERACITY_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() in _TRUTHY_ENV_VALUES


def should_apply_critic_pruning() -> bool:
    """Read `V2_APPLY_CRITIC_PRUNING` env var (default false).

    When false (the default), the LLM critic stage is skipped entirely so no
    entities get dropped from the output. Set to true to re-enable the critic
    stage's drop suggestions.
    """
    raw = os.getenv("V2_APPLY_CRITIC_PRUNING")
    if raw is None:
        return False
    return raw.strip().lower() in _TRUTHY_ENV_VALUES


def canonical_output_enabled() -> bool:
    """Read `V2_CANONICAL_OUTPUT_ENABLED` (default **true**).

    `/v2/extract` returns the graph in the **v3 canonical shapes**: platform
    profiles instead of flat handles, bare identifiers, deposits carrying
    publication dates. Measured at 119/119 SHACL-conformant against the real
    `ontology-shapes-canonical.ttl` over the 120-repo corpus — see
    `scripts/v2/canonical_conformance.py`.

    Kept as a switch rather than deleted because this is a breaking change for
    consumers: setting it `false` restores the v2-shaped output without a
    redeploy. Property renames and value-format changes are listed in §2.4 and
    §2.6 of ONTOLOGY_V3_REQUIREMENTS.md.
    """
    raw = os.getenv("V2_CANONICAL_OUTPUT_ENABLED")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "f", "no", "n", "off"}


def resolve_company_to_ror_enabled() -> bool:
    """Read `V2_RESOLVE_COMPANY_TO_ROR` env var (default true).

    When true (the default), the company → ROR resolver runs right after
    `reconcile_entities` and stamps `schema:affiliation` on persons whose
    `gme-internal:company` string resolves confidently against ROR.
    Set to `false` (or `0`/`no`/`off`) to skip the stage; useful for
    catalog backfills that should preserve the raw company strings
    unchanged.
    """
    raw = os.getenv("V2_RESOLVE_COMPANY_TO_ROR")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "f", "no", "n", "off"}


def resolve_bio_to_ror_enabled() -> bool:
    """Read `V2_RESOLVE_BIO_TO_ROR` env var (default true).

    When true (the default), the bio → ROR resolver runs right after
    `resolve_company_to_ror` and stamps `schema:affiliation` on persons
    whose `_company` was empty but whose `_bio` / `_orcid_biography` /
    `_blog` carry an institution signal that the strict ROR gate accepts.
    Set to `false` (or `0`/`no`/`off`) to skip; useful when a catalog
    backfill wants the structured-field-only behaviour.
    """
    raw = os.getenv("V2_RESOLVE_BIO_TO_ROR")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "f", "no", "n", "off"}


def resolve_bio_to_ror_llm_enabled() -> bool:
    """Read `V2_RESOLVE_BIO_TO_ROR_LLM` env var (default true).

    When true (the default) AND the request runs under the LLM / hybrid
    runtime, the LLM bio resolver runs after `resolve_bio_to_ror` and
    spends one LLM call per still-unaffiliated person to resolve
    affiliations that only surface in prose (long profile READMEs,
    bios without a clean `at X` shape). Gated by runtime as well as
    this flag — under the rule-based runtime it never runs even when
    true.
    """
    raw = os.getenv("V2_RESOLVE_BIO_TO_ROR_LLM")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "f", "no", "n", "off"}


def resolve_placeholder_orgs_to_ror_enabled() -> bool:
    """Read `V2_RESOLVE_PLACEHOLDER_ORGS_TO_ROR` env var (default true).

    When true (the default), the placeholder-resolver stage runs after
    the three Person-side resolver stages and rewrites Organization
    entities whose `idSource = "uuid"` (and which carry a `schema:name`
    breadcrumb) into ROR-anchored Orgs, patching every referring
    Membership composite in the same pass. Set to `false` to leave the
    `urn:pulse:<uuid>` Org bucket untouched.
    """
    raw = os.getenv("V2_RESOLVE_PLACEHOLDER_ORGS_TO_ROR")
    if raw is None:
        return True
    return raw.strip().lower() not in {"0", "false", "f", "no", "n", "off"}


def format_duration(seconds: float) -> str:
    """Render a duration in compact human form, e.g. '7.3s' or '4m 12s'."""
    if seconds < SECONDS_PER_MINUTE:
        return f"{seconds:.1f}s"
    minutes = int(seconds // SECONDS_PER_MINUTE)
    remainder = seconds - minutes * SECONDS_PER_MINUTE
    return f"{minutes}m {remainder:.0f}s"


def jsonld_to_graph(payload: dict[str, Any]) -> RDFGraph | None:
    try:
        graph = RDFGraph()
        graph.parse(data=json.dumps(payload), format="json-ld")
    except Exception:  # noqa: BLE001
        return None
    else:
        return graph


def extract_path_kind(source_url: str) -> str | None:
    candidate_url = source_url.strip()
    if "://" not in candidate_url:
        candidate_url = f"https://{candidate_url}"

    parsed_url = urlparse(candidate_url)
    path_segments = [segment for segment in parsed_url.path.split("/") if segment]
    if len(path_segments) >= MIN_SUBRESOURCE_PATH_SEGMENTS:
        return path_segments[SUBRESOURCE_SEGMENT_INDEX].lower()
    return None


def resolve_max_concurrent_agents() -> int:
    """Read `V2_MAX_CONCURRENT_AGENTS` env var (default 8).

    Caps how many work items per stage (person agents, contribution agents,
    link-veracity calls, etc.) run in parallel within a single /extract
    request. Higher values speed up wide-fanout repos at the cost of more
    concurrent LLM calls — keep within the LLM provider's rate limit.

    Default raised from 6 to 8 after profiling a 50-person repo
    (deeplabcut/deeplabcut): person+membership stages were spending ~12
    minutes waiting on the semaphore. The RCP/LLM stack absorbed 8
    in-flight calls without thermal throttling in that test.
    """
    raw = os.getenv("V2_MAX_CONCURRENT_AGENTS")
    if raw is None:
        return 8
    try:
        value = int(raw.strip())
    except ValueError:
        return 8
    return max(1, value)
