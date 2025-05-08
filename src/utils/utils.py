import json
import requests
from pyld import jsonld
from rdflib import Graph
import ast
import logging

logger = logging.getLogger(__name__)

def fetch_jsonld(url):
    """Fetch JSON-LD data from a given URL."""
    headers = {"Accept": "application/ld+json"}
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return ast.literal_eval(response.json().get("output", "{}"))
    else:
        raise Exception(f"Error fetching data: {response.status_code} - {response.text}")
    
def clean_json_string(raw_text):
    """Remove triple backticks and 'json' from the response."""
    if raw_text.startswith("```json"):
        raw_text = raw_text[7:]
    if raw_text.endswith("```"):
        raw_text = raw_text[:-3]

    return raw_text.strip()

def json_to_jsonLD(json_data, file_path): #"files/json-ld-context.json"
    """Convert json to jsonLD using context file. Returns a jsonLD dictionary"""
    with open(file_path) as context:
        context_data = json.load(context)

    expanded_data = jsonld.expand({**context_data, **json_data})

    return expanded_data[0]

def merge_jsonld(gimie_graph: list, llm_jsonld: dict, output_path: str = None):
    """Merge a GIMIE JSON-LD graph (list of nodes) with a flat LLM JSON-LD object,
    giving priority to GIMIE fields and preserving JSON-LD structure."""

    logger.info("Merging GIMIE (@graph list) and LLM JSON-LD (flat object)...")

    # Identify the SoftwareSourceCode node in GIMIE
    software_node = next(
        (node for node in gimie_graph if "http://schema.org/SoftwareSourceCode" in node.get("@type", [])),
        None
    )

    if software_node is None:
        raise ValueError("No SoftwareSourceCode node found in GIMIE @graph.")

    # Merge LLM fields that don't exist in GIMIE
    added_fields = []
    for key, value in llm_jsonld.items():
        if key not in software_node:
            software_node[key] = value
            added_fields.append(key)

    logger.info(f"Merged {len(added_fields)} fields from LLM into SoftwareSourceCode node.")
    if added_fields:
        logger.debug(f"Fields added: {added_fields}")

    # Reconstruct the final JSON-LD
    merged_jsonld = {
        "@context": "https://schema.org",
        "@graph": gimie_graph
    }

    if output_path:
        # Save to file
        with open(output_path, "w", encoding="utf-8") as f:
            json.dump(merged_jsonld, f, indent=4)

        logger.info(f"✅ Merged JSON-LD written to {output_path}")
    else:
        return merged_jsonld