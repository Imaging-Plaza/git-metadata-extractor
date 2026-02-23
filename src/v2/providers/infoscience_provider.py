from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Awaitable, Callable

from src.v2.providers.base import InfoscienceProvider

JSONMapping = dict[str, Any]
InfoscienceSearch = Callable[[str, int], Awaitable[Any]]


def _run_async(sync_or_async: Awaitable[Any] | Any) -> Any:
    if not asyncio.iscoroutine(sync_or_async):
        return sync_or_async
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(sync_or_async)

    with ThreadPoolExecutor(max_workers=1) as executor:
        future = executor.submit(asyncio.run, sync_or_async)
        return future.result()


def _to_dict(value: Any) -> JSONMapping:
    if hasattr(value, "model_dump"):
        dumped = value.model_dump()
        if isinstance(dumped, dict):
            return dumped
    if isinstance(value, dict):
        return value
    return {}


def _ensure_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


class RealInfoscienceProvider(InfoscienceProvider):
    """Production Infoscience provider wrapping existing context search utilities."""

    def __init__(
        self,
        *,
        max_results: int = 10,
        search_authors_func: InfoscienceSearch | None = None,
        search_labs_func: InfoscienceSearch | None = None,
        search_publications_func: InfoscienceSearch | None = None,
    ) -> None:
        self._max_results = max_results
        self._search_authors_func = search_authors_func
        self._search_labs_func = search_labs_func
        self._search_publications_func = search_publications_func

    def _resolve_search_authors(self) -> InfoscienceSearch:
        if self._search_authors_func is not None:
            return self._search_authors_func
        from src.context.infoscience import search_authors  # noqa: PLC0415

        self._search_authors_func = search_authors
        return search_authors

    def _resolve_search_labs(self) -> InfoscienceSearch:
        if self._search_labs_func is not None:
            return self._search_labs_func
        from src.context.infoscience import search_labs  # noqa: PLC0415

        self._search_labs_func = search_labs
        return search_labs

    def _resolve_search_publications(self) -> InfoscienceSearch:
        if self._search_publications_func is not None:
            return self._search_publications_func
        from src.context.infoscience import search_publications  # noqa: PLC0415

        self._search_publications_func = search_publications
        return search_publications

    def search_person(self, query: str) -> list[dict[str, Any]]:
        result = _run_async(self._resolve_search_authors()(query, self._max_results))
        payload = _to_dict(result)
        authors = _ensure_list(payload.get("authors"))
        return [
            {
                "infosciencePersonIdentifier": author.get("uuid"),
                "name": author.get("name"),
                "orcid": author.get("orcid"),
                "affiliations": (
                    [author["affiliation"]]
                    if isinstance(author.get("affiliation"), str)
                    else []
                ),
                "profileUrl": author.get("profile_url"),
            }
            for author in authors
        ]

    def search_orgunit(self, query: str) -> list[dict[str, Any]]:
        result = _run_async(self._resolve_search_labs()(query, self._max_results))
        payload = _to_dict(result)
        labs = _ensure_list(payload.get("labs"))
        return [
            {
                "infoscienceOrgUnitIdentifier": lab.get("uuid"),
                "name": lab.get("name"),
                "parentOrganization": lab.get("parent_organization"),
                "url": lab.get("url"),
            }
            for lab in labs
        ]

    def search_publications(self, query: str) -> list[dict[str, Any]]:
        result = _run_async(
            self._resolve_search_publications()(query, self._max_results),
        )
        payload = _to_dict(result)
        publications = _ensure_list(payload.get("publications"))
        return [
            {
                "infosciencePublicationIdentifier": publication.get("uuid"),
                "title": publication.get("title"),
                "doi": publication.get("doi"),
                "url": publication.get("url"),
            }
            for publication in publications
        ]
