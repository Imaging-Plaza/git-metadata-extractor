import os
import tempfile
import subprocess
import glob
import requests
import tiktoken
import logging
from dotenv import load_dotenv
from pprint import pprint
import openai

from .prompts import system_prompt_json
from .models import SoftwareSourceCode, GitHubOrganization, GitHubUser
from ..utils.utils import *
from .verification import Verification

load_dotenv()

OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.environ["MODEL"]
PROVIDER = os.environ["PROVIDER"]

# Setup logger
logger = logging.getLogger(__name__)

def reduce_input_size(input_text, max_tokens=800000):
    """
    Reduce the size of the input text to fit within the specified token limit.
    """
    limiter_encoding = tiktoken.get_encoding("cl100k_base")
    tokens = limiter_encoding.encode(input_text)
    
    logger.info(f"Original amount of tokens: {len(tokens)}")
    if len(tokens) > max_tokens:
        tokens = tokens[:max_tokens]
        reduced_text = limiter_encoding.decode(tokens)
        logger.warning(f"Token count exceeded limit, truncated to {max_tokens} tokens")
        return reduced_text
    return input_text

def sort_files_by_priority(file_paths):
    """
    Sorts a list of file paths based on a predefined extension priority.

    The order is:
    1. Documentation files (.md, .txt, .html)
    2. Code files (.py, .r)
    3. All other files
    """
    priority_order = {
        # Priority 0: Documentation
        ".cff":0,
        ".md": 0,
        ".txt": 0,
        ".html": 0,
        # Priority 1: Code
        ".py": 1,
        ".r": 1,
    }
    # Priority 2 will be the default for all other extensions

    def get_sort_key(filepath):
        # Get the file extension
        _, ext = os.path.splitext(filepath)
        # Return a tuple: (priority, original_filepath)
        # The priority is looked up from the map (defaulting to 2)
        # The original filepath is used as a tie-breaker to maintain a stable sort
        return (priority_order.get(ext.lower(), 2), filepath)

    return sorted(file_paths, key=get_sort_key)

def combine_text_files(directory):
    """
    Combine all text files in the specified directory into a single string.
    """
    combined_text = ""
    txt_files = glob.glob(os.path.join(directory, "*.txt"))
    
    logger.info(f"Found {len(txt_files)} text files in {directory}")

    for file in txt_files:
        logger.debug(f"Reading file: {file}")
        with open(file, "r", encoding="utf-8") as f:
            combined_text += f.read() + "\n"
    
    return combined_text

def store_combined_text(input_text, output_file):
    """
    Store the combined text into a specified output file.
    """
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(input_text)
    logger.info(f"Combined text saved to {output_file}")
    return output_file
        

def clone_repo(repo_url):
    """
    Clone a GitHub repository into a temporary directory.
    """
    with tempfile.TemporaryDirectory() as temp_dir:
        logger.info(f"Cloning {repo_url} into {temp_dir}...")
        try:
            subprocess.run(["git", "clone", repo_url, temp_dir], check=True)
            logger.info("Repository cloned successfully.")
            return temp_dir
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to clone repository: {e}")
            return None
    

def llm_request_repo_infos(repo_url, output_format="json-ld"):    
    # Clone the GitHub repository into a temporary folder
    with tempfile.TemporaryDirectory() as temp_dir:
        logger.info(f"Cloning {repo_url} into {temp_dir}...")
        try:
            subprocess.run(["git", "clone", repo_url, temp_dir], check=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"Failed to clone repository: {e}")
            return None

        # Run the repo-to-text command in the repository directory
        try:
            subprocess.run(["repo-to-text"], cwd=temp_dir, check=True)
        except subprocess.CalledProcessError as e:
            logger.error(f"'repo-to-text' command failed: {e}")
            return None

        input_text = combine_text_files(temp_dir)
        input_text = reduce_input_size(input_text, max_tokens=40000)

        combined_file_path = os.path.join(temp_dir, "combined_repo.txt")
        store_combined_text(input_text, combined_file_path)


        if PROVIDER == "openrouter":
            response = get_openrouter_response(input_text, model=MODEL)
        elif PROVIDER == "openai":
            response = get_openai_response(input_text, model=MODEL)
        else:
            logger.error("No provider provided")

        if response.status_code == 200:
            try:
                raw_result = response.json()["choices"][0]["message"]["content"]
                parsed_result = clean_json_string(raw_result)
                json_data = json.loads(parsed_result)

                logger.info("Successfully parsed API response")

                # Run verification before converting to JSON-LD
                verifier = Verification(json_data)
                verifier.run()
                verifier.summary()

                # Sanitize metadata before conversion
                cleaned_json = verifier.sanitize_metadata()

                # TODO. This is hardcoded. Not good.
                context_path = "src/files/json-ld-context.json"
                # Now convert cleaned data to JSON-LD
                if output_format == "json-ld":
                    return json_to_jsonLD(cleaned_json, context_path)
                elif output_format == "json":
                    return json_data
                else:
                    logger.error(f"Unsupported output format: {output_format}")
                    return None

            except Exception as e:
                logger.error(f"Error parsing response: {e}")
                return None
        else:
            logger.error(f"API Error: {response.status_code} - {response.text}")
            return None

def get_openrouter_response(input_text, system_prompt=system_prompt_json, model="google/gemini-2.5-flash", temperature=0.1, schema=SoftwareSourceCode):
    """
    Get structured response from openrouter
    """
    # Prepare payload for OpenRouter API
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": input_text}
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": schema.model_json_schema()
        },
        "temperature": temperature
    }

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json"
    }


    # Send request to OpenRouter

    n = 3
    while n != 0:
        try:
            response = requests.post(OPENROUTER_ENDPOINT, headers=headers, json=payload)
            logger.info(f"API response status: {response.status_code}")
            n = 0
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e}")
            n -= 1
            return None
        
    return response
    

def get_openai_response(prompt, model="gpt-4o", temperature=0.1, schema=SoftwareSourceCode):
    """
    Get structured response from OpenAI API using SoftwareSourceCode schema.
    """
    try:
        response = openai.beta.chat.completions.parse(
            model=model,
            messages=[
                {"role": "system", "content": "You are a helpful assistant. Respond in JSON format."},
                {"role": "user", "content": prompt}
            ],
            temperature=temperature,
            response_format=convert_httpurl_to_str(schema)
        )

        return response
    
    except Exception as e:
        logger.error(f"OpenAI API error: {e}")
        return None
    
def llm_request_userorg_infos(metadata, item_type="user"):    

    input_text = metadata.model_dump_json()

    if item_type == "user":
        schema = GitHubUser
    elif item_type == "org":
        schema = GitHubOrganization

    if PROVIDER == "openrouter":
        response = get_openrouter_response(input_text, 
                                           system_prompt="Please parse this information and fill the json schema provided. Do not make new fields if they are not in the schema.", 
                                           model=MODEL, 
                                           schema=schema)
    elif PROVIDER == "openai":
        response = get_openai_response(input_text, model=MODEL, schema=schema)
    else:
        logger.error("No provider provided")

    #if response.status_code == 200:
    try:
        #TODO: Such a mess this part. Needs refactoring.
        if PROVIDER == "openrouter":
            response_json = response.json()
            raw_result = response_json["choices"][0]["message"]["content"]
            parsed_result = clean_json_string(raw_result)
            json_data = json.loads(parsed_result)
        elif PROVIDER == "openai":
            # OpenAI response has a different structure

            # For OpenAI's structured outputs, the parsed content is directly available
            json_data = response.choices[0].message.parsed
            if json_data is None:
                # Fallback to content if parsed is None
                raw_result = response.choices[0].message.content
                parsed_result = clean_json_string(raw_result)
                json_data = json.loads(parsed_result)
        else:
            logger.error("Unknown provider")
            return None

        logger.info("Successfully parsed API response")

        
        return json_data

    except Exception as e:
        logger.error(f"Error parsing response: {e}")
        return None
    
    # else:
    #     logger.error(f"API Error: {response.status_code} - {response.text}")
    #     return None