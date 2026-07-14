"""
Organizations Parser
"""

import base64
import json
import os
from typing import Any, Dict, List, Optional

import requests
from dotenv import load_dotenv

from src.utils.github_token_pool import github_auth_headers

from .organization_models import GitHubOrganizationMetadata

load_dotenv()


class GitHubOrganizationsParser:
    """Parser for GitHub organization metadata using REST and GraphQL APIs"""

    def __init__(self):
        self.rest_base_url = "https://api.github.com"
        self.graphql_url = "https://api.github.com/graphql"
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "GitHubOrganizationsParser/1.0",
        }

    def _auth_headers(self, extra: dict | None = None) -> dict:
        """Per-request headers carrying a freshly rotated PAT.

        Rotating per call (not per init) is what lets a multi-PAT
        deployment actually spread GraphQL load across every token —
        see issue #56.
        """
        merged = dict(self.headers)
        if extra:
            merged.update(extra)
        return github_auth_headers(extra=merged)

    def get_organization_metadata(
        self,
        org_name: str,
        include_repositories: bool = True,
    ) -> GitHubOrganizationMetadata:
        """
        Retrieve comprehensive organization metadata from GitHub

        Args:
            org_name: GitHub organization name
            include_repositories: Include repository list from /orgs/{org}/repos

        Returns:
            GitHubOrganizationMetadata object with all available organization information

        Raises:
            requests.RequestException: If API calls fail
            ValueError: If organization not found
        """
        # Get basic organization data from REST API
        rest_data = self._get_rest_organization_data(org_name)

        # Get extended data from GraphQL API (social accounts and pinned repos)
        graphql_data = self._get_graphql_organization_data(org_name)

        # Get public members
        public_members = self._get_organization_public_members(org_name)

        # Get repositories (limited to first 100 for performance)
        repositories = (
            self._get_organization_repositories(org_name)
            if include_repositories
            else []
        )

        # Get teams (if accessible)
        teams = self._get_organization_teams(org_name)

        # Check for README and get content
        readme_data = self._get_organization_readme(org_name)

        # Combine all data and create Pydantic model
        org_data = {
            "login": rest_data["login"],
            "name": rest_data.get("name"),
            "description": rest_data.get("description"),
            "email": rest_data.get("email"),
            "location": rest_data.get("location"),
            "company": rest_data.get("company"),
            "blog": rest_data.get("blog"),
            "twitter_username": rest_data.get("twitter_username"),
            "public_repos": rest_data["public_repos"],
            "public_gists": rest_data["public_gists"],
            "followers": rest_data["followers"],
            "following": rest_data["following"],
            "created_at": rest_data["created_at"],
            "updated_at": rest_data["updated_at"],
            "avatar_url": rest_data["avatar_url"],
            "html_url": rest_data["html_url"],
            "gravatar_id": rest_data.get("gravatar_id"),
            "type": rest_data["type"],
            "node_id": rest_data["node_id"],
            "url": rest_data["url"],
            "repos_url": rest_data["repos_url"],
            "events_url": rest_data["events_url"],
            "hooks_url": rest_data["hooks_url"],
            "issues_url": rest_data["issues_url"],
            "members_url": rest_data["members_url"],
            "public_members": public_members,
            "repositories": repositories,
            "teams": teams,
            "readme_url": readme_data.get("url"),
            "readme_content": readme_data.get("content"),
            "social_accounts": graphql_data.get("social_accounts", []),
            "pinned_repositories": graphql_data.get("pinned_repositories", []),
        }

        return GitHubOrganizationMetadata(**org_data)

    def _get_rest_organization_data(self, org_name: str) -> Dict[str, Any]:
        """Get basic organization data from REST API"""
        url = f"{self.rest_base_url}/orgs/{org_name}"
        response = requests.get(url, headers=self._auth_headers())

        if response.status_code == 404:
            raise ValueError(f"Organization '{org_name}' not found")

        response.raise_for_status()
        return response.json()

    def _get_graphql_organization_data(self, org_name: str) -> Dict[str, Any]:
        """Get extended organization data from GraphQL API including social accounts and pinned repos"""
        query = """
        query($org_name: String!) {
            organization(login: $org_name) {
                socialAccounts(first: 10) {
                    nodes {
                        provider
                        url
                        displayName
                    }
                }
                pinnedItems(first: 6, types: REPOSITORY) {
                    nodes {
                        ... on Repository {
                            name
                            description
                            url
                            stargazerCount
                            forkCount
                            primaryLanguage {
                                name
                                color
                            }
                            isPrivate
                            updatedAt
                        }
                    }
                }
            }
        }
        """

        variables = {"org_name": org_name}

        payload = {"query": query, "variables": variables}

        response = requests.post(
            self.graphql_url,
            headers=self._auth_headers({"Content-Type": "application/json"}),
            data=json.dumps(payload),
        )

        if response.status_code != 200:
            return {"social_accounts": [], "pinned_repositories": []}

        data = response.json()

        if "errors" in data:
            return {"social_accounts": [], "pinned_repositories": []}

        org_data = data["data"]["organization"]
        if not org_data:
            return {"social_accounts": [], "pinned_repositories": []}

        # Extract social accounts
        social_accounts = []
        if org_data.get("socialAccounts") and org_data["socialAccounts"].get("nodes"):
            for account in org_data["socialAccounts"]["nodes"]:
                social_accounts.append(
                    {
                        "provider": account["provider"],
                        "url": account["url"],
                        "display_name": account.get("displayName", ""),
                    },
                )

        # Extract pinned repositories
        pinned_repositories = []
        if org_data.get("pinnedItems") and org_data["pinnedItems"].get("nodes"):
            for repo in org_data["pinnedItems"]["nodes"]:
                pinned_repo = {
                    "name": repo["name"],
                    "description": repo.get("description"),
                    "url": repo["url"],
                    "stargazer_count": repo["stargazerCount"],
                    "fork_count": repo["forkCount"],
                    "is_private": repo["isPrivate"],
                    "updated_at": repo["updatedAt"],
                }

                if repo.get("primaryLanguage"):
                    pinned_repo["primary_language"] = {
                        "name": repo["primaryLanguage"]["name"],
                        "color": repo["primaryLanguage"]["color"],
                    }

                pinned_repositories.append(pinned_repo)

        return {
            "social_accounts": social_accounts,
            "pinned_repositories": pinned_repositories,
        }

    def _get_organization_public_members(self, org_name: str) -> List[str]:
        """Get organization's public members"""
        url = f"{self.rest_base_url}/orgs/{org_name}/public_members"
        response = requests.get(url, headers=self._auth_headers())

        if response.status_code != 200:
            return []

        members_data = response.json()
        return [member["login"] for member in members_data]

    def _get_organization_repositories(
        self,
        org_name: str,
        limit: int = 100,
    ) -> List[str]:
        """Get organization's repositories (limited for performance)"""
        url = f"{self.rest_base_url}/orgs/{org_name}/repos"
        params = {"per_page": limit, "sort": "updated"}
        response = requests.get(url, headers=self._auth_headers(), params=params)

        if response.status_code != 200:
            return []

        repos_data = response.json()
        return [repo["name"] for repo in repos_data]

    def _get_organization_teams(self, org_name: str) -> List[str]:
        """Get organization's teams (requires organization membership)"""
        url = f"{self.rest_base_url}/orgs/{org_name}/teams"
        response = requests.get(url, headers=self._auth_headers())

        if response.status_code != 200:
            # This is expected for external users who can't see teams
            return []

        teams_data = response.json()
        return [team["name"] for team in teams_data]

    def _get_organization_readme(self, org_name: str) -> Dict[str, Optional[str]]:
        """Get organization's README URL and content if it exists"""
        # Organizations can have a README in a special repository named .github
        # Try to get README from the .github repository
        readme_paths = ["profile/README.md", "README.md"]

        for readme_path in readme_paths:
            url = (
                f"{self.rest_base_url}/repos/{org_name}/.github/contents/{readme_path}"
            )
            response = requests.get(url, headers=self._auth_headers())

            if response.status_code == 200:
                readme_data = response.json()
                content = self._decode_readme_content(readme_data.get("content", ""))
                return {
                    "url": f"https://github.com/{org_name}/.github/blob/main/{readme_path}",
                    "content": content,
                }

        # Try master branch as fallback
        for readme_path in readme_paths:
            url = (
                f"{self.rest_base_url}/repos/{org_name}/.github/contents/{readme_path}"
            )
            params = {"ref": "master"}
            response = requests.get(url, headers=self._auth_headers(), params=params)

            if response.status_code == 200:
                readme_data = response.json()
                content = self._decode_readme_content(readme_data.get("content", ""))
                return {
                    "url": f"https://github.com/{org_name}/.github/blob/master/{readme_path}",
                    "content": content,
                }

        return {"url": None, "content": None}

    def _decode_readme_content(self, encoded_content: str) -> Optional[str]:
        """Decode base64 encoded README content"""
        if not encoded_content:
            return None

        try:
            # GitHub API returns content in base64 format
            decoded_bytes = base64.b64decode(encoded_content)
            return decoded_bytes.decode("utf-8")
        except Exception as e:
            print(f"Warning: Could not decode README content: {e}")
            return None


def is_it_github_organization(org_name: str) -> bool:
    """
    Check if the given name is a valid GitHub organization.

    Args:
        org_name: GitHub organization name to check

    Returns:
        True if organization exists, False otherwise
    """
    parser = GitHubOrganizationsParser()
    try:
        parser.get_organization_metadata(org_name)
        return True
    except ValueError:
        return False
    except requests.RequestException as e:
        print(f"API Error: {e}")
        return False


def parse_github_organization(org_name: str) -> GitHubOrganizationMetadata:
    """
    Parse GitHub organization metadata

    Args:
        org_name: GitHub organization name

    Returns:
        GitHubOrganizationMetadata object with all available information
    """
    parser = GitHubOrganizationsParser()

    try:
        # Get organization metadata
        org_metadata = parser.get_organization_metadata(org_name)

        # Export to JSON
        # print("\nJSON representation:")
        # print(json.dumps(org_metadata.dict(), indent=2))

        return org_metadata

    except ValueError as e:
        print(f"Error: {e}")
        raise
    except requests.RequestException as e:
        print(f"API Error: {e}")
        raise
