"""
Cache configuration settings and environment variables.
"""

import os
from typing import Any, Dict

# Cache configuration
CACHE_CONFIG = {
    # Default TTL settings (in days) - set to 1 year for essentially permanent storage
    "default_ttl_days": int(os.environ.get("CACHE_DEFAULT_TTL_DAYS", "365")),
    # API-specific TTL overrides (in days)
    # Note: Cache is only refreshed when force_refresh=true is used
    "api_ttl_overrides": {
        "github_user": int(os.environ.get("CACHE_GITHUB_USER_TTL_DAYS", "365")),
        "github_org": int(os.environ.get("CACHE_GITHUB_ORG_TTL_DAYS", "365")),
        "orcid": int(os.environ.get("CACHE_ORCID_TTL_DAYS", "365")),
        "gimie": int(os.environ.get("CACHE_GIMIE_TTL_DAYS", "365")),  # Changed from 1 day
        "llm": int(os.environ.get("CACHE_LLM_TTL_DAYS", "365")),  # Changed from 30 days
        "llm_user": int(os.environ.get("CACHE_LLM_USER_TTL_DAYS", "365")),  # Changed from 7 days
        "llm_org": int(os.environ.get("CACHE_LLM_ORG_TTL_DAYS", "365")),  # Changed from 7 days
    },
    # Cache database settings
    "cache_db_path": os.environ.get("CACHE_DB_PATH", "api_cache.db"),
    "cache_enabled": os.environ.get("CACHE_ENABLED", "true").lower() == "true",
    # Cache cleanup settings
    "auto_cleanup_enabled": os.environ.get("CACHE_AUTO_CLEANUP", "true").lower()
    == "true",
    "cleanup_interval_hours": int(os.environ.get("CACHE_CLEANUP_INTERVAL_HOURS", "24")),
    # Cache size limits
    "max_cache_size_mb": int(os.environ.get("CACHE_MAX_SIZE_MB", "1000")),
    "max_entries_per_api": int(os.environ.get("CACHE_MAX_ENTRIES_PER_API", "10000")),
}

# Cache statistics thresholds
CACHE_STATS_THRESHOLDS = {
    "high_hit_rate": 0.8,  # 80% hit rate considered high
    "low_hit_rate": 0.3,  # 30% hit rate considered low
    "large_db_size_mb": 500,  # 500MB considered large
    "many_entries": 5000,  # 5000 entries considered many
}

# Cache key patterns for different API types
CACHE_KEY_PATTERNS = {
    "github_user": "github_user:{username}",
    "github_org": "github_org:{org_name}",
    "orcid": "orcid:{orcid_id}",
    "gimie": "gimie:{full_path}:{format}",
    "llm": "llm:{full_path}:{max_tokens}:{output_format}",
    "llm_user": "llm_user:{username}:{item_type}",
    "llm_org": "llm_org:{org_name}:{item_type}",
}


def get_cache_config() -> Dict[str, Any]:
    """Get the current cache configuration."""
    return CACHE_CONFIG.copy()


def get_cache_ttl(api_type: str) -> int:
    """Get TTL for specific API type."""
    return CACHE_CONFIG["api_ttl_overrides"].get(
        api_type,
        CACHE_CONFIG["default_ttl_days"],
    )


def is_cache_enabled() -> bool:
    """Check if caching is enabled."""
    return CACHE_CONFIG["cache_enabled"]


def get_cache_db_path() -> str:
    """Get the cache database path."""
    return CACHE_CONFIG["cache_db_path"]


def should_auto_cleanup() -> bool:
    """Check if automatic cleanup is enabled."""
    return CACHE_CONFIG["auto_cleanup_enabled"]


def get_cleanup_interval_hours() -> int:
    """Get cleanup interval in hours."""
    return CACHE_CONFIG["cleanup_interval_hours"]


def get_max_cache_size_mb() -> int:
    """Get maximum cache size in MB."""
    return CACHE_CONFIG["max_cache_size_mb"]


def get_max_entries_per_api() -> int:
    """Get maximum entries per API type."""
    return CACHE_CONFIG["max_entries_per_api"]
