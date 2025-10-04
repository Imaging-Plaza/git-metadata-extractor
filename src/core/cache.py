"""
Caching system for external API calls to reduce requests to GitHub, ORCID, and GIMIE.
Uses SQLite for structured storage with TTL support and force refresh capabilities.
"""

import hashlib
import json
import logging
import os
import sqlite3
from datetime import datetime, timedelta
from typing import Any, Dict, Optional

logger = logging.getLogger(__name__)


class APICache:
    """
    SQLite-based cache for external API responses with TTL support.

    Features:
    - Automatic TTL expiration (default 30 days)
    - Force refresh capability
    - Cache statistics and management
    - JSON storage for complex API responses
    - Thread-safe operations
    """

    def __init__(self, cache_db_path: str = "api_cache.db", default_ttl_days: int = 30):
        """
        Initialize the API cache.

        Args:
            cache_db_path: Path to SQLite database file
            default_ttl_days: Default TTL in days for cached entries
        """
        self.cache_db_path = cache_db_path
        self.default_ttl_days = default_ttl_days
        self._init_database()

    def _init_database(self):
        """Initialize the SQLite database with required tables."""
        with sqlite3.connect(self.cache_db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS cache_entries (
                    cache_key TEXT PRIMARY KEY,
                    api_type TEXT NOT NULL,
                    request_params TEXT NOT NULL,
                    response_data TEXT NOT NULL,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    expires_at TIMESTAMP NOT NULL,
                    hit_count INTEGER DEFAULT 0,
                    last_accessed TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """,
            )

            # Create indexes for better performance
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_expires_at ON cache_entries(expires_at)",
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_api_type ON cache_entries(api_type)",
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_created_at ON cache_entries(created_at)",
            )

            conn.commit()

    def _generate_cache_key(self, api_type: str, params: Dict[str, Any]) -> str:
        """
        Generate a unique cache key for the given API type and parameters.

        Args:
            api_type: Type of API (github_user, github_org, orcid, gimie, llm)
            params: Parameters used for the API call

        Returns:
            Unique cache key string
        """
        # Sort params to ensure consistent key generation
        sorted_params = json.dumps(params, sort_keys=True)
        key_string = f"{api_type}:{sorted_params}"
        return hashlib.sha256(key_string.encode()).hexdigest()

    def get(
        self,
        api_type: str,
        params: Dict[str, Any],
        force_refresh: bool = False,
    ) -> Optional[Any]:
        """
        Retrieve cached data for the given API type and parameters.

        Args:
            api_type: Type of API (github_user, github_org, orcid, gimie, llm)
            params: Parameters used for the API call
            force_refresh: If True, bypass cache and return None

        Returns:
            Cached response data or None if not found/expired
        """
        if force_refresh:
            logger.info(f"Force refresh requested for {api_type}, bypassing cache")
            return None

        cache_key = self._generate_cache_key(api_type, params)

        with sqlite3.connect(self.cache_db_path) as conn:
            cursor = conn.execute(
                """
                SELECT response_data, expires_at FROM cache_entries
                WHERE cache_key = ? AND expires_at > CURRENT_TIMESTAMP
            """,
                (cache_key,),
            )

            row = cursor.fetchone()

            if row:
                response_data, expires_at = row

                # Update hit count and last accessed
                conn.execute(
                    """
                    UPDATE cache_entries
                    SET hit_count = hit_count + 1, last_accessed = CURRENT_TIMESTAMP
                    WHERE cache_key = ?
                """,
                    (cache_key,),
                )
                conn.commit()

                logger.info(f"Cache hit for {api_type} with key {cache_key[:8]}...")
                return json.loads(response_data)
            logger.info(f"Cache miss for {api_type} with key {cache_key[:8]}...")
            return None

    def set(
        self,
        api_type: str,
        params: Dict[str, Any],
        response_data: Any,
        ttl_days: Optional[int] = None,
    ) -> None:
        """
        Store response data in cache.

        Args:
            api_type: Type of API (github_user, github_org, orcid, gimie, llm)
            params: Parameters used for the API call
            response_data: Response data to cache
            ttl_days: TTL in days (uses default if None)
        """
        cache_key = self._generate_cache_key(api_type, params)
        ttl = ttl_days or self.default_ttl_days
        expires_at = datetime.now() + timedelta(days=ttl)

        with sqlite3.connect(self.cache_db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO cache_entries
                (cache_key, api_type, request_params, response_data, expires_at)
                VALUES (?, ?, ?, ?, ?)
            """,
                (
                    cache_key,
                    api_type,
                    json.dumps(params, sort_keys=True),
                    json.dumps(response_data, default=str),
                    expires_at,
                ),
            )
            conn.commit()

        logger.info(
            f"Cached {api_type} response with key {cache_key[:8]}... (expires in {ttl} days)",
        )

    def invalidate(self, api_type: str, params: Dict[str, Any]) -> bool:
        """
        Remove specific cache entry.

        Args:
            api_type: Type of API
            params: Parameters used for the API call

        Returns:
            True if entry was found and removed, False otherwise
        """
        cache_key = self._generate_cache_key(api_type, params)

        with sqlite3.connect(self.cache_db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM cache_entries WHERE cache_key = ?",
                (cache_key,),
            )
            conn.commit()

            if cursor.rowcount > 0:
                logger.info(
                    f"Invalidated cache entry for {api_type} with key {cache_key[:8]}...",
                )
                return True
            logger.info(
                f"No cache entry found for {api_type} with key {cache_key[:8]}...",
            )
            return False

    def cleanup_expired(self) -> int:
        """
        Remove expired cache entries.

        Returns:
            Number of entries removed
        """
        with sqlite3.connect(self.cache_db_path) as conn:
            cursor = conn.execute(
                "DELETE FROM cache_entries WHERE expires_at <= CURRENT_TIMESTAMP",
            )
            conn.commit()

            removed_count = cursor.rowcount
            if removed_count > 0:
                logger.info(f"Cleaned up {removed_count} expired cache entries")

            return removed_count

    def get_stats(self) -> Dict[str, Any]:
        """
        Get cache statistics.

        Returns:
            Dictionary with cache statistics
        """
        with sqlite3.connect(self.cache_db_path) as conn:
            # Total entries
            total_cursor = conn.execute("SELECT COUNT(*) FROM cache_entries")
            total_entries = total_cursor.fetchone()[0]

            # Active entries (not expired)
            active_cursor = conn.execute(
                """
                SELECT COUNT(*) FROM cache_entries WHERE expires_at > CURRENT_TIMESTAMP
            """,
            )
            active_entries = active_cursor.fetchone()[0]

            # Entries by API type
            type_cursor = conn.execute(
                """
                SELECT api_type, COUNT(*) FROM cache_entries
                WHERE expires_at > CURRENT_TIMESTAMP
                GROUP BY api_type
            """,
            )
            entries_by_type = dict(type_cursor.fetchall())

            # Total hit count
            hits_cursor = conn.execute("SELECT SUM(hit_count) FROM cache_entries")
            total_hits = hits_cursor.fetchone()[0] or 0

            # Database size
            db_size = (
                os.path.getsize(self.cache_db_path)
                if os.path.exists(self.cache_db_path)
                else 0
            )

            return {
                "total_entries": total_entries,
                "active_entries": active_entries,
                "expired_entries": total_entries - active_entries,
                "entries_by_type": entries_by_type,
                "total_hits": total_hits,
                "database_size_bytes": db_size,
                "database_size_mb": round(db_size / (1024 * 1024), 2),
            }

    def clear_all(self) -> int:
        """
        Clear all cache entries.

        Returns:
            Number of entries removed
        """
        with sqlite3.connect(self.cache_db_path) as conn:
            cursor = conn.execute("DELETE FROM cache_entries")
            conn.commit()

            removed_count = cursor.rowcount
            logger.info(f"Cleared all {removed_count} cache entries")
            return removed_count


# Global cache instance
_cache_instance: Optional[APICache] = None


def get_cache() -> APICache:
    """Get the global cache instance."""
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = APICache()
    return _cache_instance


def cache_result(api_type: str, ttl_days: Optional[int] = None):
    """
    Decorator to cache function results.

    Args:
        api_type: Type of API for caching
        ttl_days: TTL in days (uses default if None)
    """

    def decorator(func):
        def wrapper(*args, **kwargs):
            cache = get_cache()

            # Create params dict from function arguments
            params = {"function": func.__name__, "args": args, "kwargs": kwargs}

            # Try to get from cache first
            result = cache.get(api_type, params)
            if result is not None:
                return result

            # Execute function and cache result
            result = func(*args, **kwargs)
            if result is not None:
                cache.set(api_type, params, result, ttl_days)

            return result

        return wrapper

    return decorator
