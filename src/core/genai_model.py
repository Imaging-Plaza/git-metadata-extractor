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
from .pydantic import SoftwareSourceCode
from ..utils.utils import *
from .verification import Verification

load_dotenv()

OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.environ["MODEL"]

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
    

def llm_request_repo_infos(repo_url):    
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
        input_text = reduce_input_size(input_text, max_tokens=800000)

        combined_file_path = os.path.join(temp_dir, "combined_repo.txt")
        store_combined_text(input_text, combined_file_path)


        # Prepare payload for OpenRouter API
        payload = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": system_prompt_json},
                {"role": "user", "content": input_text}
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": SoftwareSourceCode.model_json_schema()
            },
            "temperature": 0.1
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

        if response.status_code == 200:
            print(response.json())
            try:
                raw_result = response.json()["choices"][0]["message"]["content"]
                parsed_result = clean_json_string(raw_result)
                json_data = json.loads(parsed_result)
                pprint(json_data)

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
                return json_to_jsonLD(cleaned_json, context_path)

            except Exception as e:
                logger.error(f"Error parsing response: {e}")
                return None
        else:
            logger.error(f"API Error: {response.status_code} - {response.text}")
            return None



def get_openai_response(prompt, model="gpt-4o", temperature=0.7):
    """
    Get structured response from OpenAI API using SoftwareSourceCode schema.
    """
    try:
        response = openai.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": "You are a helpful assistant. Respond in JSON format."},
                {"role": "user", "content": prompt}
            ],
            temperature=temperature,
            response_format={
                "type": "json_object",
                "schema": SoftwareSourceCode.model_json_schema()
            }
        )
        # The response content will be a JSON string matching your schema
        result = response.choices[0].message.content
        return result
    except Exception as e:
        logger.error(f"OpenAI API error: {e}")
        return None