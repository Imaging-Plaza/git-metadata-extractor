import requests
import json
import re
import base64
from typing import Dict, List, Optional, Any
from pydantic import BaseModel, Field, validator
from datetime import datetime
import os
from dotenv import load_dotenv

from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.common.desired_capabilities import DesiredCapabilities
from selenium.webdriver.firefox.options import Options

import time

load_dotenv()

GITHUB_TOKEN = os.environ["GITHUB_TOKEN"]
SELENIUM_REMOTE_URL = os.environ.get("SELENIUM_REMOTE_URL", "http://localhost:4444")


class ORCIDEmployment(BaseModel):
    """ORCID employment entry"""
    organization: str = Field(..., description="Organization name")
    role: Optional[str] = Field(None, description="Job title/role")
    start_date: Optional[str] = Field(None, description="Start date")
    end_date: Optional[str] = Field(None, description="End date")
    location: Optional[str] = Field(None, description="Location")
    duration_years: Optional[float] = Field(None, description="Duration in years")


class ORCIDEducation(BaseModel):
    """ORCID education entry"""
    organization: str = Field(..., description="Educational institution")
    degree: Optional[str] = Field(None, description="Degree or qualification")
    start_date: Optional[str] = Field(None, description="Start date")
    end_date: Optional[str] = Field(None, description="End date")
    location: Optional[str] = Field(None, description="Location")
    duration_years: Optional[float] = Field(None, description="Duration in years")


class ORCIDActivities(BaseModel):
    """ORCID activities data"""
    employment: List[ORCIDEmployment] = Field(default_factory=list, description="Employment history")
    education: List[ORCIDEducation] = Field(default_factory=list, description="Education history")
    works_count: Optional[int] = Field(None, description="Number of works/publications")
    peer_reviews_count: Optional[int] = Field(None, description="Number of peer reviews")
    orcid_content: Optional[str] = Field(None, description="Parsed ORCID Activities content as Markdown")
    orcid_format: Optional[str] = Field(default="markdown", description="Format of orcid_content")


class GitHubUserMetadata(BaseModel):
    """Pydantic model to store GitHub user metadata with validation"""
    login: str = Field(..., description="GitHub username")
    name: Optional[str] = Field(None, description="User's display name")
    bio: Optional[str] = Field(None, description="User's bio")
    email: Optional[str] = Field(None, description="User's public email")
    location: Optional[str] = Field(None, description="User's location")
    company: Optional[str] = Field(None, description="User's company")
    blog: Optional[str] = Field(None, description="User's blog URL")
    twitter_username: Optional[str] = Field(None, description="Twitter username")
    public_repos: int = Field(..., ge=0, description="Number of public repositories")
    public_gists: int = Field(..., ge=0, description="Number of public gists")
    followers: int = Field(..., ge=0, description="Number of followers")
    following: int = Field(..., ge=0, description="Number of users following")
    created_at: str = Field(..., description="Account creation date")
    updated_at: str = Field(..., description="Last profile update date")
    avatar_url: str = Field(..., description="Avatar image URL")
    html_url: str = Field(..., description="GitHub profile URL")
    orcid: Optional[str] = Field(None, description="ORCID identifier")
    orcid_activities: Optional[ORCIDActivities] = Field(None, description="ORCID activities data")
    organizations: List[str] = Field(default_factory=list, description="Public organizations")
    social_accounts: List[Dict[str, str]] = Field(default_factory=list, description="Social media accounts")
    readme_url: Optional[str] = Field(None, description="Profile README URL if exists")
    readme_content: Optional[str] = Field(None, description="Profile README content if exists")

    @validator('orcid')
    def validate_orcid(cls, v):
        """Validate ORCID format"""
        if v is not None:
            orcid_pattern = r'^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$'
            if not re.match(orcid_pattern, v):
                raise ValueError('Invalid ORCID format')
        return v

    @validator('email')
    def validate_email(cls, v):
        """Basic email validation"""
        if v is not None and '@' not in v:
            raise ValueError('Invalid email format')
        return v

    class Config:
        """Pydantic configuration"""
        validate_assignment = True
        extra = "forbid"


class GitHubUsersParser:
    """Parser for GitHub user metadata using REST and GraphQL APIs"""
    
    def __init__(self):
        """
        Initialize the parser with optional GitHub token for higher rate limits
        
        """
        self.github_token = GITHUB_TOKEN
        self.rest_base_url = "https://api.github.com"
        self.graphql_url = "https://api.github.com/graphql"
        
        self.headers = {
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "GitHubUsersParser/1.0"
        }
        
        if self.github_token:
            self.headers["Authorization"] = f"token {self.github_token}"
    
    def get_user_metadata(self, username: str) -> GitHubUserMetadata:
        """
        Retrieve comprehensive user metadata from GitHub
        
        Args:
            username: GitHub username
            
        Returns:
            GitHubUserMetadata object with all available user information
            
        Raises:
            requests.RequestException: If API calls fail
            ValueError: If user not found
        """
        # Get basic user data from REST API
        rest_data = self._get_rest_user_data(username)
        
        # Get extended data from GraphQL API (social accounts)
        graphql_data = self._get_graphql_user_data(username)
        
        # Get organizations
        organizations = self._get_user_organizations(username)
        
        # Check for README and get content
        readme_data = self._get_user_readme(username)
        
        # Scrape ORCID from profile page
        orcid = self._scrape_orcid_from_profile(username)
        
        # If not found in profile, try extracting from bio
        if not orcid:
            orcid = self._extract_orcid_from_bio(rest_data.get("bio", ""))
        
        # Get ORCID activities if ORCID is found
        orcid_activities = None
        if orcid:
            orcid_activities = self._scrape_orcid_activities(orcid)
        
        # Combine all data and create Pydantic model
        user_data = {
            "login": rest_data["login"],
            "name": rest_data.get("name"),
            "bio": rest_data.get("bio"),
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
            "orcid": orcid,
            "orcid_activities": orcid_activities,
            "organizations": organizations,
            "social_accounts": graphql_data.get("social_accounts", []),
            "readme_url": readme_data.get("url"),
            "readme_content": readme_data.get("content")
        }
        
        return GitHubUserMetadata(**user_data)
    
    def _scrape_orcid_from_profile(self, username: str) -> Optional[str]:
        """
        Scrape ORCID from GitHub profile page
        
        Args:
            username: GitHub username
            
        Returns:
            ORCID ID if found, None otherwise
        """
        try:
            profile_url = f"https://github.com/{username}"
            
            # Use a browser-like user agent to avoid blocking
            scraping_headers = {
                "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            
            response = requests.get(profile_url, headers=scraping_headers, timeout=10)
            
            if response.status_code != 200:
                return None
            
            soup = BeautifulSoup(response.content, 'html.parser')
            
            # Look for ORCID links in social links section
            # Target the specific element structure you mentioned
            orcid_links = soup.find_all('a', href=re.compile(r'https://orcid\.org/\d{4}-\d{4}-\d{4}-\d{3}[\dX]'))
            
            if orcid_links:
                orcid_url = orcid_links[0]['href']
                # Extract ORCID ID from URL
                orcid_match = re.search(r'(\d{4}-\d{4}-\d{4}-\d{3}[\dX])', orcid_url)
                if orcid_match:
                    return orcid_match.group(1)
            
            # Alternative: Look in all text content for ORCID patterns
            page_text = soup.get_text()
            orcid_patterns = [
                r'https://orcid\.org/(\d{4}-\d{4}-\d{4}-\d{3}[\dX])',
                r'orcid\.org/(\d{4}-\d{4}-\d{4}-\d{3}[\dX])',
                r'\b(\d{4}-\d{4}-\d{4}-\d{3}[\dX])\b'
            ]
            
            for pattern in orcid_patterns:
                match = re.search(pattern, page_text)
                if match:
                    return match.group(1)
            
            return None
            
        except Exception as e:
            print(f"Warning: Could not scrape ORCID from profile: {e}")
            return None
    
    def _scrape_orcid_activities(self, orcid_id: str) -> Optional[ORCIDActivities]:
        """
        Scrape activities from ORCID profile page using Selenium
        
        Args:
            orcid_id: ORCID identifier (e.g., "0000-0002-8076-2034")
            
        Returns:
            ORCIDActivities object with employment and education data
        """
        driver = None
        try:
            orcid_url = f"https://orcid.org/{orcid_id}"

            options = Options()
            options.headless = True
            options.add_argument("--no-sandbox")
            options.add_argument("--disable-dev-shm-usage")
            options.add_argument("--width=1920")
            options.add_argument("--height=1080")
            options.add_argument("--user-agent=Mozilla/5.0 (X11; Linux x86_64; rv:140.0) Gecko/20100101 Firefox/140.0")
            
            # Set Firefox capabilities
            capabilities = DesiredCapabilities.FIREFOX.copy()
            capabilities['browserName'] = 'firefox'
            
            driver = webdriver.Remote(
                command_executor=SELENIUM_REMOTE_URL,
                options=options,
            )
            driver.get(orcid_url)
            
            # Wait for the page to load
            WebDriverWait(driver, 10).until(
                EC.presence_of_element_located((By.TAG_NAME, "body"))
            )
            
            # Wait a bit more for dynamic content to load
            time.sleep(3)
            
            # Get the page source and parse with BeautifulSoup
            html_content = driver.page_source
            soup = BeautifulSoup(html_content, 'html.parser')
            
            # Extract raw HTML from Activities section
            activities_html = self._extract_activities_html(soup)
            
            # Extract employment data
            employment_list = self._extract_employment_from_orcid_selenium(soup)
            
            # Extract education data
            education_list = self._extract_education_from_orcid_selenium(soup)
            
            # Extract activity counts
            works_count = self._extract_works_count_selenium(soup)
            peer_reviews_count = self._extract_peer_reviews_count_selenium(soup)
            
            return ORCIDActivities(
                employment=employment_list,
                education=education_list,
                works_count=works_count,
                peer_reviews_count=peer_reviews_count,
                orcid_content=activities_html
            )
            
        except Exception as e:
            print(f"Warning: Could not scrape ORCID activities: {e}")
            return None
        finally:
            if driver:
                driver.quit()
    
    def _extract_employment_from_orcid_selenium(self, soup: BeautifulSoup) -> List[ORCIDEmployment]:
        """Extract employment information from ORCID page using Selenium-rendered HTML"""
        employment_list = []
        
        try:
            # Look for employment section
            employment_section = soup.find('section', {'id': 'affiliations'})
            if not employment_section:
                print("Warning: Employment section not found")
                return employment_list
            
            # Find employment entries - they might be in different containers
            employment_containers = employment_section.find_all(['app-affiliation-stack-group', 'div'], 
                                                              class_=re.compile(r'affiliation|employment'))
            
            if not employment_containers:
                # Try alternative selectors
                employment_containers = employment_section.find_all('div', 
                                                                  string=re.compile(r'\d{4}'))
            
            print(f"Found {len(employment_containers)} employment containers")
            
            for container in employment_containers:
                try:
                    # Extract text content
                    text_content = container.get_text(separator=' ', strip=True)
                    
                    # Skip if empty or too short
                    if len(text_content) < 10:
                        continue
                    
                    # Extract organization name (usually the first substantial text)
                    organization = self._extract_organization_name(text_content)
                    
                    # Extract dates
                    start_date, end_date = self._extract_dates_from_text(text_content)
                    
                    # Extract role/title
                    role = self._extract_role_from_text(text_content)
                    
                    # Extract location
                    location = self._extract_location_from_text(text_content)
                    
                    # Calculate duration
                    duration_years = self._calculate_duration(start_date, end_date)
                    
                    # Only add if we have at least an organization
                    if organization:
                        employment_list.append(ORCIDEmployment(
                            organization=organization,
                            role=role,
                            start_date=start_date,
                            end_date=end_date,
                            location=location,
                            duration_years=duration_years
                        ))
                        print(f"Added employment: {organization}")
                    
                except Exception as e:
                    print(f"Warning: Could not parse employment entry: {e}")
                    continue
                
        except Exception as e:
            print(f"Warning: Could not extract employment data: {e}")
        
        return employment_list
    
    def _extract_education_from_orcid_selenium(self, soup: BeautifulSoup) -> List[ORCIDEducation]:
        """Extract education information from ORCID page using Selenium-rendered HTML"""
        education_list = []
        
        try:
            # Look for education section
            education_section = soup.find('section', {'id': 'education-and-qualification'})
            if not education_section:
                print("Warning: Education section not found")
                return education_list
            
            # Find education entries
            education_containers = education_section.find_all(['app-affiliation-stack-group', 'div'], 
                                                            class_=re.compile(r'affiliation|education'))
            
            if not education_containers:
                education_containers = education_section.find_all('div', 
                                                                string=re.compile(r'\d{4}'))
            
            print(f"Found {len(education_containers)} education containers")
            
            for container in education_containers:
                try:
                    text_content = container.get_text(separator=' ', strip=True)
                    
                    if len(text_content) < 10:
                        continue
                    
                    organization = self._extract_organization_name(text_content)
                    start_date, end_date = self._extract_dates_from_text(text_content)
                    degree = self._extract_degree_from_text(text_content)
                    location = self._extract_location_from_text(text_content)
                    duration_years = self._calculate_duration(start_date, end_date)
                    
                    if organization:
                        education_list.append(ORCIDEducation(
                            organization=organization,
                            degree=degree,
                            start_date=start_date,
                            end_date=end_date,
                            location=location,
                            duration_years=duration_years
                        ))
                        print(f"Added education: {organization}")
                    
                except Exception as e:
                    print(f"Warning: Could not parse education entry: {e}")
                    continue
                
        except Exception as e:
            print(f"Warning: Could not extract education data: {e}")
        
        return education_list
    
    def _extract_works_count_selenium(self, soup: BeautifulSoup) -> Optional[int]:
        """Extract works count from ORCID page using Selenium-rendered HTML"""
        try:
            # Look for works section with count
            works_patterns = [
                r'Works.*\((\d+)\)',
                r'(\d+)\s+works',
                r'(\d+)\s+publications'
            ]
            
            page_text = soup.get_text()
            
            for pattern in works_patterns:
                match = re.search(pattern, page_text, re.IGNORECASE)
                if match:
                    count = int(match.group(1))
                    print(f"Found works count: {count}")
                    return count
                    
        except Exception as e:
            print(f"Warning: Could not extract works count: {e}")
        return None
    
    def _extract_peer_reviews_count_selenium(self, soup: BeautifulSoup) -> Optional[int]:
        """Extract peer reviews count from ORCID page using Selenium-rendered HTML"""
        try:
            # Look for peer review section with count
            peer_review_patterns = [
                r'(\d+)\s+reviews?\s+for\s+(\d+)\s+publications',
                r'Peer review.*\((\d+)\s+reviews?',
                r'(\d+)\s+peer\s+reviews?'
            ]
            
            page_text = soup.get_text()
            
            for pattern in peer_review_patterns:
                match = re.search(pattern, page_text, re.IGNORECASE)
                if match:
                    count = int(match.group(1))
                    print(f"Found peer reviews count: {count}")
                    return count
                    
        except Exception as e:
            print(f"Warning: Could not extract peer reviews count: {e}")
        return None
    
    def _extract_organization_name(self, text: str) -> Optional[str]:
        """Extract organization name from text"""
        # Split by common separators and take the first substantial part
        parts = re.split(r'[,\n\t]', text)
        for part in parts:
            part = part.strip()
            # Look for text that's not just dates or common words
            if len(part) > 3 and not re.match(r'^\d{4}', part):
                return part
        return None
    
    def _extract_dates_from_text(self, text: str) -> tuple[Optional[str], Optional[str]]:
        """Extract start and end dates from text"""
        # Look for "YYYY to YYYY" pattern first (most specific)
        to_pattern = r'\b(\d{4})\s+to\s+(\d{4})\b'
        to_match = re.search(to_pattern, text)
        if to_match:
            return to_match.group(1), to_match.group(2)
        
        # Look for other date patterns as fallback
        date_patterns = [
            r'\b(\d{1,2}[/-]\d{4})\b',  # MM/YYYY or MM-YYYY
            r'\b(\d{4})\b'               # YYYY
        ]
        
        dates = []
        for pattern in date_patterns:
            matches = re.findall(pattern, text)
            dates.extend(matches)
        
        # Remove duplicates while preserving order
        unique_dates = []
        for date in dates:
            if date not in unique_dates:
                unique_dates.append(date)
        
        if len(unique_dates) >= 2:
            return unique_dates[0], unique_dates[1]
        elif len(unique_dates) == 1:
            return unique_dates[0], None
        
        return None, None
    
    def _extract_role_from_text(self, text: str) -> Optional[str]:
        """Extract role/title from text"""
        # Common role indicators
        role_keywords = ['professor', 'researcher', 'scientist', 'director', 'manager', 'analyst', 'engineer']
        
        words = text.lower().split()
        for i, word in enumerate(words):
            if any(keyword in word for keyword in role_keywords):
                # Return a few words around the keyword
                start = max(0, i-1)
                end = min(len(words), i+3)
                return ' '.join(words[start:end]).title()
        
        return None
    
    def _extract_degree_from_text(self, text: str) -> Optional[str]:
        """Extract degree from text"""
        degree_patterns = [
            r'\b(Ph\.?D\.?|PhD|Doctor of Philosophy)\b',
            r'\b(M\.?S\.?|MS|Master of Science)\b',
            r'\b(M\.?A\.?|MA|Master of Arts)\b',
            r'\b(B\.?S\.?|BS|Bachelor of Science)\b',
            r'\b(B\.?A\.?|BA|Bachelor of Arts)\b',
            r'\b(Bachelor|Master|Doctor)\s+[oO]f\s+\w+\b'
        ]
        
        for pattern in degree_patterns:
            match = re.search(pattern, text, re.IGNORECASE)
            if match:
                return match.group(0)
        
        return None
    
    def _extract_location_from_text(self, text: str) -> Optional[str]:
        """Extract location from text"""
        # Look for patterns like "City, Country" or "State, USA"
        location_pattern = r'\b([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*),\s*([A-Z][a-z]+(?:\s+[A-Z][a-z]+)*)\b'
        match = re.search(location_pattern, text)
        if match:
            return f"{match.group(1)}, {match.group(2)}"
        
        return None
    
    def _extract_employment_from_orcid(self, soup: BeautifulSoup) -> List[ORCIDEmployment]:
        """Extract employment information from ORCID page"""
        employment_list = []
        
        try:
            # Look for employment section
            employment_section = soup.find('section', {'id': 'affiliations'})
            if not employment_section:
                return employment_list
            
            # Find employment panels
            employment_panels = employment_section.find_all('app-affiliation-stack-group')
            
            for panel in employment_panels:
                # Extract organization name
                org_elements = panel.find_all(string=re.compile(r'\S+'))
                organization = None
                role = None
                start_date = None
                end_date = None
                location = None
                
                # Try to extract structured data from text content
                text_content = panel.get_text(strip=True)
                
                # Look for date patterns (YYYY or MM/YYYY)
                date_matches = re.findall(r'\b(\d{4}|\d{1,2}/\d{4})\b', text_content)
                
                if len(date_matches) >= 2:
                    start_date = date_matches[0]
                    end_date = date_matches[1] if date_matches[1] != 'present' else None
                elif len(date_matches) == 1:
                    start_date = date_matches[0]
                
                # Calculate duration if we have dates
                duration_years = self._calculate_duration(start_date, end_date)
                
                # This is a simplified extraction - ORCID's dynamic content makes it complex
                # You might need to use Selenium for better extraction
                employment_list.append(ORCIDEmployment(
                    organization=organization or "Unknown Organization",
                    role=role,
                    start_date=start_date,
                    end_date=end_date,
                    location=location,
                    duration_years=duration_years
                ))
                
        except Exception as e:
            print(f"Warning: Could not extract employment data: {e}")
        
        return employment_list
    
    def _extract_education_from_orcid(self, soup: BeautifulSoup) -> List[ORCIDEducation]:
        """Extract education information from ORCID page"""
        education_list = []
        
        try:
            # Look for education section
            education_section = soup.find('section', {'id': 'education-and-qualification'})
            if not education_section:
                return education_list
            
            # Similar extraction logic as employment
            # This is simplified - actual implementation would need more sophisticated parsing
            
        except Exception as e:
            print(f"Warning: Could not extract education data: {e}")
        
        return education_list
    
    def _extract_works_count(self, soup: BeautifulSoup) -> Optional[int]:
        """Extract works count from ORCID page"""
        try:
            # Look for works section with count
            works_text = soup.find(string=re.compile(r'Works.*\((\d+)\)'))
            if works_text:
                match = re.search(r'\((\d+)\)', works_text)
                if match:
                    return int(match.group(1))
        except Exception:
            pass
        return None
    
    def _extract_peer_reviews_count(self, soup: BeautifulSoup) -> Optional[int]:
        """Extract peer reviews count from ORCID page"""
        try:
            # Look for peer review section with count
            peer_review_text = soup.find(string=re.compile(r'Peer review.*\((\d+)\s+reviews'))
            if peer_review_text:
                match = re.search(r'\((\d+)\s+reviews', peer_review_text)
                if match:
                    return int(match.group(1))
        except Exception:
            pass
        return None
    
    def _calculate_duration(self, start_date: Optional[str], end_date: Optional[str]) -> Optional[float]:
        """Calculate duration in years between start and end dates"""
        if not start_date:
            return None
        
        try:
            # Parse start year
            start_year = int(start_date.split('/')[-1])
            
            # If no end date, assume current year
            if not end_date:
                end_year = datetime.now().year
            else:
                end_year = int(end_date.split('/')[-1])
            
            return float(end_year - start_year)
            
        except (ValueError, IndexError):
            return None
    
    def _get_rest_user_data(self, username: str) -> Dict[str, Any]:
        """Get basic user data from REST API"""
        url = f"{self.rest_base_url}/users/{username}"
        response = requests.get(url, headers=self.headers)
        
        if response.status_code == 404:
            raise ValueError(f"User '{username}' not found")
        
        response.raise_for_status()
        return response.json()
    
    def _get_graphql_user_data(self, username: str) -> Dict[str, Any]:
        """Get extended user data from GraphQL API including social accounts"""
        query = """
        query($username: String!) {
            user(login: $username) {
                socialAccounts(first: 10) {
                    nodes {
                        provider
                        url
                        displayName
                    }
                }
                ... on User {
                    bio
                }
            }
        }
        """
        
        variables = {"username": username}
        
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
            # If GraphQL fails, return empty data
            return {"social_accounts": []}
        
        data = response.json()
        
        if "errors" in data:
            return {"social_accounts": []}
        
        user_data = data["data"]["user"]
        if not user_data:
            return {"social_accounts": []}
        
        # Extract social accounts
        social_accounts = []
        if user_data.get("socialAccounts") and user_data["socialAccounts"].get("nodes"):
            for account in user_data["socialAccounts"]["nodes"]:
                social_accounts.append({
                    "provider": account["provider"],
                    "url": account["url"],
                    "display_name": account.get("displayName", "")
                })
        
        return {
            "social_accounts": social_accounts
        }
    
    def _get_user_organizations(self, username: str) -> List[str]:
        """Get user's public organizations"""
        url = f"{self.rest_base_url}/users/{username}/orgs"
        response = requests.get(url, headers=self.headers)
        
        if response.status_code != 200:
            return []
        
        orgs_data = response.json()
        return [org["login"] for org in orgs_data]
    
    def _get_user_readme(self, username: str) -> Dict[str, Optional[str]]:
        """Get user's README URL and content if it exists"""
        # Try to get README from API first
        url = f"{self.rest_base_url}/repos/{username}/{username}/readme"
        response = requests.get(url, headers=self.headers)
        
        if response.status_code == 200:
            readme_data = response.json()
            content = self._decode_readme_content(readme_data.get("content", ""))
            return {
                "url": f"https://github.com/{username}/{username}/blob/main/README.md",
                "content": content
            }
        
        # Try master branch as fallback
        url = f"{self.rest_base_url}/repos/{username}/{username}/contents/README.md"
        response = requests.get(url, headers=self.headers)
        
        if response.status_code == 200:
            readme_data = response.json()
            content = self._decode_readme_content(readme_data.get("content", ""))
            return {
                "url": f"https://github.com/{username}/{username}/blob/master/README.md",
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
    
    def _extract_activities_html(self, soup: BeautifulSoup) -> Optional[str]:
        """Extract text content from ORCID Activities section"""
        try:
            # Find the Activities section by aria-label
            activities_section = soup.find('section', {'aria-label': 'Activities'})
            
            if not activities_section:
                print("Warning: Activities section not found")
                return None
            
            # Extract all text content from the Activities section
            activities_text = activities_section.get_text(separator='\n', strip=True)
            
            # Clean up the text - remove excessive whitespace and empty lines
            lines = [line.strip() for line in activities_text.split('\n') if line.strip()]
            cleaned_text = '\n'.join(lines)
            
            print(f"Extracted {len(lines)} lines of activities text")
            return cleaned_text
            
        except Exception as e:
            print(f"Warning: Could not extract Activities text: {e}")
            return None


def is_it_github_user(username: str) -> bool:
    """
    Check if the given username is a valid GitHub user.
    
    Args:
        username: GitHub username to check
        
    Returns:
        True if user exists, False otherwise
    """
    parser = GitHubUsersParser()
    try:
        parser.get_user_metadata(username)
        return True
    except ValueError:
        return False
    except requests.RequestException as e:
        print(f"API Error: {e}")
        return False


def parse_github_user(username: str) -> GitHubUserMetadata:
    parser = GitHubUsersParser()

    try:
        # Get user metadata
        user_metadata = parser.get_user_metadata(username)
        
        # Export to JSON
        #print("\nJSON representation:")
        #print(json.dumps(user_metadata.dict(), indent=2))
        
        return user_metadata
        
    except ValueError as e:
        print(f"Error: {e}")
    except requests.RequestException as e:
        print(f"API Error: {e}")