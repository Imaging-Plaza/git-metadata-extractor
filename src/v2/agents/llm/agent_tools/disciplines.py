from __future__ import annotations

import logging

from pydantic_ai import Tool

from src.v2.api_models.enums import DisciplineV2

logger = logging.getLogger(__name__)


def _build_discipline_description() -> str:
    """Generate tool description with the full discipline table from DisciplineV2."""
    lines = [
        "Return the complete list of valid pulse:discipline Wikidata IRIs with human-readable names.",
        "",
        "| Wikidata ID | Discipline Name |",
        "|---|---|",
        "",
        "Use wikidata_id values from this list when assigning pulse:discipline fields.",
        "Only assign IDs present in this list.",
    ]
    return "\n".join(lines)


def _list_disciplines() -> list[dict[str, str]]:
    logger.info("tool call: list_disciplines — returning %d entries", len(DisciplineV2))
    return [
        {"wikidata_id": member.value, "name": member.name.replace("_", " ").title()}
        for member in DisciplineV2
    ]


list_disciplines_tool = Tool(
    _list_disciplines,
    name="list_disciplines",
    description=_build_discipline_description(),
)
