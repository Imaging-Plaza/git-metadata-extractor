import os
import tempfile
import subprocess
import glob
import requests
from pathlib import Path
import tiktoken
from pprint import pprint

from utils.prompts import system_prompt

GEMINI_API_KEY = os.environ["GEMINI_API_KEY"]
ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"

def request(repo_url):   
    # Load schema context
    file_path_schema = Path(__file__).parent / "files" / "schema_context.txt"
    schema_context = ""
    if file_path_schema.exists():
        with open(file_path_schema, "r", encoding="utf-8") as f:
            schema_context = f.read()

    # Count tokens in schema_context
    limiter_encoding = tiktoken.get_encoding("cl100k_base")
    schema_tokens = len(limiter_encoding.encode(schema_context))
    print(f"Schema context token count: {schema_tokens}")
    
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
        tokens = limiter_encoding.encode(combined_text)
        
        print(f"Original amount of tokens: {len(tokens)}")
        if len(tokens) > 950000 - schema_tokens:
            tokens = tokens[:800000]
            combined_text = limiter_encoding.decode(tokens)
                
        # Save the combined text to a new file
        combined_file_path = os.path.join(temp_dir, "combined_repo.txt")
        with open(combined_file_path, "w", encoding="utf-8") as f:
            f.write(combined_text)

        # Prepare payload for OpenRouter API
        payload = {
            "model": "google/gemini-2.0-flash-001",
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": combined_text + "\n\n" + schema_context}
            ],
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
            result = response.json()["choices"][0]["message"]["content"]
            pprint(result)
        else:
            print(f"Error: {response.status_code} - {response.text}")
            result = None

        return result
