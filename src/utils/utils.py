import ast
import inspect
import json
import logging
import os
import re
from typing import List, Optional, Union, get_args, get_origin

import requests
from pydantic import BaseModel, HttpUrl, create_model
from pyld import jsonld

logger = logging.getLogger(__name__)


def is_github_repo_public(repo_url: str) -> bool:
    """
    Check if a GitHub repository is public by making a request to the GitHub API.

    Args:
        repo_url: The GitHub repository URL (e.g., 'https://github.com/owner/repo')

    Returns:
        bool: True if the repository is public, False otherwise
    """
    # Extract owner and repo name from the URL
    match = re.match(
        r"https?://github\.com/([^/]+)/([^/]+?)(?:\.git)?/?$",
        repo_url.strip(),
    )
    if not match:
        logger.error(f"Invalid GitHub URL format: {repo_url}")
        return False

    owner, repo = match.groups()
    api_url = f"https://api.github.com/repos/{owner}/{repo}"

    # Use GitHub token if available for higher rate limits
    headers = {}
    github_token = os.environ.get("GITHUB_TOKEN")
    if github_token:
        headers["Authorization"] = f"token {github_token}"

    try:
        response = requests.get(api_url, headers=headers, timeout=10)

        if response.status_code == 200:
            repo_data = response.json()
            is_private = repo_data.get("private", True)
            if is_private:
                logger.warning(f"Repository {repo_url} is private")
                return False
            logger.info(f"Repository {repo_url} is public")
            return True
        if response.status_code == 404:
            logger.error(f"Repository not found or not accessible: {repo_url}")
            return False
        if response.status_code == 403:
            # Check if it's a rate limit issue
            rate_limit_remaining = response.headers.get(
                "X-RateLimit-Remaining",
                "unknown",
            )
            rate_limit_reset = response.headers.get("X-RateLimit-Reset", "unknown")
            logger.error(
                f"GitHub API rate limit or access issue for {repo_url}. "
                f"Rate limit remaining: {rate_limit_remaining}, reset at: {rate_limit_reset}",
            )
            return False
        logger.error(
            f"GitHub API returned status {response.status_code} for {repo_url}",
        )
        return False

    except requests.RequestException as e:
        logger.error(f"Failed to check repository visibility: {e}")
        return False


def fetch_jsonld(url):
    """Fetch JSON-LD data from a given URL."""
    headers = {"Accept": "application/ld+json"}
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return ast.literal_eval(response.json().get("output", "{}"))
    raise Exception(
        f"Error fetching data: {response.status_code} - {response.text}",
    )


def clean_json_string(raw_text):
    """Remove triple backticks and 'json' from the response."""
    if raw_text.startswith("```json"):
        raw_text = raw_text[7:]
    if raw_text.endswith("```"):
        raw_text = raw_text[:-3]

    return raw_text.strip()


def json_to_jsonLD(json_data, file_path):
    """Convert json to jsonLD using context file. Returns a jsonLD dictionary"""
    with open(file_path) as context:
        context_data = json.load(context)

    expanded_data = jsonld.expand({**context_data, **json_data})

    return expanded_data[0]


def merge_jsonld(gimie_graph: list, llm_jsonld: dict, output_path: str = None):
    """Merge a GIMIE JSON-LD graph (list of nodes) with a flat LLM JSON-LD object,
    giving priority to GIMIE fields and preserving JSON-LD structure."""

    logger.info("Merging GIMIE (@graph list) and LLM JSON-LD (flat object)...")

    # Identify the SoftwareSourceCode node in GIMIE

    software_node = next(
        (
            node
            for node in gimie_graph
            if "http://schema.org/SoftwareSourceCode" in node.get("@type", [])
        ),
        None,
    )

    if software_node is None:
        raise ValueError("No SoftwareSourceCode node found in GIMIE @graph.")

    # Merge LLM fields that don't exist in GIMIE
    added_fields = []
    for key, value in llm_jsonld.items():
        if key not in software_node:
            software_node[key] = value
            added_fields.append(key)

    logger.info(
        f"Merged {len(added_fields)} fields from LLM into SoftwareSourceCode node.",
    )
    if added_fields:
        logger.debug(f"Fields added: {added_fields}")

    # Reconstruct the final JSON-LD
    merged_jsonld = {"@context": "https://schema.org", "@graph": gimie_graph}

    if output_path:
        # Save to file
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(merged_jsonld, f, indent=4)

        logger.info(f"✅ Merged JSON-LD written to {output_path}")
    else:
        logger.info("✅ Merged JSON-LD")

        return merged_jsonld


# def convert_httpurl_to_str(obj: Any) -> Any:
#     """
#     Recursively convert all HttpUrl fields in a Pydantic model (or nested structures)
#     to plain strings, so the resulting dict is OpenAI-compatible.
#     """
#     if isinstance(obj, HttpUrl):
#         return str(obj)
#     elif isinstance(obj, BaseModel):
#         return {k: convert_httpurl_to_str(v) for k, v in obj.dict(exclude_none=True).items()}
#     elif isinstance(obj, list):
#         return [convert_httpurl_to_str(item) for item in obj]
#     elif isinstance(obj, dict):
#         return {k: convert_httpurl_to_str(v) for k, v in obj.items()}
#     else:
#         return obj


def convert_httpurl_to_str(schema_class):
    """
    Convert HttpUrl fields to str fields for OpenAI compatibility, including nested models.
    """
    if not issubclass(schema_class, BaseModel):
        return schema_class

    # Get the original fields
    original_fields = schema_class.model_fields
    new_fields = {}

    for field_name, field_info in original_fields.items():
        annotation = field_info.annotation
        converted_annotation = _convert_annotation(annotation)
        new_fields[field_name] = (converted_annotation, field_info.default)

    # Create new model class with converted fields
    converted_model = create_model(f"{schema_class.__name__}Converted", **new_fields)

    return converted_model


def _convert_annotation(annotation):
    """
    Recursively convert annotations, replacing HttpUrl with str and handling nested models.
    """
    origin = get_origin(annotation)

    # Handle Union types (Optional, etc.)
    if origin is Union:
        args = get_args(annotation)
        new_args = tuple(_convert_annotation(arg) for arg in args)
        return Union[new_args]

    # Handle List types
    if origin is list or origin is List:
        args = get_args(annotation)
        if args:
            new_args = tuple(_convert_annotation(arg) for arg in args)
            return List[new_args[0]] if len(new_args) == 1 else List[new_args]
        return annotation

    # Handle HttpUrl -> str conversion
    if annotation is HttpUrl:
        return str

    # Handle nested BaseModel classes
    if (
        inspect.isclass(annotation)
        and issubclass(annotation, BaseModel)
        and annotation is not BaseModel
    ):
        return convert_httpurl_to_str(annotation)

    # Return unchanged for all other types
    return annotation


def extract_orcid_id(orcid_url: str) -> Optional[str]:
    """
    Extract ORCID ID from ORCID URL.

    Args:
        orcid_url: ORCID URL (e.g., "https://orcid.org/0000-0002-1126-1535")

    Returns:
        ORCID ID (e.g., "0000-0002-1126-1535") or None if invalid

    Examples:
        >>> extract_orcid_id("https://orcid.org/0000-0002-1126-1535")
        '0000-0002-1126-1535'
        >>> extract_orcid_id("0000-0002-1126-1535")
        '0000-0002-1126-1535'
    """
    if not orcid_url:
        return None

    # If it's already just the ID format, return it
    orcid_pattern = r"\b(\d{4}-\d{4}-\d{4}-\d{3}[\dX])\b"
    match = re.search(orcid_pattern, str(orcid_url))

    if match:
        return match.group(1)

    return None


def normalize_orcid_to_url(orcid_input: str) -> Optional[str]:
    """
    Normalize ORCID input to URL format, validating the format.

    Args:
        orcid_input: ORCID as either ID (0000-0000-0000-0000) or URL

    Returns:
        ORCID URL (e.g., "https://orcid.org/0000-0002-1126-1535") or None if invalid

    Examples:
        >>> normalize_orcid_to_url("0000-0002-1126-1535")
        'https://orcid.org/0000-0002-1126-1535'
        >>> normalize_orcid_to_url("https://orcid.org/0000-0002-1126-1535")
        'https://orcid.org/0000-0002-1126-1535'
    """
    if not orcid_input:
        return None

    # If it's already a URL, validate and return
    if orcid_input.startswith("http"):
        orcid_url_pattern = r"^https://orcid\.org/(\d{4}-\d{4}-\d{4}-\d{3}[\dX])$"
        match = re.match(orcid_url_pattern, orcid_input)
        if match:
            return orcid_input
        logger.warning(f"Invalid ORCID URL format: {orcid_input}")
        return None

    # If it's an ID, validate and convert to URL
    orcid_id_pattern = r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$"
    if re.match(orcid_id_pattern, orcid_input):
        return f"https://orcid.org/{orcid_input}"

    logger.warning(f"Invalid ORCID format: {orcid_input}")
    return None


def get_orcid_affiliations(orcid_id: str, use_cache: bool = True) -> List[str]:
    """
    Fetch affiliations (organization names only) from ORCID.

    Args:
        orcid_id: ORCID identifier (e.g., "0000-0002-1126-1535")
        use_cache: Whether to use cached data (default: True)

    Returns:
        List of organization names from ORCID employment history

    Examples:
        >>> get_orcid_affiliations("0000-0002-1126-1535")
        ['EPFL - École Polytechnique Fédérale de Lausanne', 'Swiss Data Science Center']
    """
    try:
        from ..cache import get_cache_manager
        from ..parsers.users_parser import GitHubUsersParser
    except ImportError:
        # Fallback for when called outside package context
        from src.cache import get_cache_manager
        from src.parsers.users_parser import GitHubUsersParser

    if not orcid_id:
        return []

    # Normalize ORCID ID (remove URL if present)
    orcid_id = extract_orcid_id(orcid_id)
    if not orcid_id:
        logger.warning("Invalid ORCID ID format")
        return []

    def fetch_affiliations():
        """Fetch affiliations from ORCID"""
        parser = GitHubUsersParser()
        orcid_activities = parser._scrape_orcid_activities(orcid_id)

        if not orcid_activities or not orcid_activities.employment:
            return []

        # Extract only organization names, removing duplicates while preserving order
        affiliations = []
        seen = set()

        for employment in orcid_activities.employment:
            org_name = employment.organization
            # Clean up the organization name - remove location suffixes like ": Lausanne"
            if org_name and ":" in org_name:
                org_name = org_name.split(":")[0].strip()

            if org_name and org_name not in seen:
                affiliations.append(org_name)
                seen.add(org_name)

        return affiliations

    if use_cache:
        # Use cache manager for ORCID affiliations
        cache_manager = get_cache_manager()
        affiliations = cache_manager.get_cached_or_fetch(
            api_type="orcid",
            params={"orcid_id": orcid_id, "data_type": "affiliations"},
            fetch_func=fetch_affiliations,
            force_refresh=not use_cache,
        )
        return affiliations
    return fetch_affiliations()


def enrich_author_with_orcid(author: dict, use_cache: bool = True) -> dict:
    """
    Enrich author object with ORCID affiliations if orcidId is present.
    Also validates and normalizes ORCID ID to URL format.

    Args:
        author: Author dictionary with optional 'orcidId' field
        use_cache: Whether to use cached ORCID data (default: True)

    Returns:
        Author dictionary enriched with affiliations from ORCID and normalized ORCID URL

    Examples:
        >>> author = {
        ...     "name": "Cyril Matthey-Doret",
        ...     "orcidId": "0000-0002-1126-1535"  # Can be ID or URL
        ... }
        >>> enriched = enrich_author_with_orcid(author)
        >>> enriched['orcidId']  # Normalized to URL
        'https://orcid.org/0000-0002-1126-1535'
        >>> enriched['affiliation']
        ['EPFL - École Polytechnique Fédérale de Lausanne', 'Swiss Data Science Center']
    """
    if not isinstance(author, dict):
        return author

    # Support both plain 'orcidId' and Zod format 'md4i:orcidId'
    orcid_key = None
    orcid_input = None

    if "md4i:orcidId" in author:
        orcid_key = "md4i:orcidId"
        orcid_input = author.get("md4i:orcidId")
    elif "orcidId" in author:
        orcid_key = "orcidId"
        orcid_input = author.get("orcidId")

    if not orcid_input:
        return author

    # Normalize ORCID to URL format and validate
    normalized_orcid_url = normalize_orcid_to_url(orcid_input)
    if not normalized_orcid_url:
        logger.warning(
            f"Invalid ORCID format for author {author.get('name') or author.get('schema:name')}: {orcid_input}",
        )
        return author

    # Update the author object with the normalized URL
    author[orcid_key] = normalized_orcid_url

    # Extract ORCID ID from normalized URL for API calls
    orcid_id = extract_orcid_id(normalized_orcid_url)
    if not orcid_id:
        logger.warning(
            f"Could not extract ORCID ID from normalized URL: {normalized_orcid_url}",
        )
        return author

    # Get affiliations from ORCID
    orcid_affiliations = get_orcid_affiliations(orcid_id, use_cache=use_cache)

    if not orcid_affiliations:
        logger.warning(
            f"No ORCID affiliations found for {orcid_id} (author: {author.get('name') or author.get('schema:name')})",
        )
        return author
    logger.info(
        f"Found {len(orcid_affiliations)} ORCID affiliations for {orcid_id}: {orcid_affiliations}",
    )

    # Detect which affiliation key is used (Zod format uses 'schema:affiliation', plain uses 'affiliation')
    affiliation_key = None
    if "schema:affiliation" in author:
        affiliation_key = "schema:affiliation"
    elif "affiliation" in author:
        affiliation_key = "affiliation"
    else:
        # No existing affiliations, use plain 'affiliation' key
        affiliation_key = "affiliation"

    # Merge with existing affiliations (if any)
    existing_affiliations = author.get(affiliation_key, [])

    # Ensure existing_affiliations is a list
    if not isinstance(existing_affiliations, list):
        existing_affiliations = [existing_affiliations] if existing_affiliations else []

    # Merge affiliations, removing duplicates while preserving order
    merged_affiliations = list(existing_affiliations)  # Start with existing
    seen = set(
        aff.lower() for aff in existing_affiliations
    )  # Track what we have (case-insensitive)

    # Add ORCID affiliations that aren't already present
    added_count = 0
    for aff in orcid_affiliations:
        if aff.lower() not in seen:
            merged_affiliations.append(aff)
            seen.add(aff.lower())
            added_count += 1

    author[affiliation_key] = merged_affiliations

    if added_count > 0:
        logger.info(
            f"Enriched author {author.get('name') or author.get('schema:name')} with {added_count} new affiliations from ORCID (total: {len(merged_affiliations)})",
        )

    return author
