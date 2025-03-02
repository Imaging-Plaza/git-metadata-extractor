import json
import requests
from datetime import datetime
from pyld import jsonld

def to_bool(s):
    """Return a boolean based on a string boolean"""
    return s == "True"

def dict_to_json(data):
    """Convert a python dict to json."""
    def convert_datetime(obj):
        if isinstance(obj, datetime):
            return obj.isoformat()
        raise TypeError(f"Type {obj.__class__.__name__} not serializable")

    json_string = json.dumps(data, default=convert_datetime, indent=4)
    return json_string

def process_list(string):
    return string.split(" ,")

def fetch_jsonld(url):
    """Fetch JSON-LD data from a given URL."""
    headers = {"Accept": "application/ld+json"}
    response = requests.get(url, headers=headers)
    if response.status_code == 200:
        return response.json()
    else:
        raise Exception(f"Error fetching data: {response.status_code} - {response.text}")
    
def clean_json_string(raw_text):
    """Remove triple backticks and 'json' from the response."""
    if raw_text.startswith("```json"):
        raw_text = raw_text[7:]  # Remove leading ```json
    if raw_text.endswith("```"):
        raw_text = raw_text[:-3]  # Remove trailing ```
    return raw_text.strip()

def json_to_jsonLD(json_data):
    """Convert json to jsonLD using context file. Returns a jsonLD dictionary"""
    with open("files/json-ld-context.json") as context:
        context_data = json.load(context)

    expanded_data = jsonld.expand({**context_data, **json_data})

    return expanded_data[0]

def merge_jsonld(data1, data2):
    """Recursively merges two JSON-LD objects, giving priority to data1."""
    merged = data1.copy()
    for key, value in data2.items():
        if key in merged:
            merged[key] = merge_jsonld(merged[key], value)
        else:
            merged[key] = value
    return merged