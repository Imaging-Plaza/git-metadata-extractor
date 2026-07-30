"""Tiny shared helpers for agent post-LLM payload normalisation."""
from __future__ import annotations

from typing import Any


def force_server_uuid(payload: dict[str, Any], uuid_value: str) -> None:
    """Overwrite the entity's `identifiers.uuid` with the server-generated
    value.

    Agents pre-generate a real UUIDv4 and inject it into the LLM input as
    `uuid`. The LLM is told to copy it through verbatim, but in practice it
    sometimes emits a placeholder (e.g. ``a1b2c3d4-...``) that fails strict
    validation. Calling this after the LLM returns guarantees the entity
    carries the server-side uuid regardless of what the LLM produced.

    Writes ONLY to `identifiers.uuid` — the strict schemas have
    `additionalProperties: false` and reject a top-level `uuid` field.

    No-op when `payload` is not a dict.
    """
    if not isinstance(payload, dict):
        return
    payload.pop("uuid", None)
    identifiers = payload.get("identifiers")
    if isinstance(identifiers, dict):
        identifiers["uuid"] = uuid_value
    else:
        payload["identifiers"] = {"uuid": uuid_value}


__all__ = ["force_server_uuid"]
