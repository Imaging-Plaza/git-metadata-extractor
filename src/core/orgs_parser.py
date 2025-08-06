import requests
import json
import base64
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field, validator
from datetime import datetime
import os
from dotenv import load_dotenv

load_dotenv()

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]


class GitHubOrganizationMetadata(BaseModel):
    """Pydantic model to store GitHub organization metadata with validation"""
    login: str = Field(..., description="Organization username/login")
    name: Optional[str] = Field(None, description="Organization's display name")
    description: Optional[str] = Field(None, description="Organization's description")
    email: Optional[str] = Field(None, description="Organization's public email")
    location: Optional[str] = Field(None, description="Organization's location")
    company: Optional[str] = Field(None, description="Organization's company")
    blog: Optional[str] = Field(None, description="Organization's blog URL")
    twitter_username: Optional[str] = Field(None, description="Twitter username")
    public_repos: int = Field(..., ge=0, description="Number of public repositories")
    public_gists: int = Field(..., ge=0, description="Number of public gists")
    followers: int = Field(..., ge=0, description="Number of followers")
    following: int = Field(..., ge=0, description="Number of users following")
    created_at: str = Field(..., description="Organization creation date")
    updated_at: str = Field(..., description="Last organization update date")
    avatar_url: str = Field(..., description="Avatar image URL")
    html_url: str = Field(..., description="GitHub organization URL")
    gravatar_id: Optional[str] = Field(None, description="Gravatar ID")
    type: str = Field(..., description="Type (should be 'Organization')")
    node_id: str = Field(..., description="GraphQL node ID")
    url: str = Field(..., description="API URL")
    repos_url: str = Field(..., description="Repositories API URL")
    events_url: str = Field(..., description="Events API URL")
    hooks_url: str = Field(..., description="Hooks API URL")
    issues_url: str = Field(..., description="Issues API URL")
    members_url: str = Field(..., description="Members API URL")

    # Additional metadata
    public_members: List[str] = Field(default_factory=list, description="Public members")
    repositories: List[str] = Field(default_factory=list, description="Repository names")
    teams: List[str] = Field(default_factory=list, description="Team names")
    readme_url: Optional[str] = Field(None, description="Profile README URL if exists")
    readme_content: Optional[str] = Field(None, description="Profile README content if exists")
    social_accounts: List[Dict[str, str]] = Field(default_factory=list, description="Social media accounts")
    pinned_repositories: List[Dict[str, Any]] = Field(default_factory=list, description="Pinned repositories")

    @validator('email')
    def validate_email(cls, v):
        """Basic email validation"""
        if v is not None and v != "" and '@' not in v:
            raise ValueError('Invalid email format')
        return v

    class Config:
        """Pydantic configuration"""
        validate_assignment = True
        extra = "forbid"


class GitHubOrganizationsParser:
    """Parser for GitHub organization metadata using REST and GraphQL APIs"""
    
    def __init__(self):
        """Initialize the parser with GitHub token for higher rate limits"""
        self.github_token = GITHUB_TOKEN
        self.rest_base_url = "https://api.github.com"
        self.graphql_url = "https://api.github.com/graphql"
        
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "GitHubOrganizationsParser/1.0"
        }
        
        if self.github_token:
            self.headers["Authorization"] = f"token {self.github_token}"
    
    def get_organization_metadata(self, org_name: str) -> GitHubOrganizationMetadata:
        """
        Retrieve comprehensive organization metadata from GitHub
        
        Args:
            org_name: GitHub organization name
            
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
        repositories = self._get_organization_repositories(org_name)
        
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
            "pinned_repositories": graphql_data.get("pinned_repositories", [])
        }
        
        return GitHubOrganizationMetadata(**org_data)
    
    def _get_rest_organization_data(self, org_name: str) -> Dict[str, Any]:
        """Get basic organization data from REST API"""
        url = f"{self.rest_base_url}/orgs/{org_name}"
        response = requests.get(url, headers=self.headers)
        
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
        
        payload = {
            "query": query,
            "variables": variables
        }
        
        headers = self.headers.copy()
        headers["Content-Type"] = "application/json"
        
        response = requests.post(
            self.graphql_url, 
            headers=headers, 
            data=json.dumps(payload)
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
                social_accounts.append({
                    "provider": account["provider"],
                    "url": account["url"],
                    "display_name": account.get("displayName", "")
                })
        
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
                    "updated_at": repo["updatedAt"]
                }
                
                if repo.get("primaryLanguage"):
                    pinned_repo["primary_language"] = {
                        "name": repo["primaryLanguage"]["name"],
                        "color": repo["primaryLanguage"]["color"]
                    }
                
                pinned_repositories.append(pinned_repo)
        
        return {
            "social_accounts": social_accounts,
            "pinned_repositories": pinned_repositories
        }
    
    def _get_organization_public_members(self, org_name: str) -> List[str]:
        """Get organization's public members"""
        url = f"{self.rest_base_url}/orgs/{org_name}/public_members"
        response = requests.get(url, headers=self.headers)
        
        if response.status_code != 200:
            return []
        
        members_data = response.json()
        return [member["login"] for member in members_data]
    
    def _get_organization_repositories(self, org_name: str, limit: int = 100) -> List[str]:
        """Get organization's repositories (limited for performance)"""
        url = f"{self.rest_base_url}/orgs/{org_name}/repos"
        params = {"per_page": limit, "sort": "updated"}
        response = requests.get(url, headers=self.headers, params=params)
        
        if response.status_code != 200:
            return []
        
        repos_data = response.json()
        return [repo["name"] for repo in repos_data]
    
    def _get_organization_teams(self, org_name: str) -> List[str]:
        """Get organization's teams (requires organization membership)"""
        url = f"{self.rest_base_url}/orgs/{org_name}/teams"
        response = requests.get(url, headers=self.headers)
        
        if response.status_code != 200:
            # This is expected for external users who can't see teams
            return []
        
        teams_data = response.json()
        return [team["name"] for team in teams_data]
    
    def _get_organization_readme(self, org_name: str) -> Dict[str, Optional[str]]:
        """Get organization's README URL and content if it exists"""
        # Organizations can have a README in a special repository named .github
        # Try to get README from the .github repository
        readme_paths = [
            "profile/README.md",
            "README.md"
        ]
        
        for readme_path in readme_paths:
            url = f"{self.rest_base_url}/repos/{org_name}/.github/contents/{readme_path}"
            response = requests.get(url, headers=self.headers)
            
            if response.status_code == 200:
                readme_data = response.json()
                content = self._decode_readme_content(readme_data.get("content", ""))
                return {
                    "url": f"https://github.com/{org_name}/.github/blob/main/{readme_path}",
                    "content": content
                }
        
        # Try master branch as fallback
        for readme_path in readme_paths:
            url = f"{self.rest_base_url}/repos/{org_name}/.github/contents/{readme_path}"
            params = {"ref": "master"}
            response = requests.get(url, headers=self.headers, params=params)
            
            if response.status_code == 200:
                readme_data = response.json()
                content = self._decode_readme_content(readme_data.get("content", ""))
                return {
                    "url": f"https://github.com/{org_name}/.github/blob/master/{readme_path}",
                    "content": content
                }
        
        return {"url": None, "content": None}
    
    def _decode_readme_content(self, encoded_content: str) -> Optional[str]:
        """Decode base64 encoded README content"""
        if not encoded_content:
            return None
        
        try:
            # GitHub API returns content in base64 format
            decoded_bytes = base64.b64decode(encoded_content)
            return decoded_bytes.decode('utf-8')
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