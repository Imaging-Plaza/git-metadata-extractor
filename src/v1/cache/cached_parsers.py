"""
Cached versions of the GitHub parsers to reduce external API calls.
"""

import logging

from ..parsers.orgs_parser import GitHubOrganizationMetadata, GitHubOrganizationsParser
from ..parsers.users_parser import GitHubUserMetadata, GitHubUsersParser
from .cache_manager import get_cache_manager

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
        include_repositories: bool = True,
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
            return self.get_user_metadata(
                username,
                include_repositories=include_repositories,
            )

        # Get from cache or fetch fresh
        user_data = self.cache_manager.get_cached_or_fetch(
            api_type="github_user",
            params={
                "username": username,
                "include_repositories": include_repositories,
            },
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
        include_repositories: bool = True,
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
            return self.get_organization_metadata(
                org_name,
                include_repositories=include_repositories,
            )

        # Get from cache or fetch fresh
        org_data = self.cache_manager.get_cached_or_fetch(
            api_type="github_org",
            params={
                "org_name": org_name,
                "include_repositories": include_repositories,
            },
            fetch_func=fetch_org_data,
            force_refresh=force_refresh,
        )

        return org_data


# Convenience functions for backward compatibility
def parse_github_user_cached(
    username: str,
    force_refresh: bool = False,
    include_repositories: bool = True,
) -> GitHubUserMetadata:
    """Parse GitHub user with caching support."""
    parser = CachedGitHubUsersParser()
    return parser.get_user_metadata_cached(
        username,
        force_refresh=force_refresh,
        include_repositories=include_repositories,
    )


def parse_github_organization_cached(
    org_name: str,
    force_refresh: bool = False,
    include_repositories: bool = True,
) -> GitHubOrganizationMetadata:
    """Parse GitHub organization with caching support."""
    parser = CachedGitHubOrganizationsParser()
    return parser.get_organization_metadata_cached(
        org_name,
        force_refresh=force_refresh,
        include_repositories=include_repositories,
    )
