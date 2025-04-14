import json
import requests
from pyld import jsonld
from rdflib import Graph
import ast

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

def json_to_jsonLD(json_data):
    """Convert json to jsonLD using context file. Returns a jsonLD dictionary"""
    with open("files/json-ld-context.json") as context:
        context_data = json.load(context)

    expanded_data = jsonld.expand({**context_data, **json_data})

    return expanded_data[0]

def merge_jsonld(data1, data2, output_path):
    """Merges two JSON-LD objects and puts the output in output_path"""
    g1 = Graph()
    g1.parse(data=json.dumps(data1), format="json-ld")

    g2 = Graph()
    g2.parse(data=json.dumps(data2), format="json-ld")

    g3 = g1 + g2

    # Serialize to N-Triples format in output path
    g3.serialize(destination=output_path, format="json-ld", indent=4)