from __future__ import annotations

import hashlib
import logging
import re

from pydantic_ai import Tool

logger = logging.getLogger(__name__)
HASH_LENGTH = 12
HASHED_LOCAL_PART_PATTERN = re.compile(r"^[0-9a-f]{12}$|^[0-9a-f]{64}$", re.IGNORECASE)


def hash_user_email(email: str) -> str:
    """Hash/anonymize an email local-part with the project canonical policy.

    Uses SHA-256(local-part)[:12] while preserving the original domain.
    If the email is invalid or already anonymized, the input is returned unchanged.
    """

    normalized = email.strip() if isinstance(email, str) else ""
    logger.info("tool call: hash_user_email")
    if "@" not in normalized:
        return normalized

    local_part, domain = normalized.split("@", maxsplit=1)
    if not local_part or not domain:
        return normalized

    if HASHED_LOCAL_PART_PATTERN.fullmatch(local_part):
        return normalized

    hashed_local = hashlib.sha256(local_part.encode("utf-8")).hexdigest()[:HASH_LENGTH]
    return f"{hashed_local}@{domain}"


hash_user_email_tool = Tool(
    hash_user_email,
    name="hash_user_email",
    description=(
        "Hash/anonymize a user email local part using the canonical policy "
        "(sha256(local-part)[:12] + original domain). "
        "Use this tool before setting schema:email."
    ),
)
