from __future__ import annotations

import json
from pathlib import Path

import pytest

from git_metadata_extractor.observation.query_log import (
    QueryLog,
    current_agent,
    current_agent_context_var,
    current_agent_var,
    query_log_var,
    record_query,
    stamp_current_agent,
)


@pytest.fixture(autouse=True)
def _reset_query_log_contextvars() -> None:
    """ContextVars persist across tests — wipe them between every test."""
    query_log_var.set(None)
    current_agent_var.set(None)
    current_agent_context_var.set(None)


def test_record_query_no_active_log_is_noop() -> None:
    """When no QueryLog is set on the ContextVar, record_query is a silent no-op."""
    query_log_var.set(None)
    record_query(service="infoscience.search_person", query="anyone")  # must not raise


def test_record_query_groups_by_agent_and_context() -> None:
    log = QueryLog(run_id="r-1", extract_full_path="github.com/foo/bar")
    query_log_var.set(log)

    with current_agent(name="person_agent", context={"username": "cmdoret"}):
        record_query(service="infoscience.search_person", query="Cyril Matthey-Doret")
        record_query(service="orcid.get_person_by_orcid", query="0000-...")

    with current_agent(name="org_agent", context={"org_name": "EPFL"}):
        record_query(service="ror.search_organizations", query="EPFL")

    payload = log.to_dict()
    assert payload["run_id"] == "r-1"
    assert payload["extract_full_path"] == "github.com/foo/bar"
    agents = {entry["agent"]: entry for entry in payload["agents"]}
    assert set(agents) == {"person_agent", "org_agent"}
    assert agents["person_agent"]["context"] == {"username": "cmdoret"}
    assert len(agents["person_agent"]["queries"]) == 2
    assert agents["org_agent"]["context"] == {"org_name": "EPFL"}
    assert len(agents["org_agent"]["queries"]) == 1


def test_stamp_current_agent_sets_context_for_subsequent_calls() -> None:
    log = QueryLog(run_id="r-2", extract_full_path="github.com/foo/bar")
    query_log_var.set(log)
    stamp_current_agent(name="repo_agent", context={"full_name": "foo/bar"})
    record_query(service="github.get_organization", query="foo")
    payload = log.to_dict()
    assert payload["agents"][0]["agent"] == "repo_agent"
    assert payload["agents"][0]["context"] == {"full_name": "foo/bar"}
    assert payload["agents"][0]["queries"][0]["service"] == "github.get_organization"


def test_write_serialises_to_disk(tmp_path: Path) -> None:
    log = QueryLog(run_id="r-3", extract_full_path="github.com/foo/bar")
    query_log_var.set(log)
    with current_agent(name="person_agent", context={"username": "cmdoret"}):
        record_query(service="duckduckgo.search", query="cmdoret github")

    out = log.write(tmp_path)
    assert out is not None
    assert out.exists()
    payload = json.loads(out.read_text(encoding="utf-8"))
    assert payload["run_id"] == "r-3"
    assert payload["agents"][0]["queries"][0]["query"] == "cmdoret github"


def test_unknown_agent_default_when_no_stamp() -> None:
    log = QueryLog(run_id="r-4", extract_full_path="github.com/foo/bar")
    query_log_var.set(log)
    # Don't stamp — record_query must still work and bucket under "unknown".
    record_query(service="ror.search_organizations", query="EPFL")
    payload = log.to_dict()
    assert payload["agents"][0]["agent"] == "unknown"
    assert payload["agents"][0]["queries"][0]["query"] == "EPFL"
