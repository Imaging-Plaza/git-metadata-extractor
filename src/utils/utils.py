import json
import requests
from pyld import jsonld
from rdflib import Graph
import ast
import logging
from pprint import pprint

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

def json_to_jsonLD(json_data, file_path): 
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
        logger.info(f"✅ Merged JSON-LD")

        return merged_jsonld
    
from pydantic import HttpUrl, BaseModel
from typing import Any

# def convert_httpurl_to_str(obj: Any) -> Any:
#     """
#     Recursively convert all HttpUrl fields in a Pydantic model (or nested structures)
#     to plain strings, so the resulting dict is OpenAI-compatible.
#     """
#     if isinstance(obj, HttpUrl):
#         return str(obj)
#     elif isinstance(obj, BaseModel):
#         return {k: convert_httpurl_to_str(v) for k, v in obj.dict(exclude_none=True).items()}
#     elif isinstance(obj, list):
#         return [convert_httpurl_to_str(item) for item in obj]
#     elif isinstance(obj, dict):
#         return {k: convert_httpurl_to_str(v) for k, v in obj.items()}
#     else:
#         return obj

import json
from pydantic import create_model, HttpUrl, BaseModel
from typing import get_origin, get_args, Union, List, Any, get_type_hints
import inspect

def convert_httpurl_to_str(schema_class):
    """
    Convert HttpUrl fields to str fields for OpenAI compatibility, including nested models.
    """
    if not issubclass(schema_class, BaseModel):
        return schema_class
    
    # Get the original fields
    original_fields = schema_class.model_fields
    new_fields = {}
    
    for field_name, field_info in original_fields.items():
        annotation = field_info.annotation
        converted_annotation = _convert_annotation(annotation)
        new_fields[field_name] = (converted_annotation, field_info.default)
    
    # Create new model class with converted fields
    converted_model = create_model(
        f"{schema_class.__name__}Converted",
        **new_fields
    )
    
    return converted_model

def _convert_annotation(annotation):
    """
    Recursively convert annotations, replacing HttpUrl with str and handling nested models.
    """
    origin = get_origin(annotation)
    
    # Handle Union types (Optional, etc.)
    if origin is Union:
        args = get_args(annotation)
        new_args = tuple(_convert_annotation(arg) for arg in args)
        return Union[new_args]
    
    # Handle List types
    elif origin is list or origin is List:
        args = get_args(annotation)
        if args:
            new_args = tuple(_convert_annotation(arg) for arg in args)
            return List[new_args[0]] if len(new_args) == 1 else List[new_args]
        return annotation
    
    # Handle HttpUrl -> str conversion
    elif annotation is HttpUrl:
        return str
    
    # Handle nested BaseModel classes
    elif (inspect.isclass(annotation) and 
          issubclass(annotation, BaseModel) and 
          annotation is not BaseModel):
        return convert_httpurl_to_str(annotation)
    
    # Return unchanged for all other types
    else:
        return annotation