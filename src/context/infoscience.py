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


def _parse_author(item: Dict[str, Any]) -> Optional[InfoscienceAuthor]:
    """
    Parse a DSpace person entity into an InfoscienceAuthor model.

    Args:
        item: DSpace person item dictionary

    Returns:
        InfoscienceAuthor instance or None if parsing fails
    """
    metadata = item.get("metadata", {})
    uuid = item.get("uuid")
    handle = item.get("handle")

    # Get name - person entities typically use eperson.firstname + eperson.lastname
    # or dc.title for the full name
    name = _parse_metadata(metadata, "dc.title")
    if not name:
        # Try combining first and last name
        first_name = _parse_metadata(metadata, "eperson.firstname")
        last_name = _parse_metadata(metadata, "eperson.lastname")
        if first_name and last_name:
            name = f"{first_name} {last_name}"
        elif first_name:
            name = first_name
        elif last_name:
            name = last_name
    
    if not name:
        logger.warning(f"Could not extract name from person item with UUID {uuid}")
        return None

    # Build URL
    url = None
    if uuid:
        url = f"https://infoscience.epfl.ch/entities/person/{uuid}"
    elif handle:
        url = f"https://infoscience.epfl.ch/record/{handle}"

    return InfoscienceAuthor(
        uuid=uuid,
        name=name,
        email=_parse_metadata(metadata, "eperson.email"),
        orcid=_parse_metadata(metadata, "person.identifier.orcid"),
        affiliation=_parse_metadata(metadata, "person.affiliation.name"),
        url=url,
        publication_count=None,  # Not available from person entity directly
    )


def _parse_lab(item: Dict[str, Any]) -> Optional[InfoscienceLab]:
    """
    Parse a DSpace organizational unit entity into an InfoscienceLab model.

    Args:
        item: DSpace orgunit item dictionary

    Returns:
        InfoscienceLab instance or None if parsing fails
    """
    metadata = item.get("metadata", {})
    uuid = item.get("uuid")
    handle = item.get("handle")

    # Get name - orgunit entities typically use dc.title or organization.legalName
    name = _parse_metadata(metadata, "dc.title")
    if not name:
        name = _parse_metadata(metadata, "organization.legalName")
    if not name:
        name = _parse_metadata(metadata, "organization.name")
    
    if not name:
        logger.warning(f"Could not extract name from orgunit item with UUID {uuid}")
        return None

    # Build URL
    url = None
    if uuid:
        url = f"https://infoscience.epfl.ch/entities/orgunit/{uuid}"
    elif handle:
        url = f"https://infoscience.epfl.ch/record/{handle}"

    return InfoscienceLab(
        uuid=uuid,
        name=name,
        description=_parse_metadata(metadata, "dc.description") or _parse_metadata(metadata, "dc.description.abstract"),
        url=url,
        parent_organization=_parse_metadata(metadata, "organization.parentOrganization"),
        publication_count=None,  # Not available from orgunit entity directly
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
    
    Uses the /discover/search/objects endpoint with configuration=person
    to search the person index directly, just like the web UI.

    Args:
        name: Author name to search for
        max_results: Maximum number of results to return

    Returns:
        InfoscienceSearchResult with authors
    """
    authors = []
    total_results = 0

    # First, try searching the person configuration (like the web UI)
    logger.info(f"Searching for person profiles: {name}")
    person_params = {
        "query": name,
        "size": max_results,
        "configuration": "person",
    }
    
    person_response = await _make_api_request("/discover/search/objects", params=person_params)
    
    if person_response:
        search_result = person_response.get("_embedded", {}).get("searchResult", {})
        page_info = search_result.get("page", {})
        total_results = page_info.get("totalElements", 0)
        
        if total_results > 0:
            logger.info(f"Found {total_results} person profiles for: {name}")
            objects = search_result.get("_embedded", {}).get("objects", [])
            
            for obj in objects:
                try:
                    item = obj.get("_embedded", {}).get("indexableObject", {})
                    if item:
                        author = _parse_author(item)
                        if author:
                            authors.append(author)
                except Exception as e:
                    logger.warning(f"Error parsing person item: {e}")
                    continue
            
            logger.info(f"Found {len(authors)} authors for name: {name}")
            return InfoscienceSearchResult(
                total_results=total_results,
                page=1,
                results_per_page=max_results,
                authors=authors,
            )
    
    # Fallback: Search publications by author name and extract authors
    logger.info(f"No person profiles found, searching publications by author: {name}")
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
    
    First tries searching with configuration=orgunit (like the web UI for organizational units),
    then falls back to searching publications and extracting lab information from metadata.

    Args:
        name: Lab or organization name to search for
        max_results: Maximum number of results to return

    Returns:
        InfoscienceSearchResult with labs
    """
    labs = []
    lab_names_seen = set()
    total_results = 0
    
    # First, try searching the orgunit configuration (like the web UI)
    logger.info(f"Searching for organizational units: {name}")
    orgunit_params = {
        "query": name,
        "size": max_results,
        "configuration": "orgunit",
    }
    
    orgunit_response = await _make_api_request("/discover/search/objects", params=orgunit_params)
    
    if orgunit_response:
        search_result = orgunit_response.get("_embedded", {}).get("searchResult", {})
        page_info = search_result.get("page", {})
        total_results = page_info.get("totalElements", 0)
        
        if total_results > 0:
            logger.info(f"Found {total_results} organizational units for: {name}")
            objects = search_result.get("_embedded", {}).get("objects", [])
            
            for obj in objects:
                try:
                    item = obj.get("_embedded", {}).get("indexableObject", {})
                    if item:
                        lab = _parse_lab(item)
                        if lab:
                            labs.append(lab)
                            lab_names_seen.add(lab.name)
                except Exception as e:
                    logger.warning(f"Error parsing orgunit item: {e}")
                    continue
            
            logger.info(f"Found {len(labs)} labs for name: {name}")
            return InfoscienceSearchResult(
                total_results=total_results,
                page=1,
                results_per_page=max_results,
                labs=labs,
            )
    
    # Fallback: Search publications and extract lab information from metadata
    logger.info(f"No organizational units found, searching publications for lab info: {name}")
    params = {
        "query": name,
        "size": max_results,
        "configuration": "researchoutputs",
    }

    response = await _make_api_request("/discover/search/objects", params=params)

    if response:
        search_result = response.get("_embedded", {}).get("searchResult", {})
        page_info = search_result.get("page", {})
        total_results = page_info.get("totalElements", 0)

        objects = search_result.get("_embedded", {}).get("objects", [])

        # Extract labs from publications
        for obj in objects:
            try:
                item = obj.get("_embedded", {}).get("indexableObject", {})
                metadata = item.get("metadata", {})
                
                # Try to find lab information in various metadata fields
                lab_info = _parse_metadata(metadata, "dc.contributor.lab")
                if not lab_info:
                    lab_info = _parse_metadata(metadata, "dc.contributor.unit")
                if not lab_info:
                    lab_info = _parse_metadata(metadata, "dc.contributor.affiliation")
                
                # If we found lab info and it matches the search query
                if lab_info and name.lower() in lab_info.lower():
                    if lab_info not in lab_names_seen:
                        lab_names_seen.add(lab_info)
                        
                        # Get publication title for context
                        pub_title = _parse_metadata(metadata, "dc.title")
                        description = f"Lab identified from publication: {pub_title[:100] if pub_title else 'N/A'}..."
                        
                        lab = InfoscienceLab(
                            name=lab_info,
                            description=description,
                            publication_count=1,  # At least one publication
                        )
                        labs.append(lab)
                        
                        if len(labs) >= max_results:
                            break
                            
            except Exception as e:
                logger.warning(f"Error extracting lab from publication: {e}")
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


async def get_entity_by_uuid(
    uuid: str,
    entity_type: Optional[str] = None,
) -> Optional[Dict[str, Any]]:
    """
    Get an entity directly by its UUID.
    
    This function supports direct access to entities using their UUID.
    Useful for accessing specific publications, persons, or organizational units
    when you already know the UUID (e.g., from user-provided URLs).
    
    Args:
        uuid: The UUID of the entity
        entity_type: Optional hint about entity type ("publication", "person", "orgunit")
                    If not provided, will try /core/items/{uuid}
    
    Returns:
        Raw entity data as dictionary, or None if not found
        
    Example URLs:
        - https://infoscience.epfl.ch/entities/publication/{uuid}
        - https://infoscience.epfl.ch/entities/person/{uuid}
        - https://infoscience.epfl.ch/entities/orgunit/{uuid}
    """
    logger.info(f"Fetching entity by UUID: {uuid} (type: {entity_type or 'auto'})")
    
    # Try entity-specific endpoint if type is known
    if entity_type:
        response = await _make_api_request(f"/entities/{entity_type}/{uuid}")
        if response:
            return response
            
    # Fallback to generic items endpoint
    response = await _make_api_request(f"/core/items/{uuid}")
    if response:
        return response
        
    logger.warning(f"Entity not found for UUID: {uuid}")
    return None


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

