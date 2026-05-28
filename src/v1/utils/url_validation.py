"""
URL validation utilities for LLM-generated content.
"""

import logging
import re
from typing import Any, Dict, Optional
from urllib.parse import urlparse

logger = logging.getLogger(__name__)


ORCID_ID_PATTERN = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")
ORCID_URL_PATTERN = re.compile(
    r"^https?://orcid\.org/(\d{4}-\d{4}-\d{4}-\d{3}[\dX])/?$",
    flags=re.IGNORECASE,
)


def _is_valid_orcid_checksum(orcid_id: str) -> bool:
    """
    Validate ORCID checksum using ISO 7064 MOD 11-2.

    Args:
        orcid_id: ORCID identifier in canonical form (XXXX-XXXX-XXXX-XXXX)

    Returns:
        True if checksum is valid, False otherwise
    """
    digits = orcid_id.replace("-", "")
    if len(digits) != 16:
        return False

    total = 0
    for char in digits[:15]:
        if not char.isdigit():
            return False
        total = (total + int(char)) * 2

    remainder = total % 11
    result = (12 - remainder) % 11
    expected_check_digit = "X" if result == 10 else str(result)

    return digits[-1] == expected_check_digit


def normalize_orcid_id(orcid: Any) -> Optional[str]:
    """
    Normalize ORCID to canonical ID form (XXXX-XXXX-XXXX-XXXX).

    Accepts both ID and URL input, validates pattern and checksum, and
    returns None for invalid values.
    """
    if not orcid:
        return None

    if hasattr(orcid, "__str__"):
        value = str(orcid).strip()
    elif isinstance(orcid, str):
        value = orcid.strip()
    else:
        return None

    if not value:
        return None

    match = ORCID_URL_PATTERN.match(value)
    if match:
        candidate = match.group(1).upper()
    else:
        candidate = value.upper()

    if not ORCID_ID_PATTERN.match(candidate):
        return None

    if not _is_valid_orcid_checksum(candidate):
        return None

    return candidate


def normalize_orcid_url(orcid: Any) -> Optional[str]:
    """Normalize ORCID input to canonical URL form."""
    normalized_id = normalize_orcid_id(orcid)
    if not normalized_id:
        return None
    return f"https://orcid.org/{normalized_id}"


def is_valid_url(url: Any) -> bool:
    """
    Validate if a URL is properly formatted and accessible.

    Args:
        url: URL to validate (string, HttpUrl, or other)

    Returns:
        True if valid URL, False otherwise
    """
    if not url:
        return False

    # Handle Pydantic HttpUrl objects
    if hasattr(url, "__str__"):
        url = str(url)
    elif not isinstance(url, str):
        return False

    # Remove whitespace
    url = url.strip()

    # Check for common malformed URL patterns
    if _is_malformed_url(url):
        return False

    # Check basic URL format
    try:
        result = urlparse(url)
        if not result.scheme or not result.netloc:
            return False
        if result.scheme not in ("http", "https"):
            return False

        # Check for relative URLs (missing scheme or netloc)
        if not result.scheme or not result.netloc:
            return False

        # Check for common malformed patterns
        if result.netloc.startswith(".") or result.netloc.endswith("."):
            return False

        return True
    except Exception:
        return False


def _is_malformed_url(url: str) -> bool:
    """
    Check for common malformed URL patterns.

    Args:
        url: URL string to check

    Returns:
        True if malformed, False otherwise
    """
    # Check for common malformed patterns
    malformed_patterns = [
        r"^https?://$",  # Just scheme
        r"^https?:///$",  # Just scheme and slash
        r"^https?://\s+",  # Whitespace after scheme
        r"^\s+https?://",  # Whitespace before scheme
        r"https?://[^/]*\s+",  # Whitespace in domain
        r"https?://[^/]*\.$",  # Domain ending with dot
        r"https?://\.",  # Domain starting with dot
        r"https?://[^/]*\.\.",  # Double dots in domain
        r"https?://[^/]*//",  # Double slashes in path
        r"https?://[^/]*\?\?",  # Double question marks
        r"https?://[^/]*##",  # Double hash marks
    ]

    for pattern in malformed_patterns:
        if re.search(pattern, url):
            return True

    return False


def is_valid_orcid_url(url: Any) -> bool:
    """
    Validate ORCID URL format.

    Args:
        url: URL to validate

    Returns:
        True if valid ORCID URL, False otherwise
    """
    return normalize_orcid_id(url) is not None


def is_valid_ror_url(url: Any) -> bool:
    """
    Validate ROR URL format.

    Args:
        url: URL to validate

    Returns:
        True if valid ROR URL, False otherwise
    """
    if not url:
        return False

    # Handle Pydantic HttpUrl objects
    if hasattr(url, "__str__"):
        url = str(url)
    elif not isinstance(url, str):
        return False

    url = url.strip()

    # Check if it's a ROR URL
    return url.startswith("https://ror.org/") and len(url) > 20


def is_valid_registry_url(url: Any) -> bool:
    """
    Validate container registry URL format.

    Args:
        url: URL to validate

    Returns:
        True if valid registry URL, False otherwise
    """
    if not url:
        return False

    # Handle Pydantic HttpUrl objects
    if hasattr(url, "__str__"):
        url = str(url)
    elif not isinstance(url, str):
        return False

    url = url.strip()

    # Common container registry patterns
    registry_patterns = [
        # Docker Hub (docker.io) - supports tags with colons
        r"^https?://(?:hub\.)?docker\.io/(?:r/)?[a-zA-Z0-9._/-]+(:[a-zA-Z0-9._-]+)?$",
        # GitHub Container Registry (ghcr.io) - supports tags with colons
        r"^https?://ghcr\.io/[a-zA-Z0-9._/-]+(:[a-zA-Z0-9._-]+)?$",
        # Quay.io - supports tags with colons
        r"^https?://quay\.io/[a-zA-Z0-9._/-]+(:[a-zA-Z0-9._-]+)?$",
        # Amazon ECR - supports tags with colons
        r"^https?://[0-9]+\.dkr\.ecr\.[a-z0-9-]+\.amazonaws\.com/[a-zA-Z0-9._/-]+(:[a-zA-Z0-9._-]+)?$",
        # Google Container Registry - supports tags with colons
        r"^https?://gcr\.io/[a-zA-Z0-9._/-]+(:[a-zA-Z0-9._-]+)?$",
        r"^https?://[a-z0-9-]+\.gcr\.io/[a-zA-Z0-9._/-]+(:[a-zA-Z0-9._-]+)?$",
        # Azure Container Registry - supports tags with colons
        r"^https?://[a-zA-Z0-9-]+\.azurecr\.io/[a-zA-Z0-9._/-]+(:[a-zA-Z0-9._-]+)?$",
        # Harbor registries
        r"^https?://[a-zA-Z0-9.-]+/harbor/projects/[0-9]+/repositories/[a-zA-Z0-9._/-]+(:[a-zA-Z0-9._-]+)?$",
        # JFrog Artifactory
        r"^https?://[a-zA-Z0-9.-]+/artifactory/[a-zA-Z0-9._/-]+(:[a-zA-Z0-9._-]+)?$",
        # Nexus registries
        r"^https?://[a-zA-Z0-9.-]+/repository/[a-zA-Z0-9._/-]+(:[a-zA-Z0-9._-]+)?$",
        # Generic registry pattern (fallback) - supports tags with colons
        r"^https?://[a-zA-Z0-9.-]+:[0-9]+/[a-zA-Z0-9._/-]+(:[a-zA-Z0-9._-]+)?$",
        r"^https?://[a-zA-Z0-9.-]+/[a-zA-Z0-9._/-]+(:[a-zA-Z0-9._-]+)?$",
    ]

    for pattern in registry_patterns:
        if re.match(pattern, url):
            return True

    return False


def validate_and_clean_urls(data: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate and clean URLs in a dictionary, converting invalid URLs to None.

    Args:
        data: Dictionary containing potential URLs

    Returns:
        Dictionary with validated URLs (invalid ones set to None)
    """
    cleaned_data = data.copy()

    # Fields that should contain URLs
    url_fields = [
        "url",
        "readme",
        "hasDocumentation",
        "isBasedOn",
        "identifier",
        "hasExecutableInstructions",
    ]

    # Fields that should contain lists of URLs
    url_list_fields = [
        "codeRepository",
        "citation",
    ]

    # Validate single URL fields
    for field in url_fields:
        if field in cleaned_data and cleaned_data[field] is not None:
            url_value = cleaned_data[field]
            # Skip empty strings - they're valid "no URL" values
            if isinstance(url_value, str) and url_value.strip() == "":
                cleaned_data[field] = None
                continue

            if not is_valid_url(url_value):
                logger.warning(f"Invalid URL in {field}: {url_value!r}")
                cleaned_data[field] = None

    # Validate URL list fields
    for field in url_list_fields:
        if field in cleaned_data and cleaned_data[field] is not None:
            if isinstance(cleaned_data[field], list):
                valid_urls = []
                for url in cleaned_data[field]:
                    # Skip empty strings - they're valid "no URL" values
                    if isinstance(url, str) and url.strip() == "":
                        continue
                    if url is not None and is_valid_url(url):
                        valid_urls.append(url)
                    elif url is not None:
                        logger.warning(f"Invalid URL in {field}: {url!r}")
                cleaned_data[field] = valid_urls if valid_urls else None
            else:
                logger.warning(
                    f"Expected list for {field}, got {type(cleaned_data[field])}",
                )
                cleaned_data[field] = None

    return cleaned_data


def validate_author_urls(author: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate URLs in author data, especially ORCID IDs.

    Args:
        author: Author dictionary

    Returns:
        Author dictionary with validated URLs
    """
    cleaned_author = author.copy()

    # Validate ORCID and normalize to canonical ID format.
    if "orcid" in cleaned_author and cleaned_author["orcid"] is not None:
        original_orcid = cleaned_author["orcid"]
        normalized_orcid = normalize_orcid_id(original_orcid)
        if normalized_orcid is None:
            logger.warning(f"Invalid ORCID format: {original_orcid}")
            cleaned_author["orcid"] = None
        else:
            cleaned_author["orcid"] = normalized_orcid

    return cleaned_author


def validate_organization_urls(org: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate URLs in organization data, especially ROR IDs.

    Args:
        org: Organization dictionary

    Returns:
        Organization dictionary with validated URLs
    """
    cleaned_org = org.copy()

    # Validate ROR ID
    if "hasRorId" in cleaned_org and cleaned_org["hasRorId"] is not None:
        ror_id = cleaned_org["hasRorId"]

        if hasattr(ror_id, "__str__"):
            ror_id = str(ror_id)

        if isinstance(ror_id, str) and ror_id.strip():
            ror_id = ror_id.strip()
            if not is_valid_ror_url(ror_id):
                logger.warning(f"Invalid ROR ID format: {ror_id}")
                cleaned_org["hasRorId"] = None
        else:
            cleaned_org["hasRorId"] = None

    # Validate website
    if "website" in cleaned_org and cleaned_org["website"] is not None:
        if not is_valid_url(cleaned_org["website"]):
            logger.warning(f"Invalid website URL: {cleaned_org['website']}")
            cleaned_org["website"] = None

    return cleaned_org


def validate_software_image_urls(image: Dict[str, Any]) -> Dict[str, Any]:
    """
    Validate URLs in software image data.

    Args:
        image: Software image dictionary

    Returns:
        Software image dictionary with validated URLs
    """
    cleaned_image = image.copy()

    # Validate registry URL
    if (
        "availableInRegistry" in cleaned_image
        and cleaned_image["availableInRegistry"] is not None
    ):
        if not is_valid_registry_url(cleaned_image["availableInRegistry"]):
            logger.warning(
                f"Invalid registry URL: {cleaned_image['availableInRegistry']}",
            )
            cleaned_image["availableInRegistry"] = None

    return cleaned_image
