from __future__ import annotations

from typing import Any

from git_metadata_extractor.agents.llm.agent_tools.duckduckgo_search import (
    DUCKDUCKGO_INSTANT_ANSWER_URL,
    make_duckduckgo_search_tool,
)


class _FakeResponse:
    def __init__(self, payload: Any, *, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            raise ValueError(f"http {self.status_code}")

    def json(self) -> Any:
        return self._payload


def test_search_on_the_internet_returns_compact_context_rows() -> None:
    captured_calls: list[dict[str, Any]] = []

    def _fake_get(url: str, *, params: dict[str, Any], timeout: float) -> _FakeResponse:
        captured_calls.append({"url": url, "params": params, "timeout": timeout})
        return _FakeResponse(
            {
                "Heading": "Swiss Data Science Center",
                "AbstractURL": "https://www.datascience.ch/",
                "AbstractText": "Swiss Data Science Center official site.",
                "RelatedTopics": [
                    {
                        "Text": "Swiss Data Science Center - EPFL",
                        "FirstURL": "https://www.epfl.ch/labs/sdsc/",
                    },
                    {
                        "Text": "Duplicate entry",
                        "FirstURL": "https://www.datascience.ch/",
                    },
                ],
            },
        )

    tool = make_duckduckgo_search_tool(http_get=_fake_get)
    payload = tool.function("sdsc epfl", max_results=3)

    assert captured_calls
    assert captured_calls[0]["url"] == DUCKDUCKGO_INSTANT_ANSWER_URL
    assert captured_calls[0]["params"]["q"] == "sdsc epfl"
    assert payload["query"] == "sdsc epfl"
    assert payload["result_count"] == 2
    assert payload["results"][0]["url"] == "https://www.datascience.ch/"
    assert payload["results"][0]["source"] == "abstract"
    assert payload["results"][1]["url"] == "https://www.epfl.ch/labs/sdsc/"


def test_search_on_the_internet_rejects_empty_query() -> None:
    tool = make_duckduckgo_search_tool(http_get=lambda *_args, **_kwargs: _FakeResponse({}))

    payload = tool.function("   ")

    assert payload["result_count"] == 0
    assert payload["results"] == []
    assert payload["error"] == "empty_query"


def test_search_on_the_internet_returns_error_on_http_failure() -> None:
    def _failing_get(url: str, *, params: dict[str, Any], timeout: float) -> _FakeResponse:
        del url, params, timeout
        raise RuntimeError("network down")

    tool = make_duckduckgo_search_tool(http_get=_failing_get)
    payload = tool.function("open pulse")

    assert payload["query"] == "open pulse"
    assert payload["result_count"] == 0
    assert payload["results"] == []
    assert "network down" in payload["error"]

