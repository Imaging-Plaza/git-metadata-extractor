"""Offline semantic sanity review; findings are triage, not truth judgments.

No provider or model calls are made. SHACL results are retained from benchmark
metrics: absent/skipped validation is explicitly unknown, never a pass.
"""

from __future__ import annotations

import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Literal
from urllib.parse import urlsplit

from pydantic import BaseModel, Field

from git_metadata_extractor.providers.runtime import RAG_PROVIDERS

# Only graph-internal relationships. URLs, identifiers, enums and sameAs may
# legitimately refer to external nodes and must not be treated as dangling.
_ENTITY_REFS = frozenset(
    {
        "schema:author",
        "pulse:ownedBy",
        "pulse:owns",
        "org:member",
        "org:organization",
        "org:hasMembership",
        "org:hasUnit",
        "org:unitOf",
        "pulse:hasProfile",
        "pulse:hasOrganizationProfile",
        "pulse:hasDeposit",
        "pulse:contributionTo",
        "pulse:hasContribution",
    },
)


class Finding(BaseModel):
    code: str
    severity: Literal["review", "error"]
    message: str
    count: int = 1


class TrialQuality(BaseModel):
    profile: str
    case: str
    repetition: int = 1
    trial_path: str = ""
    source_url: str
    status: Literal["acceptable", "needs_review", "failed"] = "acceptable"
    root_name: str | None = None
    root_handle: str | None = None
    languages: list[str] = Field(default_factory=list)
    licenses: list[str] = Field(default_factory=list)
    entity_types: dict[str, int] = Field(default_factory=dict)
    organization_names: list[str] = Field(default_factory=list)
    articles: list[dict[str, str]] = Field(default_factory=list)
    ownership_edges: int = 0
    source_calls: dict[str, dict[str, int]] = Field(default_factory=dict)
    historical_source_calls: int = 0
    source_record_url_events: int = 0
    source_snapshots: list[dict[str, str]] = Field(default_factory=list)
    retrieval_observation_available: bool = False
    shacl: Literal["passed", "violations", "not_checked"] = "not_checked"
    expected_facts_checked: bool = False
    expected_facts: int = 0
    matched_facts: int = 0
    warning_patterns: dict[str, int] = Field(default_factory=dict)
    findings: list[Finding] = Field(default_factory=list)


class QualityReport(BaseModel):
    schema_version: Literal[1] = 1
    interpretation: str = "Offline triage, not proof of factual correctness. Article relevance and inferred affiliations require human review. Source snapshots describe versions, not successful retrievals."
    trials: list[TrialQuality]
    comparisons: list[dict[str, Any]] = Field(default_factory=list)


def _list(value):
    return value if isinstance(value, list) else ([] if value is None else [value])


def _text(value) -> str:
    if isinstance(value, dict):
        value = value.get("@value", value.get("@id", ""))
    return str(value) if value is not None else ""


def _texts(value) -> list[str]:
    return [text for item in _list(value) if (text := _text(item))]


def _types(node) -> list[str]:
    return _texts(node.get("@type"))


def _nodes(document):
    for node in (document or {}).get("@graph", []):
        if isinstance(node, dict):
            yield node
            yield from _nodes(node)


def _add(audit, code, message, *, error=False, count=1):
    audit.findings.append(
        Finding(
            code=code,
            severity="error" if error else "review",
            message=message,
            count=count,
        ),
    )


def _source_observations(audit, response):
    # Use runtime registrations so new source adapters receive the same treatment.
    adapters = {registration.name for registration in RAG_PROVIDERS}
    report = response.get("run_report") or {}
    incomplete = any(
        "execution evidence was" in str(message).lower()
        for message in report.get("limitations", [])
    )
    audit.retrieval_observation_available = "retrievals" in report and not incomplete
    if not audit.retrieval_observation_available:
        _add(audit, "retrieval_coverage_unknown", "Retrieval observation was unavailable or incomplete; no calls cannot be interpreted as no source use.")
    calls = defaultdict(Counter)
    adapter_returns = 0
    failures = 0
    for event in report.get("retrievals", []):
        service = event.get("service", "")
        adapter = service.partition(".")[0] in adapters
        if not (service.startswith("sources.") or adapter):
            continue
        if event.get("historical"):
            audit.historical_source_calls += 1
            continue
        status = event.get("status", "unknown")
        if adapter and status == "success":
            # The adapter swallows HTTP failures into empty lists. The wrapper's
            # success means only that it returned; the report has no hit count.
            status = "adapter_returned"
            adapter_returns += 1
        elif status not in {"success", "ok", "no_matches", "empty"}:
            failures += 1
        calls[service][status] += 1
        audit.source_record_url_events += bool(event.get("sources"))
    audit.source_calls = {service: dict(counts) for service, counts in sorted(calls.items())}
    if adapter_returns:
        _add(audit, "source_adapter_outcome_unknown", "Remote adapter returned; recorded status cannot distinguish successful empty results from swallowed service failures. Source-record URL events indicate returned evidence, not final-fact correctness.", count=adapter_returns)
    if failures:
        _add(audit, "source_retrieval_errors", "Observed sources calls did not complete successfully; inspect retrieval statuses.", count=failures)

    _source_snapshots(audit, response)


def _source_snapshots(audit, response):
    for node in _nodes(response.get("substrate")):
        if "pulse:SourceSnapshot" in _types(node):
            snapshot = {
                "platform": _text(node.get("pulse:platform")),
                "version": _text(node.get("schema:softwareVersion")),
            }
            if snapshot not in audit.source_snapshots:
                audit.source_snapshots.append(snapshot)


def _inspect_graph(audit, graph):
    ids = Counter(n.get("@id") for n in graph if n.get("@id"))
    duplicates = sum(count - 1 for count in ids.values())
    if duplicates:
        _add(
            audit,
            "duplicate_ids",
            "Canonical graph has repeated entity IDs.",
            error=True,
            count=duplicates,
        )
    audit.entity_types = dict(
        sorted(Counter(kind for node in graph for kind in _types(node)).items()),
    )
    expected_handle = urlsplit(audit.source_url).path.strip("/").removesuffix(".git")
    repositories = [n for n in graph if "schema:SoftwareSourceCode" in _types(n)]
    roots = [
        n
        for n in repositories
        if str(n.get("@id", "")).rstrip("/").casefold()
        == audit.source_url.rstrip("/").casefold()
        or expected_handle.casefold()
        in [s.casefold() for s in _texts(n.get("pulse:repositoryHandle"))]
    ]
    if not roots:
        _add(
            audit,
            "root_missing",
            "Expected repository URL or handle is absent from canonical repositories.",
            error=True,
        )
    else:
        root = roots[0]
        audit.root_name = next(iter(_texts(root.get("schema:name"))), None)
        audit.root_handle = next(iter(_texts(root.get("pulse:repositoryHandle"))), None)
        audit.languages = _texts(root.get("schema:programmingLanguage"))
        audit.licenses = _texts(root.get("schema:license"))
        if not audit.root_name:
            _add(audit, "root_name_missing", "Root repository has no name.")
        if (
            audit.root_handle
            and audit.root_handle.casefold() != expected_handle.casefold()
        ):
            _add(
                audit,
                "root_handle_mismatch",
                "Root repository handle differs from requested URL.",
                error=True,
            )
    dangling = sum(
        1
        for node in graph
        for prop in _ENTITY_REFS
        for value in _list(node.get(prop))
        if isinstance(value, dict) and value.get("@id") and value["@id"] not in ids
    )
    if dangling:
        _add(
            audit,
            "dangling_references",
            "Graph-internal entity relationships have absent targets.",
            error=True,
            count=dangling,
        )
    audit.ownership_edges = sum(
        len(_list(n.get(p))) for n in graph for p in ("pulse:owns", "pulse:ownedBy")
    )
    _inspect_articles_and_organizations(audit, graph)


def _inspect_articles_and_organizations(audit, graph):
    for node in graph:
        if "org:Organization" in _types(node):
            audit.organization_names.extend(_texts(node.get("schema:name")))
        if "schema:ScholarlyArticle" in _types(node):
            doi = next(iter(_texts(node.get("pulse:doi"))), "")
            title = next(
                iter(_texts(node.get("schema:name", node.get("schema:headline")))), "",
            )
            audit.articles.append({"title": title, "doi": doi})
            if (
                doi.upper() in {"UNKNOWN", "N/A", "TBD", "NONE", "NULL"}
                or "10.0000/" in doi
            ):
                _add(
                    audit,
                    "placeholder_doi",
                    "Article carries a placeholder DOI.",
                    error=True,
                )
            if not title:
                _add(audit, "article_title_missing", "Article has no title to review.")
    if audit.articles:
        _add(
            audit,
            "article_attribution_review",
            "Check article titles and DOIs against repository documentation; graph links or retrieval success alone do not prove relevance.",
            count=len(audit.articles),
        )


def audit_result(response: dict | None, trial: dict) -> TrialQuality:
    """Review one retained benchmark result using its observed trial metrics."""
    audit = TrialQuality(
        profile=trial.get("profile", "unknown"),
        case=trial.get("case", "unknown"),
        repetition=trial.get("repetition", 1),
        source_url=trial.get("source_url", ""),
    )
    if trial.get("status") != "completed" or response is None:
        _add(
            audit,
            "trial_failed",
            "Trial failed or no readable result was retained.",
            error=True,
        )
    if response is not None:
        _inspect_graph(audit, list(_nodes(response.get("output"))))
        _source_observations(audit, response)
        # Retain categories only: raw warnings can contain personal/provider data.
        counts = Counter()
        for warning in response.get("warnings", []):
            text = str(warning).lower()
            category = next(
                (
                    tag
                    for tag in (
                        "shacl",
                        "timeout",
                        "rate limit",
                        "validation",
                        "ror",
                        "orcid",
                        "refin",
                        "failed",
                        "error",
                    )
                    if tag in text
                ),
                "other",
            )
            counts[category] += 1
        audit.warning_patterns = dict(sorted(counts.items()))
        if counts:
            _add(
                audit,
                "pipeline_warnings",
                "Pipeline warnings require review; report retains categories only.",
                count=sum(counts.values()),
            )
    metrics = trial.get("metrics") or {}
    audit.expected_facts_checked = "expected_facts" in metrics and "matched_facts" in metrics
    audit.expected_facts = metrics.get("expected_facts", 0)
    audit.matched_facts = metrics.get("matched_facts", 0)
    if audit.matched_facts < audit.expected_facts:
        _add(
            audit,
            "expected_facts_missing",
            "Some corpus expected facts were not found.",
            count=audit.expected_facts - audit.matched_facts,
        )
    if metrics.get("shacl_checked") and metrics.get("shacl_conforms") is not None:
        audit.shacl = "passed" if metrics["shacl_conforms"] else "violations"
        if audit.shacl == "violations":
            _add(
                audit,
                "shacl_violations",
                "Canonical SHACL validation reported violations.",
                error=True,
            )
    else:
        _add(
            audit,
            "shacl_not_checked",
            "Canonical SHACL validation was skipped, unavailable or unrecorded.",
        )
    audit.status = (
        "failed"
        if any(f.severity == "error" for f in audit.findings)
        else "needs_review"
        if audit.findings
        else "acceptable"
    )
    return audit


def _comparisons(trials):
    groups = defaultdict(list)
    for trial in trials:
        groups[trial.case].append(trial)
    return [
        {
            "case": case,
            "profiles": [row.profile for row in rows],
            "root_names_differ": len({row.root_name for row in rows if row.root_name})
            > 1,
            "entity_counts": {
                (row.trial_path or f"{row.profile}/{row.repetition}/{index}"): row.entity_types for index, row in enumerate(rows)
            },
            "article_dois": {
                (row.trial_path or f"{row.profile}/{row.repetition}/{index}"): [a["doi"] for a in row.articles]
                for index, row in enumerate(rows)
            },
        }
        for case, rows in sorted(groups.items())
        if len(rows) > 1
    ]


def _safe(value):
    return (
        str(value)
        .replace("\n", " ")
        .replace("\r", " ")
        .replace("|", "\\|")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _markdown(report):
    lines = [
        "# Benchmark quality review",
        "",
        report.interpretation,
        "",
        "Acceptable means no implemented check raised a finding; it is not a factual accuracy score.",
        "",
    ]
    for row in report.trials:
        facts = f"{row.matched_facts}/{row.expected_facts}" if row.expected_facts_checked else "N/A"
        lines.extend(
            [
                f"## {_safe(row.case)} — {_safe(row.profile)} / {row.repetition}",
                "",
                f"**{row.status}** · SHACL: {row.shacl} · expected facts: {facts}",
                "",
                f"Trial artifact: {_safe(row.trial_path or 'unrecorded')}",
                f"Repository: {_safe(row.root_name or '(missing)')} ({_safe(row.root_handle or row.source_url)})",
                f"Languages: {_safe(', '.join(row.languages) or 'unreported')}; license: {_safe(', '.join(row.licenses) or 'unreported')}",
                f"Entity types: {_safe(json.dumps(row.entity_types, sort_keys=True))}",
                f"Organizations: {_safe('; '.join(row.organization_names) or 'none')}",
                f"Ownership edges: {row.ownership_edges}; articles: {len(row.articles)}",
                f"Current sources calls: {_safe(json.dumps(row.source_calls, sort_keys=True))}; historical calls: {row.historical_source_calls}; observation available: {row.retrieval_observation_available}",
                f"Events carrying source-record URLs: {row.source_record_url_events}",
                f"Source snapshots (version evidence): {_safe(json.dumps(row.source_snapshots))}",
                f"Warning categories: {_safe(json.dumps(row.warning_patterns))}",
                "",
            ],
        )
        lines.extend(
            f"- Article: {_safe(article['title'])} / {_safe(article['doi'] or 'no DOI')}"
            for article in row.articles
        )
        lines.extend(
            f"- {finding.severity}: {finding.message} (count: {finding.count})"
            for finding in row.findings
        )
        lines.append("")
    if report.comparisons:
        lines.extend(
            [
                "## Runtime comparisons",
                "",
                "Differences are review prompts; more entities do not establish better accuracy.",
                "",
            ],
        )
        for comparison in report.comparisons:
            lines.extend(
                [
                    f"### {_safe(comparison['case'])}",
                    "",
                    f"Root names differ: {comparison['root_names_differ']}",
                    "",
                    "| Profile / repetition | Entity counts | Article DOIs |",
                    "| --- | --- | --- |",
                ],
            )
            for profile, counts in comparison["entity_counts"].items():
                lines.append(
                    f"| {_safe(profile)} | {_safe(json.dumps(counts, sort_keys=True))} | {_safe(', '.join(comparison['article_dois'][profile]))} |",
                )
            lines.append("")
    return "\n".join(lines)


def review_benchmark(directory: Path) -> QualityReport:
    """Write quality.json and quality.md beside a benchmark's retained trials."""
    directory = Path(directory)
    trials = []
    for path in sorted(directory.rglob("trial.json")):
        row = json.loads(path.read_text(encoding="utf-8"))
        result_path = path.with_name("result.json")
        try:
            response = json.loads(result_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            response = None
        audited = audit_result(response, row)
        audited.trial_path = path.relative_to(directory).as_posix()
        trials.append(audited)
    if not trials:
        message = "No retained benchmark trial.json files found"
        raise ValueError(message)
    report = QualityReport(trials=trials, comparisons=_comparisons(trials))
    (directory / "quality.json").write_text(
        report.model_dump_json(indent=2) + "\n", encoding="utf-8",
    )
    (directory / "quality.md").write_text(_markdown(report), encoding="utf-8")
    return report
