"""
Enhanced logging configuration with support for concurrent requests.

Features:
- Color-coded log levels and request IDs for easy visual separation
- Request context tracking for concurrent operations
- Structured logging with correlation IDs
- Clean, readable format even with multiple concurrent requests
- Gunicorn multi-worker compatible (includes worker PID in request IDs)
"""

import logging
import os
import random
import sys
from contextvars import ContextVar
from typing import Optional

# Context variable to store request ID across async operations
request_id_var: ContextVar[Optional[str]] = ContextVar("request_id", default=None)


# ANSI color codes for terminal output
class Colors:
    """ANSI color codes for terminal output."""

    RESET = "\033[0m"
    BOLD = "\033[1m"

    # Log levels
    DEBUG = "\033[36m"  # Cyan
    INFO = "\033[32m"  # Green
    WARNING = "\033[33m"  # Yellow
    ERROR = "\033[31m"  # Red
    CRITICAL = "\033[35m"  # Magenta

    # Request ID colors (for distinguishing concurrent requests)
    REQUEST_COLORS = [
        "\033[94m",  # Bright Blue
        "\033[92m",  # Bright Green
        "\033[96m",  # Bright Cyan
        "\033[95m",  # Bright Magenta
        "\033[93m",  # Bright Yellow
        "\033[91m",  # Bright Red
        "\033[97m",  # Bright White
        "\033[90m",  # Bright Black (Gray)
    ]

    @classmethod
    def get_request_color(cls, request_id: str) -> str:
        """Get a consistent color for a request ID based on hash."""
        if not request_id:
            return cls.RESET
        # Use hash to get consistent color for same request ID
        index = hash(request_id) % len(cls.REQUEST_COLORS)
        return cls.REQUEST_COLORS[index]


class ColoredFormatter(logging.Formatter):
    """
    Custom formatter that adds colors to log output.

    - Colors log levels differently
    - Colors request IDs for visual separation of concurrent requests
    - Maintains readability with consistent formatting
    """

    # Map log levels to colors
    LEVEL_COLORS = {
        "DEBUG": Colors.DEBUG,
        "INFO": Colors.INFO,
        "WARNING": Colors.WARNING,
        "ERROR": Colors.ERROR,
        "CRITICAL": Colors.CRITICAL,
    }

    def format(self, record):
        # Get request ID from context
        request_id = request_id_var.get()

        # Add request ID to record if available
        if request_id:
            record.request_id = f"[{request_id}]"
            record.request_color = Colors.get_request_color(request_id)
        else:
            record.request_id = ""
            record.request_color = ""

        # Color the log level
        levelname = record.levelname
        if levelname in self.LEVEL_COLORS:
            record.levelname = (
                f"{self.LEVEL_COLORS[levelname]}{levelname}{Colors.RESET}"
            )

        # Format the message
        formatted = super().format(record)

        # Reset colors at the end
        return formatted + Colors.RESET


class RequestContextFilter(logging.Filter):
    """Filter that adds request context to log records."""

    def filter(self, record):
        request_id = request_id_var.get()
        if request_id:
            record.request_id = request_id
            record.request_color = Colors.get_request_color(request_id)
        else:
            record.request_id = ""
            record.request_color = ""
        return True


def generate_request_id(prefix: str = "req") -> str:
    """
    Generate a unique request ID that includes the worker PID.

    This ensures uniqueness across Gunicorn workers by including the process ID.
    Format: prefix-PID-XXXX (e.g., 'repo-12345-a3f2')

    Args:
        prefix: Prefix for the request ID (e.g., 'repo', 'user', 'org')

    Returns:
        A unique request ID like 'repo-12345-a3f2' where 12345 is the worker PID
    """
    # Include PID to ensure uniqueness across Gunicorn workers
    pid = os.getpid()
    # Generate 4 random hex characters
    suffix = "".join(random.choices("0123456789abcdef", k=4))
    return f"{prefix}-{pid}-{suffix}"


def set_request_id(request_id: Optional[str] = None, prefix: str = "req") -> str:
    """
    Set the request ID for the current async context.

    Args:
        request_id: Optional request ID to use. If None, generates a new one.
        prefix: Prefix for auto-generated IDs

    Returns:
        The request ID that was set
    """
    if request_id is None:
        request_id = generate_request_id(prefix)
    request_id_var.set(request_id)
    return request_id


def clear_request_id():
    """Clear the request ID from the current context."""
    request_id_var.set(None)


def get_request_id() -> Optional[str]:
    """Get the current request ID."""
    return request_id_var.get()


def setup_logging(level=logging.INFO, use_colors: bool = True):
    """
    Sets up enhanced logging configuration for the entire project.

    Args:
        level: Logging level (default: INFO)
        use_colors: Whether to use colored output (default: True)

    Features:
        - Color-coded log levels
        - Request ID tracking for concurrent operations
        - Clean, structured format
        - Support for filtering by request ID
    """

    # Create handler
    handler = logging.StreamHandler(sys.stdout)

    if use_colors:
        # Use colored formatter with request ID
        formatter = ColoredFormatter(
            fmt="%(asctime)s %(levelname)s %(request_color)s%(request_id)s\033[0m %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    else:
        # Plain formatter with request ID
        formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(request_id)s %(name)s: %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )

    handler.setFormatter(formatter)

    # Add request context filter
    handler.addFilter(RequestContextFilter())

    # Configure root logger
    logging.basicConfig(
        level=level,
        handlers=[handler],
        force=True,  # Override any existing configuration
    )

    # Reduce verbosity of external libraries
    logging.getLogger("rdflib").setLevel(logging.WARNING)
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("selenium").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)


# Example usage context manager
class RequestContext:
    """
    Context manager for setting request ID in a block of code.

    Example:
        with RequestContext("repo-123"):
            logger.info("Processing repository")  # Will include [repo-123] in log
    """

    def __init__(self, request_id: Optional[str] = None, prefix: str = "req"):
        self.request_id = request_id or generate_request_id(prefix)
        self.previous_id = None

    def __enter__(self):
        self.previous_id = request_id_var.get()
        request_id_var.set(self.request_id)
        return self.request_id

    def __exit__(self, exc_type, exc_val, exc_tb):
        request_id_var.set(self.previous_id)
        return False


# Async context manager version
class AsyncRequestContext:
    """
    Async context manager for setting request ID.

    Example:
        async with AsyncRequestContext("user-abc"):
            logger.info("Processing user")  # Will include [user-abc] in log
    """

    def __init__(self, request_id: Optional[str] = None, prefix: str = "req"):
        self.request_id = request_id or generate_request_id(prefix)
        self.previous_id = None

    async def __aenter__(self):
        self.previous_id = request_id_var.get()
        request_id_var.set(self.request_id)
        return self.request_id

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        request_id_var.set(self.previous_id)
        return False
