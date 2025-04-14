import logging
import sys

def setup_logging(level=logging.INFO):
    """Sets up logging configuration used across the entire project."""
    logging.basicConfig(
        level=level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)]
    )

    logging.getLogger("rdflib").setLevel(logging.WARNING)
