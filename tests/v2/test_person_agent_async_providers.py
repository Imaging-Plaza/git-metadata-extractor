"""Sync provider I/O must yield the person-agent loop and preserve fan-out."""
from __future__ import annotations

import asyncio
from contextvars import ContextVar
from threading import Barrier, Event
from types import SimpleNamespace

import pytest

from git_metadata_extractor.agents import PersonAgentV2, ProviderSet

ORCID = "0000-0002-1825-0097"


def _providers() -> ProviderSet:
    return ProviderSet(
        github=SimpleNamespace(
            get_user=lambda username: {
                "login": username, "name": "Alice Smith",
                "html_url": f"https://github.com/{username}",
            },
            get_profile_readme=lambda _username, **_kwargs: "",
        ),
        infoscience=SimpleNamespace(
            search_person=lambda _query: [{"name": "Alice Smith", "affiliations": []}],
        ),
        orcid=SimpleNamespace(
            search_persons=lambda _query: [{
                "orcid_id": ORCID, "given_names": "Alice", "family_names": "Smith",
            }],
            get_person_by_orcid=lambda _identifier: {
                "orcid_id": ORCID, "name": "Alice Smith", "affiliations": [],
                "employment": [], "education": [],
            },
        ),
    )


@pytest.mark.parametrize(
    ("provider_name", "method_name", "hint"),
    [
        ("github", "get_user", False),
        ("github", "get_profile_readme", False),
        ("infoscience", "search_person", False),
        ("orcid", "search_persons", False),
        ("orcid", "get_person_by_orcid", False),
        ("orcid", "get_person_by_orcid", True),
    ],
)
def test_person_provider_io_keeps_event_loop_responsive(
    provider_name, method_name, hint,
):
    """An event-loop callback can release each blocking provider operation."""
    request_marker = ContextVar("request_marker", default=None)

    async def exercise():
        providers = _providers()
        provider = getattr(providers, provider_name)
        original = getattr(provider, method_name)
        loop = asyncio.get_running_loop()
        released = Event()
        observations = []

        def blocking(*args, **kwargs):
            loop.call_soon_threadsafe(released.set)
            observations.append((released.wait(1), request_marker.get()))
            return original(*args, **kwargs)

        setattr(provider, method_name, blocking)
        request_marker.set("request-1")
        context = {"username": "alice"}
        if hint:
            context["orcid"] = ORCID
        result = await PersonAgentV2().run(context, providers)
        assert observations == [(True, "request-1")]
        assert result.data["id"] == f"https://orcid.org/{ORCID}"
        assert result.data["schema:name"] == "Alice Smith"

    asyncio.run(exercise())


def test_person_fanout_can_overlap_blocking_provider_calls():
    rendezvous = Barrier(2, timeout=2)

    async def exercise():
        providers = _providers()
        original = providers.github.get_user

        def blocking(username):
            rendezvous.wait()
            return original(username)

        providers.github.get_user = blocking
        results = await asyncio.gather(*(
            PersonAgentV2().run({"username": name}, providers)
            for name in ("alice", "alice-work")
        ))
        assert [result.data["pulse:githubUsername"] for result in results] == [
            "https://github.com/alice", "https://github.com/alice-work",
        ]

    asyncio.run(exercise())


def test_person_provider_wait_allows_request_deadline_to_cancel():
    release = Event()

    async def exercise():
        providers = _providers()
        original = providers.github.get_user

        def blocking(username):
            release.wait(1)
            return original(username)

        providers.github.get_user = blocking
        try:
            with pytest.raises(TimeoutError):
                await asyncio.wait_for(
                    PersonAgentV2().run({"username": "alice"}, providers), timeout=0.05,
                )
        finally:
            release.set()

    asyncio.run(exercise())
