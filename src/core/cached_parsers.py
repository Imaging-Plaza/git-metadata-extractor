"""
Cached versions of the GitHub parsers to reduce external API calls.
"""

import logging

from .cache_manager import get_cache_manager
from .orgs_parser import GitHubOrganizationMetadata, GitHubOrganizationsParser
from .users_parser import GitHubUserMetadata, GitHubUsersParser

logger = logging.getLogger(__name__)


class CachedGitHubUsersParser(GitHubUsersParser):
    """GitHub users parser with caching support."""

    def __init__(self):
        super().__init__()
        self.cache_manager = get_cache_manager()

    def get_user_metadata_cached(
        self,
        username: str,
        force_refresh: bool = False,
    ) -> GitHubUserMetadata:
        """
        Get user metadata with caching support.

        Args:
            username: GitHub username
            force_refresh: If True, bypass cache and fetch fresh data

        Returns:
            GitHubUserMetadata object
        """

        def fetch_user_data():
            return self.get_user_metadata(username)

        # Get from cache or fetch fresh
        user_data = self.cache_manager.get_cached_or_fetch(
            api_type="github_user",
            params={"username": username},
            fetch_func=fetch_user_data,
            force_refresh=force_refresh,
        )

        return user_data


class CachedGitHubOrganizationsParser(GitHubOrganizationsParser):
    """GitHub organizations parser with caching support."""

    def __init__(self):
        super().__init__()
        self.cache_manager = get_cache_manager()

    def get_organization_metadata_cached(
        self,
        org_name: str,
        force_refresh: bool = False,
    ) -> GitHubOrganizationMetadata:
        """
        Get organization metadata with caching support.

        Args:
            org_name: GitHub organization name
            force_refresh: If True, bypass cache and fetch fresh data

        Returns:
            GitHubOrganizationMetadata object
        """

        def fetch_org_data():
            return self.get_organization_metadata(org_name)

        # Get from cache or fetch fresh
        org_data = self.cache_manager.get_cached_or_fetch(
            api_type="github_org",
            params={"org_name": org_name},
            fetch_func=fetch_org_data,
            force_refresh=force_refresh,
        )

        return org_data


# Convenience functions for backward compatibility
def parse_github_user_cached(
    username: str,
    force_refresh: bool = False,
) -> GitHubUserMetadata:
    """Parse GitHub user with caching support."""
    parser = CachedGitHubUsersParser()
    return parser.get_user_metadata_cached(username, force_refresh)


def parse_github_organization_cached(
    org_name: str,
    force_refresh: bool = False,
) -> GitHubOrganizationMetadata:
    """Parse GitHub organization with caching support."""
    parser = CachedGitHubOrganizationsParser()
    return parser.get_organization_metadata_cached(org_name, force_refresh)
