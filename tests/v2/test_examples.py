"""Keep the runnable examples aligned with the service contracts, offline."""

import importlib
import subprocess
import sys
from pathlib import Path

import httpx
import pytest


@pytest.mark.parametrize(
    "name",
    ["caching", "user_enrichment", "organization_enrichment", "orcid_affiliations"],
)
def test_example_help_requires_no_credentials(name, monkeypatch):
    monkeypatch.delenv("API_TOKEN", raising=False)
    result = subprocess.run(  # noqa: S603 - fixed module names, no shell
        [sys.executable, "-m", f"examples.example_{name}", "--help"],
        cwd=Path(__file__).resolve().parents[2],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert "usage:" in result.stdout


def test_extraction_example_uses_v2_contract():
    example = importlib.import_module("examples._extract")
    source = "https://github.com/octocat"

    def respond(request):
        assert request.url.path == f"/v2/extract/{source}"
        assert request.url.params["agent_runtime"] == "rule_based"
        assert request.url.params["include_run_report"] == "true"
        assert request.url.params["output_format"] == "jsonld"
        return httpx.Response(
            200,
            json={
                "source_url": source,
                "detected_type": "user",
                "output_format": "jsonld",
                "output": {"@context": {}, "@graph": []},
                "stats": {
                    "entities_count": 0,
                    "triples_count": 0,
                    "run_id": "example",
                    "duration_ms": 1,
                },
                "extraction_run": {"@graph": []},
                "warnings": ["Example warning"],
            },
        )

    with httpx.Client(
        base_url="http://example.test",
        transport=httpx.MockTransport(respond),
    ) as client:
        result = example.extract(
            client,
            source,
            runtime="rule_based",
            include_run_report=True,
        )
    assert result.output.graph == []
    assert result.warnings == ["Example warning"]
    assert result.extraction_run == {"@graph": []}


def test_extraction_example_raises_on_http_failure():
    example = importlib.import_module("examples._extract")
    with httpx.Client(
        base_url="http://example.test",
        transport=httpx.MockTransport(lambda _: httpx.Response(401)),
    ) as client, pytest.raises(httpx.HTTPStatusError):
        example.extract(client, "https://github.com/octocat")


def test_cache_example_reuses_then_refreshes(tmp_path):
    example = importlib.import_module("examples.example_caching")
    assert example.demonstrate(tmp_path / "providers.db") == [1, 1, 2, 2]


def test_orcid_example_uses_provider_and_cache(tmp_path, requests_mock):
    example = importlib.import_module("examples.example_orcid_affiliations")
    orcid = "0000-0002-1126-1535"
    base = f"https://pub.orcid.org/v3.0/{orcid}"
    requests_mock.get(
        f"{base}/person",
        json={"name": {"given-names": {"value": "Example"}}},
    )
    requests_mock.get(
        f"{base}/employments",
        json={
            "affiliation-group": [
                {
                    "summaries": [
                        {
                            "employment-summary": {
                                "organization": {"name": "Example University"},
                            },
                        },
                    ],
                },
            ],
        },
    )
    requests_mock.get(f"{base}/educations", json={})
    cache_path = tmp_path / "orcid.db"
    first = example.fetch_affiliations(f"https://orcid.org/{orcid}", cache_path)
    second = example.fetch_affiliations(orcid, cache_path)
    assert first == second
    assert second["affiliations"] == ["Example University"]
    requests_per_fetch = 3  # person, employment, education
    assert requests_mock.call_count == requests_per_fetch
    example.fetch_affiliations(orcid, cache_path, refresh=True)
    assert requests_mock.call_count == 2 * requests_per_fetch
