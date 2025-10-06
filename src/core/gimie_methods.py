import json
import logging

from gimie.project import Project

logger = logging.getLogger(__name__)


def extract_gimie(full_path: str, format: str = "json-ld"):
    """
    Extracts the GIMIE project from the given URL.

    Args:
        full_path (str): The full path to the URL.
        format (str): The format to serialize the graph. Default is 'json-ld', or 'ttl'.

    Returns:
        Project: The GIMIE project object.
    """
    logger.info(f"Extracting GIMIE metadata for: {full_path}")

    proj = Project(full_path)

    # To retrieve the rdflib.Graph object
    g = proj.extract()

    if format == "json-ld":
        # To retrieve the graph in JSON-LD format
        output = json.loads(g.serialize(format="json-ld"))
    else:
        output = g.serialize(format=format)

    if output is None:
        return None
    return output
