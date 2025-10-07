"""Parsers for GitHub users and organizations."""

from .orgs_parser import parse_github_organization
from .users_parser import parse_github_user

__all__ = [
    "parse_github_organization",
    "parse_github_user",
]
