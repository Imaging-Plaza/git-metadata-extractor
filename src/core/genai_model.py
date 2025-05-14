import os
import tempfile
import subprocess
import glob
import requests
import tiktoken
import logging
from dotenv import load_dotenv
from pprint import pprint

from .prompts import system_prompt_json
from .pydantic import SoftwareSourceCode
from ..utils.utils import *
from .verification import Verification

load_dotenv()

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
#MODEL = "google/gemini-2.0-flash-001"
MODEL="google/gemini-2.5-pro-preview"

# Setup logger
logger = logging.getLogger(__name__)

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

        # Retrieve all .txt files generated in the repository directory
        txt_files = glob.glob(os.path.join(temp_dir, "*.txt"))
        logger.info(f"Found {len(txt_files)} text files")

        # Combine contents of all text files into a single string
        combined_text = ""
        for file in txt_files:
            logger.debug(f"Reading file: {file}")
            with open(file, "r", encoding="utf-8") as f:
                combined_text += f.read() + "\n"
                
        # Limit combined_text to 950000 tokens
        limiter_encoding = tiktoken.get_encoding("cl100k_base")
        tokens = limiter_encoding.encode(combined_text)
        
        logger.info(f"Original amount of tokens: {len(tokens)}")
        if len(tokens) > 950000:
            tokens = tokens[:800000]
            combined_text = limiter_encoding.decode(tokens)
            logger.warning("Token count exceeded limit, truncated to 800000 tokens")

        # Save the combined text to a new file
        combined_file_path = os.path.join(temp_dir, "combined_repo.txt")
        with open(combined_file_path, "w", encoding="utf-8") as f:
            f.write(combined_text)
        logger.info(f"Combined text saved to {combined_file_path}")

        # Prepare payload for OpenRouter API
        payload = {
            "model": MODEL,
            "messages": [
                {"role": "system", "content": system_prompt_json},
                {"role": "user", "content": combined_text}
            ],
            "response_format": {
                "type": "json_schema",
                "json_schema": SoftwareSourceCode.model_json_schema()
            },
            "temperature": 1
        }

        headers = {
            "Authorization": f"Bearer {GEMINI_API_KEY}",
            "Content-Type": "application/json"
        }


        # Send request to OpenRouter
        try:
            response = requests.post(ENDPOINT, headers=headers, json=payload)
            logger.info(f"API response status: {response.status_code}")
        except requests.exceptions.RequestException as e:
            logger.error(f"Request failed: {e}")
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


def get_pydantic_ai_data(json_data):
    

    pass