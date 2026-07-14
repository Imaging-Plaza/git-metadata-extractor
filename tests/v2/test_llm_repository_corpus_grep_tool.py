from __future__ import annotations

from git_metadata_extractor.agents.llm.agent_tools.repository_corpus_grep import (
    make_repository_corpus_grep_tool,
)


def test_grep_repository_corpus_returns_markdown_snippets_with_origin_metadata() -> None:
    tool = make_repository_corpus_grep_tool(
        [
            {
                "label": "Repository README",
                "repository": "owner/repo",
                "origin": "repository_context.readme_content",
                "path": "README.md",
                "content": "Line one\nInstall with uv\nRun `just test`\nDone",
            },
        ],
    )

    result = tool.function("just test", max_matches=3, context_lines=1)

    assert "# Repository Grep Results" in result
    assert "- repository: `owner/repo`" in result
    assert "- origin: `repository_context.readme_content`" in result
    assert "- path: `README.md`" in result
    assert "```text" in result
    assert "Run `just test`" in result


def test_grep_repository_corpus_supports_regex_queries() -> None:
    tool = make_repository_corpus_grep_tool(
        [
            {
                "label": "Repository File",
                "repository": "owner/repo",
                "origin": "repository_context.repository_files",
                "path": "pyproject.toml",
                "content": "[tool.ruff]\nline-length = 100\n",
            },
        ],
    )

    result = tool.function(r"/line-length\s*=\s*\d+/", max_matches=1)

    assert "line-length = 100" in result
    assert "Match 1" in result


def test_grep_repository_corpus_reports_no_matches_with_source_inventory() -> None:
    tool = make_repository_corpus_grep_tool(
        [
            {
                "label": "Raw GIMIE JSON-LD",
                "repository": "owner/repo",
                "origin": "repository_context.gimie_jsonld",
                "path": "gimie.jsonld",
                "content": '{"@id":"https://github.com/owner/repo"}',
            },
        ],
    )

    result = tool.function("non-existent-token")

    assert "No matches for query `non-existent-token`." in result
    assert "Available sources:" in result
    assert "Raw GIMIE JSON-LD (owner/repo:gimie.jsonld)" in result
