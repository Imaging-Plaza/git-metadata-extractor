"""Concurrent taxonomy lookups use independent remote response facts."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

from git_metadata_extractor.pipeline.stages.rule_based_disciplines import (
    _fetch_category_chain_qids,
)


def test_concurrent_lookups_keep_their_category_chains():
    class Client:
        async def operation(self, operation, payload):
            await asyncio.sleep(0)
            category = payload["category_ids"][0]
            return SimpleNamespace(
                status="ok",
                data={
                    "categories": [
                        {
                            "category_id": category,
                            "wikidata_qid": "Q2",
                            "parent_id": "root",
                        },
                        {
                            "category_id": "root",
                            "wikidata_qid": None,
                            "parent_id": None,
                        },
                    ]
                },
            )

    async def run():
        return await asyncio.gather(
            *(_fetch_category_chain_qids([str(i)], Client()) for i in range(8))
        )

    for i, result in enumerate(asyncio.run(run())):
        assert result == {str(i): ("Q2", "root"), "root": (None, None)}
