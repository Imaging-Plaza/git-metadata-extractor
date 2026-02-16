import logging
import sys


def setup_logging(level=logging.INFO):
    """Sets up logging configuration used across the entire project."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    # Silence noisy external library loggers
    logging.getLogger("rdflib").setLevel(logging.WARNING)

    # Silence noisy HTTP/API client loggers that log every request/response at DEBUG
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("httpcore.http11").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("openai").setLevel(logging.WARNING)
    logging.getLogger("openai._base_client").setLevel(logging.WARNING)
