from __future__ import annotations

import logging

from pydantic_ai import Tool

from src.v2.agents.models import generate_uuid

logger = logging.getLogger(__name__)
MAX_BATCH_SIZE = 100


def _generate_uuid_v4() -> str:
    """Generate and return one UUIDv4 string."""

    value = generate_uuid()
    logger.info("tool call: generate_uuid_v4")
    return value


def _generate_uuid_v4_batch(count: int = 1) -> list[str]:
    """Generate a batch of UUIDv4 strings."""

    count = max(count, 1)
    count = min(count, MAX_BATCH_SIZE)
    logger.info("tool call: generate_uuid_v4_batch — count=%d", count)
    return [generate_uuid() for _ in range(count)]


def generate_uuid_v4() -> str:
    """Public helper returning one UUIDv4 string."""

    return _generate_uuid_v4()


def generate_uuid_v4_batch(count: int = 1) -> list[str]:
    """Public helper returning a bounded batch of UUIDv4 strings."""

    return _generate_uuid_v4_batch(count)


generate_uuid_v4_tool = Tool(
    _generate_uuid_v4,
    name="generate_uuid_v4",
    description=(
        "Generate a random UUID version 4 string. "
        "Use when a schema requires a UUID fallback identifier."
    ),
)

generate_uuid_v4_batch_tool = Tool(
    _generate_uuid_v4_batch,
    name="generate_uuid_v4_batch",
    description=(
        "Generate multiple random UUID version 4 strings (1-100). "
        "Use when you need UUIDs for more than one entity."
    ),
)
