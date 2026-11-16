"""Offline review catches structural problems without claiming semantic truth."""

import json

from git_metadata_extractor.experiments.quality import audit_result, review_benchmark

URL = "https://github.com/acme/tool"


def response(*nodes):
    return {
        "run_report": {"retrievals": []},
        "output": {
            "@graph": [
                {
                    "@id": URL,
                    "@type": "schema:SoftwareSourceCode",
                    "schema:name": "tool",
                    "pulse:repositoryHandle": "acme/tool",
                    "pulse:platform": {"@id": "pulse:GitHub"},
                    "schema:license": {"@id": "https://spdx.org/licenses/MIT"},
                    "schema:url": {"@id": "https://example.org"},
                },
                *nodes,
            ],
        },
    }


def trial(**metrics):
    return {
        "profile": "rule_based",
        "case": "tool",
        "source_url": URL,
        "status": "completed",
        "metrics": {"shacl_checked": True, "shacl_conforms": True, **metrics},
    }


def test_external_urls_and_enum_values_are_not_dangling_entities():
    audit = audit_result(response(), trial())
    assert audit.status == "acceptable"
    assert audit.root_name == "tool"


def test_missing_root_and_duplicate_ids_fail_structural_audit():
    data = response({"@id": URL, "@type": "schema:Person"})
    assert "duplicate_ids" in {f.code for f in audit_result(data, trial()).findings}
    data["output"]["@graph"] = []
    assert audit_result(data, trial()).status == "failed"


def test_uuid_repository_root_matches_handle_but_missing_entity_edge_is_flagged():
    data = response()
    data["output"]["@graph"][0].update(
        {
            "@id": "urn:pulse:uuid",
            "schema:author": {"@id": "https://github.com/missing"},
        },
    )
    audit = audit_result(data, trial())
    assert audit.root_name == "tool"
    assert "dangling_references" in {f.code for f in audit.findings}


def test_skipped_shacl_is_not_pass_and_expected_facts_are_checked():
    audit = audit_result(
        response(), trial(shacl_checked=False, expected_facts=2, matched_facts=1),
    )
    assert audit.status == "needs_review"
    assert audit.shacl == "not_checked"
    assert {"shacl_not_checked", "expected_facts_missing"} <= {
        f.code for f in audit.findings
    }


def test_source_observations_distinguish_current_historical_and_snapshots():
    data = response()
    data["run_report"] = {
        "retrievals": [
            {"service": "sources.ror", "status": "success"},
            {"service": "sources.orcid", "status": "failed"},
            {"service": "sources.ror", "status": "success", "historical": True},
            {"service": "github", "status": "success"},
        ],
    }
    data["substrate"] = {
        "@graph": [
            {
                "@graph": [
                    {
                        "@id": "urn:snapshot",
                        "@type": "pulse:SourceSnapshot",
                        "pulse:platform": {"@id": "pulse:ROR"},
                        "schema:softwareVersion": "1.2",
                    },
                ],
            },
        ],
    }
    audit = audit_result(data, trial())
    assert audit.source_calls == {
        "sources.orcid": {"failed": 1},
        "sources.ror": {"success": 1},
    }
    assert audit.historical_source_calls == 1
    assert audit.source_snapshots == [{"platform": "pulse:ROR", "version": "1.2"}]
    assert audit.status == "needs_review"


def test_articles_need_review_even_with_plausible_doi_and_placeholder_fails():
    article = {
        "@id": "https://doi.org/10.1234/real",
        "@type": "schema:ScholarlyArticle",
        "schema:name": "Unrelated paper",
        "pulse:doi": "10.1234/real",
    }
    audit = audit_result(response(article), trial())
    assert "article_attribution_review" in {f.code for f in audit.findings}
    article["pulse:doi"] = "10.0000/placeholder"
    assert audit_result(response(article), trial()).status == "failed"


def test_review_writes_reports_and_compares_profiles_including_failed_trials(tmp_path):
    for profile in ("rule_based", "hybrid"):
        directory = tmp_path / profile / "tool" / "1"
        directory.mkdir(parents=True)
        row = trial()
        row["profile"] = profile
        (directory / "trial.json").write_text(json.dumps(row))
        data = response()
        if profile == "hybrid":
            data["output"]["@graph"][0]["schema:name"] = "Tool project"
        (directory / "result.json").write_text(json.dumps(data))
    directory = tmp_path / "llm" / "tool" / "1"
    directory.mkdir(parents=True)
    (directory / "trial.json").write_text(
        json.dumps({**trial(), "profile": "llm", "status": "failed"}),
    )
    report = review_benchmark(tmp_path)
    assert {row.profile for row in report.trials} == {"rule_based", "hybrid", "llm"}
    assert report.comparisons[0]["root_names_differ"] is True
    assert (tmp_path / "quality.json").is_file()
    markdown = (tmp_path / "quality.md").read_text()
    assert "Tool project" in markdown
    assert "failed" in markdown


def test_registered_remote_adapters_are_reported_without_claiming_transport_success():
    data = response()
    data["run_report"] = {"retrievals": [
        {"service": "ror_rag.search", "status": "success", "sources": ["https://ror.org/example"]},
        {"service": "infoscience_rag.search", "status": "success", "sources": []},
        {"service": "orcid_rag.search", "status": "failed"},
    ]}
    audit = audit_result(data, trial())
    assert audit.source_calls == {
        "ror_rag.search": {"adapter_returned": 1},
        "infoscience_rag.search": {"adapter_returned": 1},
        "orcid_rag.search": {"failed": 1},
    }
    assert audit.source_record_url_events == 1
    assert "source_adapter_outcome_unknown" in {f.code for f in audit.findings}
    assert "source_retrieval_errors" in {f.code for f in audit.findings}


def test_empty_retrieval_list_with_unavailable_evidence_is_not_observed_coverage():
    data = response()
    data["run_report"] = {"retrievals": [], "limitations": ["Execution evidence was unavailable; model and retrieval coverage is unknown."]}
    audit = audit_result(data, trial())
    assert audit.retrieval_observation_available is False
    assert audit.status == "needs_review"
    assert "retrieval_coverage_unknown" in {f.code for f in audit.findings}


def test_aggregate_review_recurses_batches_and_preserves_repeated_trial_keys(tmp_path):
    for batch in ("first", "second"):
        directory = tmp_path / "parallel" / batch / "rule_based" / "tool" / "1"
        directory.mkdir(parents=True)
        (directory / "trial.json").write_text(json.dumps(trial()))
        (directory / "result.json").write_text(json.dumps(response()))
    report = review_benchmark(tmp_path)
    expected_trials = 2
    assert len({row.trial_path for row in report.trials}) == len(report.trials) == expected_trials
    counts = report.comparisons[0]["entity_counts"]
    assert len(counts) == len(report.trials)


def test_failed_trial_report_marks_unmeasured_identity_as_na(tmp_path):
    directory = tmp_path / "hybrid" / "tool" / "1"
    directory.mkdir(parents=True)
    (directory / "trial.json").write_text(json.dumps({**trial(), "status": "failed", "metrics": {}}))
    report = review_benchmark(tmp_path)
    assert report.trials[0].expected_facts_checked is False
    markdown = (tmp_path / "quality.md").read_text()
    assert "expected facts: N/A" in markdown
    assert "expected facts: 0/0" not in markdown
