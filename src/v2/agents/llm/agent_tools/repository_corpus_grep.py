from __future__ import annotations

import logging
import re
from typing import Any

from pydantic_ai import Tool

logger = logging.getLogger(__name__)
MAX_MATCHES = 20
MAX_CONTEXT_LINES = 10
MAX_LINE_LENGTH = 320


def _to_non_empty_string(value: Any) -> str | None:
    if isinstance(value, str):
        candidate = value.strip()
        if candidate:
            return candidate
    return None


def _normalized_int(value: Any, *, default: int, lower: int, upper: int) -> int:
    if isinstance(value, int):
        return max(lower, min(upper, value))
    return default


def _compile_query_pattern(query: str) -> re.Pattern[str]:
    # Support optional `/regex/` syntax while keeping substring search as default.
    if len(query) >= 3 and query.startswith("/") and query.endswith("/"):
        return re.compile(query[1:-1], re.IGNORECASE)
    return re.compile(re.escape(query), re.IGNORECASE)


def _format_snippet(lines: list[str], *, center_line: int, context_lines: int) -> str:
    start = max(1, center_line - context_lines)
    end = min(len(lines), center_line + context_lines)
    snippet_rows: list[str] = []
    for line_number in range(start, end + 1):
        text = lines[line_number - 1]
        if len(text) > MAX_LINE_LENGTH:
            text = f"{text[: MAX_LINE_LENGTH - 3]}..."
        marker = ">" if line_number == center_line else " "
        snippet_rows.append(f"{marker}{line_number:5d} | {text}")
    return "\n".join(snippet_rows)


def make_repository_corpus_grep_tool(corpus_documents: list[dict[str, Any]]) -> Tool:
    """Create a grep-like tool over an in-memory repository document corpus."""

    def grep_repository_corpus(
        query: str,
        max_matches: int = 8,
        context_lines: int = 3,
    ) -> str:
        """Search repository corpus documents and return markdown snippets.

        Args:
            query: Search term (case-insensitive substring) or `/regex/`.
            max_matches: Max snippets to return (1..20).
            context_lines: Number of surrounding lines to include (1..10).
        """

        normalized_query = query.strip() if isinstance(query, str) else ""
        logger.info(
            "tool call: grep_repository_corpus — query=%r max_matches=%r context_lines=%r docs=%d",
            normalized_query,
            max_matches,
            context_lines,
            len(corpus_documents),
        )
        if not normalized_query:
            return (
                "No query provided.\n\n"
                "Provide a non-empty string or `/regex/` pattern."
            )

        limited_matches = _normalized_int(
            max_matches,
            default=8,
            lower=1,
            upper=MAX_MATCHES,
        )
        limited_context = _normalized_int(
            context_lines,
            default=3,
            lower=1,
            upper=MAX_CONTEXT_LINES,
        )
        try:
            pattern = _compile_query_pattern(normalized_query)
        except re.error as exc:
            return f"Invalid regex query: {exc}"

        rows: list[str] = []
        match_count = 0
        for document in corpus_documents:
            content = _to_non_empty_string(document.get("content"))
            if content is None:
                continue
            lines = content.splitlines()
            if not lines:
                continue

            for line_number, line in enumerate(lines, start=1):
                if not pattern.search(line):
                    continue
                match_count += 1
                source_label = _to_non_empty_string(document.get("label")) or "unknown"
                repository = _to_non_empty_string(document.get("repository")) or "unknown"
                origin = _to_non_empty_string(document.get("origin")) or "unknown"
                path = _to_non_empty_string(document.get("path")) or "<inline>"
                snippet = _format_snippet(
                    lines,
                    center_line=line_number,
                    context_lines=limited_context,
                )
                rows.append(f"### Match {match_count}")
                rows.append(f"- repository: `{repository}`")
                rows.append(f"- source: `{source_label}`")
                rows.append(f"- origin: `{origin}`")
                rows.append(f"- path: `{path}`")
                rows.append(f"- line: `{line_number}`")
                rows.append("```text")
                rows.append(snippet)
                rows.append("```")
                rows.append("")

                if match_count >= limited_matches:
                    break
            if match_count >= limited_matches:
                break

        if not rows:
            available_sources = []
            for document in corpus_documents:
                label = _to_non_empty_string(document.get("label"))
                repository = _to_non_empty_string(document.get("repository"))
                path = _to_non_empty_string(document.get("path"))
                if not label:
                    continue
                descriptor = f"{label} ({repository or 'unknown'}:{path or '<inline>'})"
                if descriptor not in available_sources:
                    available_sources.append(descriptor)

            sources_text = "\n".join(f"- {item}" for item in available_sources[:20])
            if not sources_text:
                sources_text = "- <no searchable sources>"
            return (
                f"No matches for query `{normalized_query}`.\n\n"
                "Available sources:\n"
                f"{sources_text}"
            )

        header = [
            "# Repository Grep Results",
            f"- query: `{normalized_query}`",
            f"- matches_returned: `{match_count}`",
            "",
        ]
        return "\n".join(header + rows).strip()

    return Tool(
        grep_repository_corpus,
        name="grep_repository_corpus",
        description=(
            "Search raw repository corpus documents (README, GIMIE JSON-LD, and "
            "repository files when available). Returns markdown snippets with "
            "origin metadata and line-numbered code blocks."
        ),
    )
