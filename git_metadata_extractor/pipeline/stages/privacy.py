from __future__ import annotations

import hashlib
import re

HASH_LENGTH = 12
HASHED_LOCAL_PART_PATTERN = re.compile(r"^[0-9a-f]{12}$|^[0-9a-f]{64}$", re.IGNORECASE)


def anonymize_email(email: str) -> str:
    if "@" not in email:
        return email

    local_part, domain = email.split("@", maxsplit=1)
    if not local_part or not domain:
        return email

    if HASHED_LOCAL_PART_PATTERN.fullmatch(local_part):
        return email

    hashed_local = hashlib.sha256(local_part.encode("utf-8")).hexdigest()[:HASH_LENGTH]
    return f"{hashed_local}@{domain}"
