"""
GitHub Token Validation Dependency

FastAPI dependency for validating GitHub tokens and retrieving rate limit information.
"""

import logging
import os
from datetime import datetime

import requests
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)


async def validate_github_token() -> dict:
    """
    Validate GitHub token and retrieve rate limit information.

    This dependency:
    - Checks if GITHUB_TOKEN is configured
    - Validates the token by calling GitHub API
    - Retrieves current rate limit information
    - Returns rate limit data for logging and response inclusion

    Returns:
        dict with:
        - valid: bool - Token is valid
        - rate_limit_limit: int - Total rate limit
        - rate_limit_remaining: int - Remaining requests
        - rate_limit_reset: datetime - When rate limit resets

    Raises:
        HTTPException 401 if token is missing, invalid, or expired
    """
    raw_token = os.environ.get("GITHUB_TOKEN")

    # `GITHUB_TOKEN` may be a comma-separated list of tokens used by the
    # rotation client (src/index/github/api.py). The validator only needs one
    # working token to confirm the deployment is configured, so pick the first
    # non-empty entry. Passing the raw comma-joined string to GitHub returns
    # 401 and blocks every authenticated route.
    token = next(
        (
            part.strip()
            for part in (raw_token or "").split(",")
            if part.strip()
        ),
        None,
    )

    # Check if token is configured
    if not token:
        logger.error("GitHub token not configured")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="GitHub token not configured. Set GITHUB_TOKEN environment variable.",
        )

    # Validate token by calling GitHub API rate limit endpoint
    headers = {
        "Authorization": f"token {token}",
        "Accept": "application/vnd.github.v3+json",
        "User-Agent": "GitMetadataExtractor/2.0",
    }

    try:
        response = requests.get(
            "https://api.github.com/rate_limit",
            headers=headers,
            timeout=10,
        )

        # If 401, token is invalid or expired
        if response.status_code == 401:
            logger.error("GitHub token is invalid or expired")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="GitHub token is invalid or expired. Please update GITHUB_TOKEN environment variable.",
            )

        # If other error status
        if response.status_code != 200:
            logger.error(f"GitHub API returned status {response.status_code}")
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail=f"GitHub API returned unexpected status: {response.status_code}",
            )

        # Extract rate limit information
        rate_data = response.json()
        core_rate = rate_data.get("rate", {})

        rate_limit = core_rate.get("limit", 0)
        rate_remaining = core_rate.get("remaining", 0)
        rate_reset_timestamp = core_rate.get("reset", 0)
        rate_reset = datetime.fromtimestamp(rate_reset_timestamp)

        # Log rate limit information
        logger.info(
            f"GitHub API rate limit: {rate_remaining}/{rate_limit} remaining, "
            f"resets at {rate_reset.isoformat()}",
        )

        # Warning when rate limit is low
        if rate_remaining < 100:
            logger.warning(
                f"⚠️  GitHub API rate limit low: only {rate_remaining} requests remaining! "
                f"Resets at {rate_reset.isoformat()}",
            )

        return {
            "valid": True,
            "rate_limit_limit": rate_limit,
            "rate_limit_remaining": rate_remaining,
            "rate_limit_reset": rate_reset,
        }

    except requests.RequestException as e:
        logger.error(f"Failed to validate GitHub token: {e}")
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail=f"Failed to connect to GitHub API: {e!s}",
        )
