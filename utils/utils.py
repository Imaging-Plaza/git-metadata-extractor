import json
import requests
from datetime import datetime

def to_bool(s):
    return s == "True"

def dict_to_json(data):
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