"""
Cache management utilities and configuration for the API caching system.
"""

import logging
import os
import sqlite3
from pathlib import Path
from typing import Any, Awaitable, Dict, Optional, Union

from .cache import APICache

logger = logging.getLogger(__name__)


class CacheConfig:
    """Configuration for API caching behavior."""

    # Default TTL settings (in days)
    DEFAULT_TTL_DAYS = 30

    # API-specific TTL settings
    API_TTL_OVERRIDES = {
        "github_user": 7,  # GitHub user data changes less frequently
        "github_org": 7,  # GitHub org data changes less frequently
        "orcid": 14,  # ORCID data is relatively stable
        "gimie": 1,  # GIMIE data might change more frequently
        "llm": 30,  # LLM responses can be cached longer
    }

    # Force refresh parameter name
    FORCE_REFRESH_PARAM = "force_refresh"

    # Cache enabled by default
    CACHE_ENABLED = True


class CacheManager:
    """High-level cache management for API endpoints."""

    SAFE_DEBUG_PARAM_KEYS = {
        "github_user": ("username", "include_repositories"),
        "github_org": ("org_name", "include_repositories"),
        "orcid": ("orcid_id",),
        "gimie": ("source_url", "output_format"),
    }

    def __init__(self, cache_db_path: str = "api_cache.db"):
        self.cache = APICache(cache_db_path)
        self.config = CacheConfig()

    @staticmethod
    def _format_debug_value(value: Any) -> str:
        if value is None:
            return "None"
        if isinstance(value, bool):
            return str(value).lower()
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, str):
            return value if len(value) <= 100 else f"{value[:97]}..."
        if isinstance(value, list):
            return f"list(len={len(value)})"
        if isinstance(value, dict):
            return f"dict(keys={len(value)})"
        return type(value).__name__

    def _build_debug_context(
        self,
        api_type: str,
        params: Dict[str, Any],
        force_refresh: bool,
    ) -> str:
        safe_keys = self.SAFE_DEBUG_PARAM_KEYS.get(api_type, ())
        details: list[str] = []

        for key in safe_keys:
            if key in params:
                details.append(f"{key}={self._format_debug_value(params[key])}")

        if not details and params:
            details.append(f"param_keys={sorted(params.keys())}")

        details.append(f"force_refresh={str(force_refresh).lower()}")
        return ", ".join(details)

    def _safe_cache_set(
        self,
        api_type: str,
        params: Dict[str, Any],
        fresh_data: Any,
        ttl: int,
        debug_context: str,
    ) -> None:
        """Persist to SQLite; log and continue if the DB is locked or data is not serializable."""
        try:
            self.cache.set(api_type, params, fresh_data, ttl)
        except (OSError, sqlite3.OperationalError, TypeError, ValueError) as e:
            logger.warning(
                "Cache set skipped for %s (%s): %s",
                api_type,
                debug_context,
                e,
            )
            return
        logger.info(
            "Cached fresh data for %s (ttl_days=%s, %s)",
            api_type,
            ttl,
            debug_context,
        )

    # Deprecated: in future versions
    def get_cached_or_fetch(
        self,
        api_type: str,
        params: Dict[str, Any],
        fetch_func: callable,
        force_refresh: bool = False,
        custom_ttl: Optional[int] = None,
    ) -> Any:
        """
        Get data from cache or fetch it if not cached/expired.

        Args:
            api_type: Type of API (github_user, github_org, orcid, gimie, llm)
            params: Parameters for the API call
            fetch_func: Function to call if cache miss
            force_refresh: If True, bypass cache
            custom_ttl: Custom TTL in days

        Returns:
            Cached or freshly fetched data
        """
        debug_context = self._build_debug_context(api_type, params, force_refresh)

        if not self.config.CACHE_ENABLED:
            logger.info(
                "Cache disabled, fetching fresh data for %s (%s)",
                api_type,
                debug_context,
            )
            return fetch_func()

        # Try to get from cache first
        if not force_refresh:
            cached_result = self.cache.get(api_type, params)
            if cached_result is not None:
                return cached_result

        # Fetch fresh data
        logger.info("Fetching fresh data for %s (%s)", api_type, debug_context)
        fresh_data = fetch_func()

        # Don't cache coroutines - return them to be awaited by the caller
        if hasattr(fresh_data, "__await__"):
            logger.info(
                "Fetch function returned a coroutine for %s (%s), returning without caching",
                api_type,
                debug_context,
            )
            return fresh_data

        # Cache the result if successful
        if fresh_data is not None:
            ttl = custom_ttl or self.config.API_TTL_OVERRIDES.get(
                api_type,
                self.config.DEFAULT_TTL_DAYS,
            )
            self._safe_cache_set(api_type, params, fresh_data, ttl, debug_context)

        return fresh_data

    # DEPRECATED announced for removal in future versions
    async def get_cached_or_fetch_async(
        self,
        api_type: str,
        params: Dict[str, Any],
        fetch_func: Union[callable, Awaitable],
        force_refresh: bool = False,
        custom_ttl: Optional[int] = None,
    ) -> Any:
        """
        Get data from cache or fetch it if not cached/expired.
        Automatically handles coroutines by awaiting them and caching the result.

        Args:
            api_type: Type of API (github_user, github_org, orcid, gimie, llm)
            params: Parameters for the API call
            fetch_func: Function to call if cache miss (can be sync or async)
            force_refresh: If True, bypass cache
            custom_ttl: Custom TTL in days

        Returns:
            Cached or freshly fetched data (awaited if it was a coroutine)
        """
        debug_context = self._build_debug_context(api_type, params, force_refresh)

        if not self.config.CACHE_ENABLED:
            logger.info(
                "Cache disabled, fetching fresh data for %s (%s)",
                api_type,
                debug_context,
            )
            fresh_data = fetch_func()
            # Handle coroutines even when cache is disabled
            if hasattr(fresh_data, "__await__"):
                return await fresh_data
            return fresh_data

        # Try to get from cache first
        if not force_refresh:
            cached_result = self.cache.get(api_type, params)
            if cached_result is not None:
                return cached_result

        # Fetch fresh data
        logger.info("Fetching fresh data for %s (%s)", api_type, debug_context)
        fresh_data = fetch_func()

        # Handle coroutines automatically
        if hasattr(fresh_data, "__await__"):
            logger.info("Awaiting coroutine for %s (%s)", api_type, debug_context)
            fresh_data = await fresh_data

        # Cache the result if successful
        if fresh_data is not None:
            ttl = custom_ttl or self.config.API_TTL_OVERRIDES.get(
                api_type,
                self.config.DEFAULT_TTL_DAYS,
            )
            self._safe_cache_set(api_type, params, fresh_data, ttl, debug_context)

        return fresh_data

    # NEW METHODS FOR DIRECT CACHE MANAGEMENT
    def load_from_cache(self, api_type: str, params: Dict[str, Any]) -> Optional[Any]:
        """Load data directly from cache without fetching."""
        if not self.config.CACHE_ENABLED:
            logger.info("Cache is disabled, cannot load from cache")
            return None
        return self.cache.get(api_type, params)

    def store_in_cache(
        self,
        api_type: str,
        params: Dict[str, Any],
        data: Any,
        custom_ttl: Optional[int] = None,
    ) -> bool:
        """Manually store data in cache."""
        ttl = custom_ttl or self.config.API_TTL_OVERRIDES.get(
            api_type,
            self.config.DEFAULT_TTL_DAYS,
        )
        return self.cache.set(api_type, params, data, ttl)

    def invalidate_api_cache(self, api_type: str, params: Dict[str, Any]) -> bool:
        """Invalidate specific cache entry."""
        return self.cache.invalidate(api_type, params)

    def cleanup_expired(self) -> int:
        """Clean up expired cache entries."""
        return self.cache.cleanup_expired()

    def get_cache_stats(self) -> Dict[str, Any]:
        """Get comprehensive cache statistics."""
        stats = self.cache.get_stats()
        stats["config"] = {
            "cache_enabled": self.config.CACHE_ENABLED,
            "default_ttl_days": self.config.DEFAULT_TTL_DAYS,
            "api_ttl_overrides": self.config.API_TTL_OVERRIDES,
        }
        return stats

    def list_cache_entries(
        self,
        api_type: Optional[str] = None,
        limit: int = 100,
        offset: int = 0,
        include_expired: bool = False,
    ) -> Dict[str, Any]:
        """List cache entries with details."""
        return self.cache.list_entries(api_type, limit, offset, include_expired)

    def clear_all_cache(self) -> int:
        """Clear all cache entries."""
        return self.cache.clear_all()

    def enable_cache(self):
        """Enable caching."""
        self.config.CACHE_ENABLED = True
        logger.info("Cache enabled")

    def disable_cache(self):
        """Disable caching."""
        self.config.CACHE_ENABLED = False
        logger.info("Cache disabled")


# Global cache manager instance
_cache_manager: Optional[CacheManager] = None


def get_cache_manager() -> CacheManager:
    """Get the global cache manager instance."""
    global _cache_manager
    if _cache_manager is None:
        # Read cache path from environment variable with fallback
        cache_db_path = os.getenv("CACHE_DB_PATH", "api_cache.db")

        # Ensure the cache directory exists
        cache_dir = Path(cache_db_path).parent
        if cache_dir != Path():  # Only create if not current directory
            cache_dir.mkdir(parents=True, exist_ok=True)

        logger.info(f"📁 Initializing cache database at: {cache_db_path}")
        _cache_manager = CacheManager(cache_db_path)
    return _cache_manager


def extract_force_refresh_param(params: Dict[str, Any]) -> tuple[Dict[str, Any], bool]:
    """
    Extract force_refresh parameter from request parameters.

    Args:
        params: Request parameters dictionary

    Returns:
        Tuple of (cleaned_params, force_refresh_flag)
    """
    force_refresh = params.pop(CacheConfig.FORCE_REFRESH_PARAM, False)

    # Convert string values to boolean
    if isinstance(force_refresh, str):
        force_refresh = force_refresh.lower() in ("true", "1", "yes", "on")

    return params, force_refresh


def should_use_cache(api_type: str) -> bool:
    """
    Check if caching should be used for the given API type.

    Args:
        api_type: Type of API

    Returns:
        True if caching should be used
    """
    manager = get_cache_manager()
    return manager.config.CACHE_ENABLED
