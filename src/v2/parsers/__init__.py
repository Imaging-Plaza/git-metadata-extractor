"""Format-specific parsers for repository-root supplementary metadata
files (publiccode.yml, …). Each module exposes a single ``parse_*``
entry point that takes the raw file contents and returns a typed
dict; failure modes are observable (returns ``None``) so the caller
never raises on a malformed file."""

from src.v2.parsers.publiccode import parse_publiccode

__all__ = ["parse_publiccode"]
