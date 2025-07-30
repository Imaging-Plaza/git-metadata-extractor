import os
import tempfile
import asyncio
import subprocess
import glob
import aiohttp
import tiktoken
import logging
from dotenv import load_dotenv
from openai import AsyncOpenAI

from .prompts import system_prompt_json, system_prompt_user_content, system_prompt_org_content
from .models import SoftwareSourceCode, GitHubOrganization, GitHubUser
from ..utils.utils import *
from .verification import Verification

load_dotenv()

OPENROUTER_API_KEY = os.environ["OPENROUTER_API_KEY"]
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.environ["MODEL"]
PROVIDER = os.environ["PROVIDER"]

# Create async OpenAI client
async_openai_client = AsyncOpenAI(api_key=os.environ.get("OPENAI_API_KEY"))

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
        

async def clone_repo(repo_url, temp_dir):
    """
    Clone a GitHub repository into a temporary directory asynchronously.
    """
    logger.info(f"Cloning {repo_url} into {temp_dir}...")
    try:
        process = await asyncio.create_subprocess_exec(
            'git', 'clone', repo_url, temp_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode == 0:
            logger.info("Repository cloned successfully.")
            return temp_dir
        else:
            logger.error(f"Failed to clone repository: {stderr.decode()}")
            return None
    except Exception as e:
        logger.error(f"Failed to clone repository: {e}")
        return None

async def run_repo_to_text(temp_dir):
    """
    Run the repo-to-text command asynchronously.
    """
    try:
        process = await asyncio.create_subprocess_exec(
            'repo-to-text',
            cwd=temp_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE
        )
        stdout, stderr = await process.communicate()
        
        if process.returncode == 0:
            logger.info("repo-to-text command completed successfully.")
            return True
        else:
            logger.error(f"'repo-to-text' command failed: {stderr.decode()}")
            return False
    except Exception as e:
        logger.error(f"'repo-to-text' command failed: {e}")
        return False

def sanitize_special_tokens(text):
    """
    Remove special tokens using tiktoken encoding/decoding.
    """
    encoding = tiktoken.get_encoding("cl100k_base")
    
    # Encode with disallowed_special=() to handle special tokens
    # Then decode to get clean text
    try:
        tokens = encoding.encode(text, disallowed_special=())
        clean_text = encoding.decode(tokens)
        return clean_text
    except Exception as e:
        logger.warning(f"Failed to sanitize with tiktoken: {e}")
        # Fallback to simple regex cleanup
        import re
        return re.sub(r'<\|[^|]*\|>', '', text)
    

async def llm_request_repo_infos(repo_url, output_format="json-ld", gimie_output=None, max_tokens=40000):    
    """
    Async version of llm_request_repo_infos
    """
    # Clone the GitHub repository into a temporary folder
    with tempfile.TemporaryDirectory() as temp_dir:
        # Clone repository asynchronously
        clone_result = await clone_repo(repo_url, temp_dir)
        if not clone_result:
            return None

        # Run repo-to-text asynchronously
        repo_to_text_success = await run_repo_to_text(temp_dir)
        if not repo_to_text_success:
            return None

        input_text = combine_text_files(temp_dir)
        input_text = sanitize_special_tokens(input_text)
        input_text = reduce_input_size(input_text, max_tokens=max_tokens)

        if gimie_output:
            input_text += "\n\n" + str(gimie_output)

        combined_file_path = os.path.join(temp_dir, "combined_repo.txt")
        store_combined_text(input_text, combined_file_path)

        if PROVIDER == "openrouter":
            response = await get_openrouter_response_async(input_text, model=MODEL)
        elif PROVIDER == "openai":
            response = await get_openai_response_async(input_text, model=MODEL)
        else:
            logger.error("No provider provided")
            return None

        try:
            if PROVIDER == "openrouter":
                raw_result = response["choices"][0]["message"]["content"]
                parsed_result = clean_json_string(raw_result)
                json_data = json.loads(parsed_result)
            elif PROVIDER == "openai":
                json_data = response.choices[0].message.parsed
                logger.info("Clean result from OpenAI response:")
                json_data = json_data.model_dump(mode='json')

            logger.info("Successfully JSON API response")

            # Run verification before converting to JSON-LD
            verifier = Verification(json_data)
            verifier.run()
            verifier.summary()

            cleaned_json = verifier.sanitize_metadata()

            context_path = "src/files/json-ld-context.json"
            if output_format == "json-ld":
                return json_to_jsonLD(cleaned_json, context_path)
            elif output_format == "json":
                return cleaned_json
            else:
                logger.error(f"Unsupported output format: {output_format}")
                return None

        except Exception as e:
            logger.error(f"Error parsing response: {e}")
            return None

async def get_openrouter_response_async(input_text, system_prompt=system_prompt_json, model="google/gemini-2.5-flash", temperature=0.2, schema=SoftwareSourceCode):
    """
    Get structured response from openrouter asynchronously
    """
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

    timeout = aiohttp.ClientTimeout(total=300)  # 5 minute timeout
    
    for attempt in range(3):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(OPENROUTER_ENDPOINT, headers=headers, json=payload) as response:
                    logger.info(f"API response status: {response.status}")
                    if response.status == 200:
                        return await response.json()
                    else:
                        logger.error(f"API request failed with status {response.status}")
                        if attempt == 2:  # Last attempt
                            return None
        except aiohttp.ClientError as e:
            logger.error(f"Request failed (attempt {attempt + 1}): {e}")
            if attempt == 2:  # Last attempt
                return None
        except asyncio.TimeoutError as e:
            logger.error(f"Request timeout (attempt {attempt + 1}): {e}")
            if attempt == 2:  # Last attempt
                return None
    
    return None

async def get_openai_response_async(prompt, system_prompt=system_prompt_json, model="gpt-4o", temperature=0.2, schema=SoftwareSourceCode):
    """
    Get structured response from OpenAI API using SoftwareSourceCode schema asynchronously.
    """
    try:
        # Use the async OpenAI client
        if model.split("-")[0] == "o3":
            response = await async_openai_client.beta.chat.completions.parse(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                response_format=convert_httpurl_to_str(schema)
            )
        else:
            response = await async_openai_client.beta.chat.completions.parse(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                temperature=temperature,
                response_format=convert_httpurl_to_str(schema)
            )

        return response
    
    except Exception as e:
        logger.error(f"OpenAI API error: {e}")
        return None

async def llm_request_userorg_infos(metadata, item_type="user"):    
    """
    Async version of llm_request_userorg_infos
    """
    input_text = metadata.model_dump_json()

    if item_type == "user":
        schema = GitHubUser
        system_prompt = system_prompt_user_content
    elif item_type == "org":
        schema = GitHubOrganization
        system_prompt = system_prompt_org_content

    if PROVIDER == "openrouter":
        response = await get_openrouter_response_async(input_text, 
                                                     system_prompt=system_prompt, 
                                                     model=MODEL, 
                                                     schema=schema)
    elif PROVIDER == "openai":
        response = await get_openai_response_async(input_text, 
                                                 system_prompt=system_prompt, 
                                                 model=MODEL, 
                                                 schema=schema)
    else:
        logger.error("No provider provided")
        return None

    try:
        if PROVIDER == "openrouter":
            raw_result = response["choices"][0]["message"]["content"]
            parsed_result = clean_json_string(raw_result)
            json_data = json.loads(parsed_result)
        elif PROVIDER == "openai":
            json_data = response.choices[0].message.parsed
            json_data = json_data.model_dump(mode='json')
        else:
            logger.error("Unknown provider")
            return None

        logger.info("Successfully parsed API response")
        return json_data

    except Exception as e:
        logger.error(f"Error parsing response: {e}")
        return None

# Keep the synchronous versions for backward compatibility
def get_openrouter_response(input_text, system_prompt=system_prompt_json, model="google/gemini-2.5-flash", temperature=0.2, schema=SoftwareSourceCode):
    """
    Synchronous wrapper for backward compatibility
    """
    import asyncio
    return asyncio.run(get_openrouter_response_async(input_text, system_prompt, model, temperature, schema))

def get_openai_response(prompt, system_prompt=system_prompt_json, model="gpt-4o", temperature=0.2, schema=SoftwareSourceCode):
    """
    Synchronous wrapper for backward compatibility
    """
    from openai import OpenAI
    
    sync_client = OpenAI(api_key=os.environ.get("OPENAI_API_KEY"))
    
    try:
        if model.split("-")[0] == "o3":
            response = sync_client.beta.chat.completions.parse(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                response_format=convert_httpurl_to_str(schema)
            )
        else:
            response = sync_client.beta.chat.completions.parse(
                model=model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": prompt}
                ],
                temperature=temperature,
                response_format=convert_httpurl_to_str(schema)
            )

        return response
    
    except Exception as e:
        logger.error(f"OpenAI API error: {e}")
        return None
