import os
import tempfile
import subprocess
import glob
import requests
import tiktoken

from utils.prompts import system_prompt_json
# from utils.output import RepositoryInfo
from utils.pydantic import SoftwareSourceCode
from utils.utils import *

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
MODEL = "google/gemini-2.0-flash-001"

def llm_request_repo_infos(repo_url):    
    # Clone the GitHub repository into a temporary folder
    with tempfile.TemporaryDirectory() as temp_dir:
        print(f"Cloning {repo_url} into {temp_dir}...")
        subprocess.run(["git", "clone", repo_url, temp_dir], check=True)

        # Run the repo-to-text command in the repository directory
        subprocess.run(["repo-to-text"], cwd=temp_dir, check=True)

        # Retrieve all .txt files generated in the repository directory
        txt_files = glob.glob(os.path.join(temp_dir, "*.txt"))

        # Combine contents of all text files into a single string
        combined_text = ""
        for file in txt_files:
            with open(file, "r", encoding="utf-8") as f:
                combined_text += f.read() + "\n"
                
        # Limit combined_text to 950000 tokens
        limiter_encoding = tiktoken.get_encoding("cl100k_base")
        tokens = limiter_encoding.encode(combined_text)
        
        print(f"Original amount of tokens: {len(tokens)}")
        if len(tokens) > 950000:
            tokens = tokens[:800000]
            combined_text = limiter_encoding.decode(tokens)
                
        # Save the combined text to a new file
        combined_file_path = os.path.join(temp_dir, "combined_repo.txt")
        with open(combined_file_path, "w", encoding="utf-8") as f:
            f.write(combined_text)

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
            "temperature": 0.7
        }

        headers = {
            "Authorization": f"Bearer {GEMINI_API_KEY}",
            "Content-Type": "application/json"
        }

        # Send request to OpenRouter
        response = requests.post(ENDPOINT, 
                                 headers=headers, json=payload)

        if response.status_code == 200:
            raw_result = response.json()["choices"][0]["message"]["content"] # Read response
            
            try:
                parsed_result = clean_json_string(raw_result) # Parse result into clean json
                return json_to_jsonLD(parsed_result) # Return converted data to jsonLD
            
            except Exception as e:
                print("Error:", e)
                return None
        else:
            print(f"Error: {response.status_code} - {response.text}")
            return None
