"""Git author/commit models used by the GitHub user & organization parsers.

Extracted from the retired v1 ``data_models/repository.py`` — only the two
classes the account parsers reference (via ``user_models`` /
``organization_models``); the rest of that module died with v1.
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, field_validator, model_validator

logger = logging.getLogger(__name__)


class Commits(BaseModel):
    total: int
    firstCommitDate: Optional[date] = None
    lastCommitDate: Optional[date] = None


class GitAuthor(BaseModel):
    id: Optional[str] = Field(
        default="",
        description="SHA-256 hash of email and name combination",
    )
    name: str
    email: Optional[str] = None
    commits: Optional[Commits] = None

    @field_validator("commits", mode="before")
    @classmethod
    def validate_commits_with_logging(cls, v):
        logger.debug(f"Validating commits field: {v} (type: {type(v)})")

        if v is None:
            logger.debug("commits is None - this is allowed")
            return None

        if isinstance(v, Commits):
            logger.debug(f"commits is already a Commits object: {v}")
            return v

        if isinstance(v, dict):
            logger.debug(f"commits is a dict, will be converted to Commits: {v}")
            return v

        logger.warning(f"commits has unexpected type: {type(v)}")
        return v

    @model_validator(mode="after")
    def compute_id(self):
        """Compute id as SHA-256 hash of email and name combination."""
        email = self.email or ""
        name = self.name or ""
        emailname = f"{email}{name}".encode()
        self.id = hashlib.sha256(emailname).hexdigest()
        return self

    def anonymize_email_local_part(self, hash_length: int = 12) -> None:
        """
        Replace the local part of the email with a SHA-256 hash while keeping the domain.

        Args:
            hash_length: Number of hexadecimal characters to keep from the hash. Defaults to 12.
        """
        if not self.email or "@" not in self.email:
            return

        local_part, domain = self.email.split("@", 1)
        if not domain:
            return

        hashed_local = hashlib.sha256(local_part.encode("utf-8")).hexdigest()
        if hash_length > 0:
            hashed_local = hashed_local[:hash_length]

        self.email = f"{hashed_local}@{domain}"
