"""Requested project citations survive publication-provider outages.

Fixtures contain only public citation excerpts retained by the 2026-11-13
benchmark; unrelated auxiliary files and personal provider records are omitted.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from git_metadata_extractor.agents import ProviderSet, RepositoryAgentV2
from git_metadata_extractor.agents.rule_based.article_agent import ArticleAgentV2
from git_metadata_extractor.parsers.project_citations import project_citations
from git_metadata_extractor.pipeline.stages.canonical_projection import (
    project_canonical,
)
from git_metadata_extractor.pipeline.stages.context_gather import (
    _optional_repository_context,
    gather_context,
)
from git_metadata_extractor.pipeline.stages.jsonld_build import build_jsonld_output
from git_metadata_extractor.pipeline.stages.output_assembly import assemble_output
from git_metadata_extractor.pipeline.stages.raw_projection import project_raw
from git_metadata_extractor.pipeline.stages.reconciliation import reconcile_entities
from git_metadata_extractor.providers.detection.models import (
    GitHubURLClassification,
    GitHubURLType,
)
from git_metadata_extractor.providers.mock_github import MockGitHubProvider
from git_metadata_extractor.providers.mock_infoscience import MockInfoscienceProvider
from git_metadata_extractor.schema import load_jsonld_context
from git_metadata_extractor.validation.schema_validation import StrictSchemaValidator

FIXTURES = Path(__file__).parent / "fixtures" / "project_citations"
CASES = [
    ("ethz-adrl--control-toolbox", "https://arxiv.org/abs/1801.04290"),
    ("martibosch--lausanne-heat-islands", "https://doi.org/10.5194/gmd-14-3521-2021"),
    ("scipy--scipy", "https://doi.org/10.1038/s41592-019-0686-2"),
    ("astropy--astropy", "https://doi.org/10.3847/1538-4357/ac7c74"),
    ("cbfinn--maml", "https://arxiv.org/abs/1703.03400"),
    ("antsx--ants", "https://doi.org/10.1038/s41598-021-87564-6"),
    ("cheng-zhao--fcfc", "https://doi.org/10.1051/0004-6361/202346015"),
]


def _gather_repository(context, context_path):
    class FixtureGitHub(MockGitHubProvider):
        def get_repository(self, full_name):
            del full_name
            return dict(context["metadata"])

        def get_contributors(self, full_name):
            del full_name
            return list(context["contributors"])

        def get_languages(self, full_name):
            del full_name
            return dict(context["languages"])

        def get_repository_readme(self, full_name):
            del full_name
            return context.get("readme_content", "")

        def get_repository_aux_files(self, full_name):
            del full_name
            return context.get("aux_files", {})

    full_name = context["full_name"]
    providers = ProviderSet(github=FixtureGitHub())
    if context_path == "optional":
        gathered = _optional_repository_context(full_name=full_name, providers=providers, warnings=[])
    else:
        owner, name = full_name.split("/")
        url_info = GitHubURLClassification(
            normalized_url=f"https://github.com/{full_name}",
            detected_type=GitHubURLType.REPOSITORY, owner=owner, repo=name,
        )
        gathered = asyncio.run(gather_context("repository", url_info, providers)).context["repository"]
    assert "<a href=" not in gathered["readme_content"]
    return gathered


def _repository(context, context_path="direct"):
    full_name = context["full_name"]
    owner, name = full_name.split("/")
    context = {
        **context,
        "metadata": {
            "full_name": full_name,
            "name": name,
            "owner": {"login": owner, "type": "User"},
            "created_at": "2020-01-01",
            "license": {"spdx_id": "MIT"},
        },
        "languages": {"Python": 1},
        "contributors": [{"login": owner}],
    }
    if context_path != "direct":
        context = _gather_repository(context, context_path)
    return asyncio.run(
        RepositoryAgentV2().run(
            {"full_name": full_name, "repository_context": context},
            ProviderSet(github=MockGitHubProvider()),
        ),
    ).data


@pytest.mark.parametrize(("fixture", "expected"), CASES)
@pytest.mark.parametrize("context_path", ["direct", "gathered", "optional"])
def test_requested_citation_survives_without_infoscience_in_both_layers(
    fixture, expected, context_path,
):
    context = json.loads((FIXTURES / f"{fixture}.json").read_text())
    repo = _repository(context, context_path)
    article_result = asyncio.run(
        ArticleAgentV2().run(
            {"full_name": context["full_name"], "known_repositories": [repo]},
            ProviderSet(github=MockGitHubProvider()),
        ),
    )
    assert article_result.stats["articles"] == []  # no fabricated author/date/DOI
    assert article_result.stats["queries"][0] == expected.removeprefix("https://doi.org/").removeprefix("https://arxiv.org/abs/")
    owner = context["full_name"].split("/")[0]
    person = {
        "id": f"https://github.com/{owner}",
        "type": "schema:Person",
        "pulse:githubUsername": f"https://github.com/{owner}",
        "schema:name": owner,
        "identifiers": {"pulse:githubUsername": f"https://github.com/{owner}"},
    }
    reconciled = reconcile_entities({"repositories": [repo], "persons": [person]})
    repo = reconciled.entities["repositories"][0]
    strict = StrictSchemaValidator().validate_batch([("repository", repo)])
    assert not strict.invalid_entities, strict.warnings
    assembled = assemble_output(reconciled, strict)
    intermediate = build_jsonld_output(
        assembled=assembled, jsonld_context=load_jsonld_context(),
    )
    canonical = project_canonical(intermediate["@graph"])
    raw = project_raw(intermediate["@graph"], run_id="citation-test")
    canonical_repo = next(n for n in canonical["@graph"] if n["@id"] == repo["id"])
    raw_repo = next(n for n in raw["@graph"] if n["@id"] == repo["id"])
    for node in (canonical_repo, raw_repo):
        assert node.get("schema:citation") == [{"@id": expected}]
    assert "_project_citations" not in json.dumps(intermediate)


@pytest.mark.parametrize("context_path", ["direct", "gathered", "optional"])
def test_explicit_citation_queries_precede_repository_name_search(context_path):
    context = json.loads((FIXTURES / "scipy--scipy.json").read_text())
    repo = _repository(context, context_path)
    queries = ArticleAgentV2().build_query_blend(
        {
            "detected_type": "repository",
            "full_name": "scipy/scipy",
            "known_repositories": [repo],
        },
    )
    assert queries[0] == "10.1038/s41592-019-0686-2"


def test_arxiv_lookup_uses_bare_identifier_but_preserves_citation_iri():
    context = json.loads((FIXTURES / "ethz-adrl--control-toolbox.json").read_text())
    repo = _repository(context, "gathered")
    queries = ArticleAgentV2().build_query_blend({
        "detected_type": "repository", "full_name": context["full_name"],
        "known_repositories": [repo],
    })
    assert queries[0] == "1801.04290"
    assert "https://arxiv.org/abs/1801.04290" not in queries
    assert repo["_project_citations"][0]["iri"] == "https://arxiv.org/abs/1801.04290"


@pytest.mark.parametrize(
    "context",
    [
        {
            "readme_content": "## References\nRelated work https://doi.org/10.1234/incidental",
        },
        {
            "readme_content": "Our software is archived at https://doi.org/10.5281/zenodo.1234",
        },
        {
            "aux_files": {
                "CITATION.cff": "type: software\ntitle: Tool\ndoi: 10.5281/zenodo.1234\n",
            },
        },
        {
            "aux_files": {
                "CITATION.bib": "@software{tool, title={Tool}, doi={10.5281/zenodo.1234}}",
            },
        },
    ],
)
def test_incidental_references_and_software_archives_are_not_project_papers(context):
    repo = _repository({"full_name": "owner/tool", **context})
    assert not repo.get("_project_citations")


def test_comment_bibtex_and_cff_references_are_not_requested_papers():
    citations = project_citations(
        "",
        {
            "CITATION.bib": "% @article{example, title={Example}, doi={10.1234/not-requested}}",
            "CITATION.cff": "title: Tool\nreferences:\n- type: article\n  doi: 10.1234/related\n",
        },
    )
    assert citations == []


def test_bibtex_nested_titles_quoted_doi_and_duplicate_readme_are_one_citation():
    citations = project_citations(
        "**Citation**: https://doi.org/10.1234/paper",
        {
            "CITATION.bib": '@article{paper, title={{A {Nested} Title}}, doi="10.1234/paper"}',
        },
    )
    assert citations == [
        {
            "iri": "https://doi.org/10.1234/paper",
            "title": "A Nested Title",
            "source": "CITATION.bib",
        },
    ]


def test_software_bibtex_in_citation_section_is_not_a_paper():
    assert (
        project_citations(
            "## Citation\n\n@software{archive, title={Tool}, doi={10.1234/software}}",
            {},
        )
        == []
    )


@pytest.mark.parametrize("reference", [
    "10.1234/example(2020)",
    "[paper](https://doi.org/10.1234/example(2020))",
    "(https://doi.org/10.1234/example(2020)).",
])
def test_balanced_doi_parentheses_survive_prose_punctuation(reference):
    citations = project_citations(f"**Citation**: {reference}", {})
    assert [citation["iri"] for citation in citations] == ["https://doi.org/10.1234/example(2020)"]


@pytest.mark.parametrize("comment", [
    "@comment{ @article{example, title={Example}, doi={10.1234/not-requested}} }",
    "@COMMENT{ nested {braces} and @article{example, title={Example}, doi={10.1234/not-requested}} }",
])
def test_bibtex_comment_blocks_do_not_promote_nested_examples(comment):
    paper = "@article{paper, title={Requested}, doi={10.1234/requested}}"
    citations = project_citations("", {"CITATION.bib": comment + "\n" + paper})
    assert [citation["iri"] for citation in citations] == ["https://doi.org/10.1234/requested"]
    assert project_citations("## Citation\n\n" + comment, {}) == []


@pytest.mark.parametrize("preferred", ["not-a-mapping", "[]", "true", "42"])
def test_malformed_cff_preferred_citation_is_ignored(preferred):
    content = f"title: Tool\npreferred-citation: {preferred}\n"
    assert project_citations("", {"CITATION.cff": content}) == []


def test_title_match_does_not_take_previous_papers_doi_in_same_paragraph():
    readme = (
        "## Citation\n\n@article{project, title={Requested project paper}}\n\n"
        "## References\n\n"
        "Other paper https://doi.org/10.1234/other; "
        "Requested project paper https://doi.org/10.1234/requested"
    )
    assert project_citations(readme, {}) == []


def test_title_match_prefers_its_own_line_over_another_papers_identifier():
    readme = (
        "## Citation\n\n@article{project, title={Requested project paper}}\n\n"
        "## References\n\n"
        "Other paper https://doi.org/10.1234/other\n"
        "Requested project paper https://doi.org/10.1234/requested"
    )
    citations = project_citations(readme, {})
    assert [citation["iri"] for citation in citations] == ["https://doi.org/10.1234/requested"]


@pytest.mark.parametrize("kind", ["article", "misc"])
def test_explicit_bibtex_arxiv_eprint_is_preserved(kind):
    citation = f"@{kind}" + "{paper, title={Requested paper}, archivePrefix={arXiv}, eprint={1703.03400}}"
    citations = project_citations("", {"CITATION.bib": citation})
    assert [item["iri"] for item in citations] == ["https://arxiv.org/abs/1703.03400"]


def test_article_search_failure_leaves_explicit_reference_available_for_assembly():
    class UnavailableInfoscience(MockInfoscienceProvider):
        def search_publications(self, query):
            del query
            message = "publication service unavailable"
            raise RuntimeError(message)

    context = json.loads((FIXTURES / "scipy--scipy.json").read_text())
    repo = _repository(context)
    result = asyncio.run(
        ArticleAgentV2().run(
            {
                "detected_type": "repository",
                "full_name": "scipy/scipy",
                "known_repositories": [repo],
            },
            ProviderSet(
                github=MockGitHubProvider(), infoscience=UnavailableInfoscience(),
            ),
        ),
    )
    assert result.stats["articles"] == []
    assert any(
        "publication service unavailable" in warning for warning in result.warnings
    )
    assert (
        repo["_project_citations"][0]["iri"]
        == "https://doi.org/10.1038/s41592-019-0686-2"
    )
