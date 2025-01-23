import json
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