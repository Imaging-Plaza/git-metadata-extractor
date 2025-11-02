"""
Infoscience API Client and PydanticAI Tool Functions

Provides async functions to query EPFL's Infoscience repository (DSpace 7.6)
for publications, authors, labs, and organizational units.
"""

import logging
import os
from typing import Dict, List, Optional, Any

import httpx

from ..data_models.infoscience import (
    InfoscienceAuthor,
    InfoscienceLab,
    InfosciencePublication,
    InfoscienceSearchResult,
)

logger = logging.getLogger(__name__)

# Configuration
INFOSCIENCE_BASE_URL = "https://infoscience.epfl.ch/server/api"
DEFAULT_MAX_RESULTS = 10
REQUEST_TIMEOUT = 30

# Authentication token (optional, for protected endpoints)
INFOSCIENCE_TOKEN = os.getenv("INFOSCIENCE_TOKEN")

# Simple in-memory cache to prevent duplicate searches in same session
_search_cache: Dict[str, str] = {}


##########################################################
# HTTP Client Functions
##########################################################


async def _make_api_request(
    endpoint: str,
    params: Optional[Dict[str, Any]] = None,
    timeout: int = REQUEST_TIMEOUT,
    use_auth: bool = False,
) -> Optional[Dict[str, Any]]:
    """
    Make an async HTTP request to the Infoscience API.

    Args:
        endpoint: API endpoint path (relative to base URL)
        params: Query parameters
        timeout: Request timeout in seconds
        use_auth: Whether to include authentication token if available

    Returns:
        JSON response as dictionary or None on error
    """
    url = f"{INFOSCIENCE_BASE_URL}{endpoint}"
    
    # Prepare headers
    headers = {}
    if use_auth and INFOSCIENCE_TOKEN:
        headers["Authorization"] = f"Bearer {INFOSCIENCE_TOKEN}"
        logger.debug("Using authentication token for request")

    try:
        async with httpx.AsyncClient(timeout=timeout) as client:
            logger.debug(f"Making API request to {url} with params {params}")
            response = await client.get(url, params=params, headers=headers)
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error {e.response.status_code} for {url}: {e}")
        return None
    except httpx.TimeoutException:
        logger.error(f"Request timeout for {url}")
        return None
    except Exception as e:
        logger.error(f"Error making API request to {url}: {e}", exc_info=True)
        return None


def _parse_metadata(metadata: Dict[str, Any], field: str) -> Optional[str]:
    """
    Extract a single metadata field value from DSpace metadata structure.

    Args:
        metadata: Metadata dictionary
        field: Field name (e.g., 'dc.title')

    Returns:
        First value of the field or None
    """
    values = metadata.get(field, [])
    if values and isinstance(values, list) and len(values) > 0:
        return values[0].get("value")
    return None


def _parse_metadata_list(metadata: Dict[str, Any], field: str) -> List[str]:
    """
    Extract multiple metadata field values from DSpace metadata structure.

    Args:
        metadata: Metadata dictionary
        field: Field name (e.g., 'dc.contributor.author')

    Returns:
        List of values
    """
    values = metadata.get(field, [])
    if isinstance(values, list):
        return [v.get("value") for v in values if v.get("value")]
    return []


def _parse_publication(item: Dict[str, Any]) -> InfosciencePublication:
    """
    Parse a DSpace item into an InfosciencePublication model.

    Args:
        item: DSpace item dictionary

    Returns:
        InfosciencePublication instance
    """
    metadata = item.get("metadata", {})
    uuid = item.get("uuid")
    handle = item.get("handle")

    # Build URL
    url = None
    if handle:
        url = f"https://infoscience.epfl.ch/record/{handle}"
    elif uuid:
        url = f"https://infoscience.epfl.ch/server/api/core/items/{uuid}"

    # Extract repository URL from relations or identifiers
    repository_url = None
    relations = _parse_metadata_list(metadata, "dc.relation.uri")
    for rel in relations:
        if "github.com" in rel.lower() or "gitlab" in rel.lower():
            repository_url = rel
            break

    return InfosciencePublication(
        uuid=uuid,
        title=_parse_metadata(metadata, "dc.title") or "Untitled",
        authors=_parse_metadata_list(metadata, "dc.contributor.author"),
        abstract=_parse_metadata(metadata, "dc.description.abstract"),
        doi=_parse_metadata(metadata, "dc.identifier.doi"),
        publication_date=_parse_metadata(metadata, "dc.date.issued"),
        publication_type=_parse_metadata(metadata, "dc.type"),
        url=url,
        repository_url=repository_url,
        lab=_parse_metadata(metadata, "dc.contributor.affiliation"),
        subjects=_parse_metadata_list(metadata, "dc.subject"),
    )


async def search_publications(
    query: str,
    max_results: int = DEFAULT_MAX_RESULTS,
    search_field: Optional[str] = None,
) -> InfoscienceSearchResult:
    """
    Search for publications in Infoscience.

    Args:
        query: Search query (can be title, DOI, keywords, etc.)
        max_results: Maximum number of results to return
        search_field: Specific field to search (e.g., 'dc.title', 'dc.identifier.doi')
                     If None, performs a general search

    Returns:
        InfoscienceSearchResult with publications
    """
    # Build query string based on field
    if search_field:
        query_str = f"{search_field}:{query}"
    else:
        # General search - try multiple fields
        query_str = query

    params = {
        "query": query_str,
        "size": max_results,
        "configuration": "researchoutputs",
    }

    response = await _make_api_request("/discover/search/objects", params=params)

    if not response:
        logger.warning(f"No response from publication search for query: {query}")
        return InfoscienceSearchResult(
            total_results=0,
            page=1,
            results_per_page=max_results,
        )

    # Parse response
    search_result = response.get("_embedded", {}).get("searchResult", {})
    page_info = search_result.get("page", {})
    total_results = page_info.get("totalElements", 0)

    # Parse publications
    publications = []
    objects = search_result.get("_embedded", {}).get("objects", [])

    for obj in objects:
        try:
            item = obj.get("_embedded", {}).get("indexableObject", {})
            if item:
                pub = _parse_publication(item)
                publications.append(pub)
        except Exception as e:
            logger.warning(f"Error parsing publication item: {e}")
            continue

    logger.info(f"Found {len(publications)} publications for query: {query}")

    return InfoscienceSearchResult(
        total_results=total_results,
        page=1,
        results_per_page=max_results,
        publications=publications,
    )


async def search_authors(
    name: str,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> InfoscienceSearchResult:
    """
    Search for authors/researchers in Infoscience.

    Args:
        name: Author name to search for
        max_results: Maximum number of results to return

    Returns:
        InfoscienceSearchResult with authors
    """
    # Try searching in profiles first
    params = {
        "query": name,
        "size": max_results,
    }

    # First try the profiles endpoint
    response = await _make_api_request("/eperson/profiles/search/byName", params=params)

    authors = []
    total_results = 0

    if response:
        # Parse profiles response
        page_info = response.get("page", {})
        total_results = page_info.get("totalElements", 0)

        profiles = response.get("_embedded", {}).get("profiles", [])

        for profile in profiles:
            try:
                uuid = profile.get("id")
                full_name = profile.get("fullName") or profile.get("name", "Unknown")
                email = profile.get("email")
                orcid = profile.get("orcid")

                # Get profile URL
                profile_url = None
                self_link = profile.get("_links", {}).get("self", {}).get("href")
                if self_link:
                    profile_url = self_link.replace("/server/api/", "/")

                author = InfoscienceAuthor(
                    uuid=uuid,
                    name=full_name,
                    email=email,
                    orcid=orcid,
                    profile_url=profile_url,
                )
                authors.append(author)
            except Exception as e:
                logger.warning(f"Error parsing author profile: {e}")
                continue

    # If no results, try searching in publications by author name
    if not authors:
        logger.info(f"No profiles found, searching publications by author: {name}")
        pub_params = {
            "query": f"dc.contributor.author:{name}",
            "size": max_results,
            "configuration": "researchoutputs",
        }

        pub_response = await _make_api_request("/discover/search/objects", params=pub_params)

        if pub_response:
            search_result = pub_response.get("_embedded", {}).get("searchResult", {})
            page_info = search_result.get("page", {})
            total_results = page_info.get("totalElements", 0)

            # Extract unique authors from publications
            author_names = set()
            objects = search_result.get("_embedded", {}).get("objects", [])

            for obj in objects:
                try:
                    item = obj.get("_embedded", {}).get("indexableObject", {})
                    metadata = item.get("metadata", {})
                    pub_authors = _parse_metadata_list(metadata, "dc.contributor.author")

                    # Find authors matching the search name
                    for author_name in pub_authors:
                        if name.lower() in author_name.lower():
                            if author_name not in author_names:
                                author_names.add(author_name)
                                authors.append(
                                    InfoscienceAuthor(
                                        name=author_name,
                                        publication_count=1,  # Approximate
                                    )
                                )
                except Exception as e:
                    logger.warning(f"Error extracting authors from publication: {e}")
                    continue

    logger.info(f"Found {len(authors)} authors for name: {name}")

    return InfoscienceSearchResult(
        total_results=total_results,
        page=1,
        results_per_page=max_results,
        authors=authors,
    )


async def search_labs(
    name: str,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> InfoscienceSearchResult:
    """
    Search for labs and organizational units in Infoscience.
    
    Note: Lab/community search uses the general discover endpoint since
    specific community/collection search endpoints may not be available
    or may require authentication.

    Args:
        name: Lab or organization name to search for
        max_results: Maximum number of results to return

    Returns:
        InfoscienceSearchResult with labs
    """
    # Use the general discover search endpoint with a query for organizational units
    # This searches across all content types
    params = {
        "query": name,
        "size": max_results,
        "dsoType": "community",  # Filter for communities (often represent labs/orgs)
    }

    response = await _make_api_request("/discover/search/objects", params=params, use_auth=True)

    labs = []
    total_results = 0

    if response:
        search_result = response.get("_embedded", {}).get("searchResult", {})
        page_info = search_result.get("page", {})
        total_results = page_info.get("totalElements", 0)

        objects = search_result.get("_embedded", {}).get("objects", [])

        for obj in objects:
            try:
                item = obj.get("_embedded", {}).get("indexableObject", {})
                uuid = item.get("uuid")
                metadata = item.get("metadata", {})
                
                lab_name = _parse_metadata(metadata, "dc.title") or item.get("name", "Unknown")
                description = _parse_metadata(metadata, "dc.description")

                # Build URL
                url = None
                handle = item.get("handle")
                if handle:
                    url = f"https://infoscience.epfl.ch/handle/{handle}"

                lab = InfoscienceLab(
                    uuid=uuid,
                    name=lab_name,
                    description=description,
                    url=url,
                )
                labs.append(lab)
            except Exception as e:
                logger.warning(f"Error parsing lab/community: {e}")
                continue

    # If no communities found, try searching collections
    if len(labs) == 0:
        logger.debug("No communities found, trying collections")
        params["dsoType"] = "collection"
        
        coll_response = await _make_api_request("/discover/search/objects", params=params, use_auth=True)

        if coll_response:
            search_result = coll_response.get("_embedded", {}).get("searchResult", {})
            if total_results == 0:
                page_info = search_result.get("page", {})
                total_results = page_info.get("totalElements", 0)
            
            objects = search_result.get("_embedded", {}).get("objects", [])

            for obj in objects[:max_results]:
                try:
                    item = obj.get("_embedded", {}).get("indexableObject", {})
                    uuid = item.get("uuid")
                    metadata = item.get("metadata", {})

                    lab_name = _parse_metadata(metadata, "dc.title") or item.get("name", "Unknown")
                    description = _parse_metadata(metadata, "dc.description")

                    # Build URL
                    url = None
                    handle = item.get("handle")
                    if handle:
                        url = f"https://infoscience.epfl.ch/handle/{handle}"

                    lab = InfoscienceLab(
                        uuid=uuid,
                        name=lab_name,
                        description=description,
                        url=url,
                    )
                    labs.append(lab)
                except Exception as e:
                    logger.warning(f"Error parsing collection: {e}")
                    continue

    logger.info(f"Found {len(labs)} labs/organizations for name: {name}")

    return InfoscienceSearchResult(
        total_results=total_results,
        page=1,
        results_per_page=max_results,
        labs=labs,
    )


async def get_author_publications(
    author_name: str,
    max_results: int = DEFAULT_MAX_RESULTS,
) -> InfoscienceSearchResult:
    """
    Get all publications by a specific author.

    Args:
        author_name: Full or partial name of the author
        max_results: Maximum number of results to return

    Returns:
        InfoscienceSearchResult with publications
    """
    logger.info(f"Fetching publications for author: {author_name}")

    # Search publications by author
    return await search_publications(
        query=author_name,
        max_results=max_results,
        search_field="dc.contributor.author",
    )


##########################################################
# PydanticAI Tool Functions
##########################################################


async def search_infoscience_publications_tool(query: str, max_results: int = 10) -> str:
    """
    Search for publications in EPFL's Infoscience repository.

    This tool searches for academic publications, papers, theses, and other research outputs.
    You can search by title, DOI, keywords, or general terms.
    
    IMPORTANT: This tool caches results - don't search for the same thing multiple times!
    Be strategic and avoid redundant searches.

    Args:
        query: Search query (title, DOI, keywords, or general search terms)
        max_results: Maximum number of results to return (default: 10, max: 50)

    Returns:
        Markdown-formatted search results with publication details
    """
    logger.info(f"🔍 Agent tool called: search_infoscience_publications_tool(query='{query}', max_results={max_results})")
    
    # Check cache first to avoid duplicate searches
    cache_key = f"pub:{query.lower()}:{max_results}"
    if cache_key in _search_cache:
        logger.info(f"⚡ Returning cached result for query: '{query}'")
        return _search_cache[cache_key]
    
    max_results = min(max_results, 50)  # Cap at 50

    try:
        result = await search_publications(query, max_results)
        logger.info(f"✓ Infoscience publications search returned {result.total_results} total results")
        markdown_result = result.to_markdown()
        
        # Cache the result
        _search_cache[cache_key] = markdown_result
        
        return markdown_result
    except Exception as e:
        logger.error(f"✗ Error in search_infoscience_publications_tool: {e}", exc_info=True)
        return f"Error searching publications: {e}"


async def search_infoscience_authors_tool(name: str, max_results: int = 10) -> str:
    """
    Search for authors and researchers in EPFL's Infoscience repository.

    This tool finds researchers, professors, and other authors affiliated with EPFL.
    Use it to find information about specific people and their publications.
    
    IMPORTANT: This tool caches results - don't search for the same person multiple times!
    Be strategic and avoid redundant searches.

    Args:
        name: Author name to search for (can be partial name)
        max_results: Maximum number of results to return (default: 10, max: 50)

    Returns:
        Markdown-formatted search results with author details
    """
    logger.info(f"🔍 Agent tool called: search_infoscience_authors_tool(name='{name}', max_results={max_results})")
    
    # Check cache first
    cache_key = f"author:{name.lower()}:{max_results}"
    if cache_key in _search_cache:
        logger.info(f"⚡ Returning cached result for author: '{name}'")
        return _search_cache[cache_key]
    
    max_results = min(max_results, 50)  # Cap at 50

    try:
        result = await search_authors(name, max_results)
        logger.info(f"✓ Infoscience authors search returned {result.total_results} total results")
        markdown_result = result.to_markdown()
        
        # Cache the result
        _search_cache[cache_key] = markdown_result
        
        return markdown_result
    except Exception as e:
        logger.error(f"✗ Error in search_infoscience_authors_tool: {e}", exc_info=True)
        return f"Error searching authors: {e}"


async def search_infoscience_labs_tool(name: str, max_results: int = 10) -> str:
    """
    Search for laboratories and organizational units in EPFL's Infoscience repository.

    This tool finds research labs, groups, departments, and other organizational units at EPFL.
    Use it to find information about specific labs and their research areas.
    
    IMPORTANT: This tool caches results - don't search for the same lab multiple times!
    If a lab isn't found, it may not be in Infoscience or has a different name - don't keep trying!

    Args:
        name: Lab or organization name to search for (can be partial name)
        max_results: Maximum number of results to return (default: 10, max: 50)

    Returns:
        Markdown-formatted search results with lab details
    """
    logger.info(f"🔍 Agent tool called: search_infoscience_labs_tool(name='{name}', max_results={max_results})")
    
    # Check cache first
    cache_key = f"lab:{name.lower()}:{max_results}"
    if cache_key in _search_cache:
        logger.info(f"⚡ Returning cached result for lab: '{name}'")
        return _search_cache[cache_key]
    
    max_results = min(max_results, 50)  # Cap at 50

    try:
        result = await search_labs(name, max_results)
        logger.info(f"✓ Infoscience labs search returned {result.total_results} total results")
        markdown_result = result.to_markdown()
        
        # Cache the result (including empty results!)
        _search_cache[cache_key] = markdown_result
        
        return markdown_result
    except Exception as e:
        logger.error(f"✗ Error in search_infoscience_labs_tool: {e}", exc_info=True)
        return f"Error searching labs: {e}"


async def get_author_publications_tool(author_name: str, max_results: int = 10) -> str:
    """
    Get publications by a specific author from EPFL's Infoscience repository.

    This tool retrieves all publications authored by a specific person.
    Use it to get a comprehensive list of someone's research outputs.
    
    IMPORTANT: This tool caches results - don't search for the same author multiple times!

    Args:
        author_name: Full or partial name of the author
        max_results: Maximum number of results to return (default: 10, max: 50)

    Returns:
        Markdown-formatted list of publications by the author
    """
    logger.info(f"🔍 Agent tool called: get_author_publications_tool(author_name='{author_name}', max_results={max_results})")
    
    # Check cache first
    cache_key = f"author_pubs:{author_name.lower()}:{max_results}"
    if cache_key in _search_cache:
        logger.info(f"⚡ Returning cached publications for author: '{author_name}'")
        return _search_cache[cache_key]
    
    max_results = min(max_results, 50)  # Cap at 50

    try:
        result = await get_author_publications(author_name, max_results)
        if result.total_results > 0:
            logger.info(f"✓ Found {result.total_results} publications for author '{author_name}'")
            markdown_result = f"## Publications by {author_name}\n\n" + result.to_markdown()
        else:
            logger.info(f"⚠ No publications found for author '{author_name}'")
            markdown_result = f"No publications found for author: {author_name}"
        
        # Cache the result
        _search_cache[cache_key] = markdown_result
        
        return markdown_result
    except Exception as e:
        logger.error(f"✗ Error in get_author_publications_tool: {e}", exc_info=True)
        return f"Error fetching author publications: {e}"

