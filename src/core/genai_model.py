import asyncio
import glob
import json
import logging
import os
import tempfile

import aiohttp
import tiktoken
from dotenv import load_dotenv
from openai import AsyncOpenAI

from ..utils.utils import (
    clean_json_string,
    convert_httpurl_to_str,
    is_github_repo_public,
    json_to_jsonLD,
)
from .models import GitHubOrganization, GitHubUser, SoftwareSourceCode
from .prompts import (
    system_prompt_json,
    system_prompt_org_content,
    system_prompt_user_content,
)
from .verification import Verification

# Setup logger first, before anything else
logger = logging.getLogger(__name__)

load_dotenv()

OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY")
OPENROUTER_ENDPOINT = "https://openrouter.ai/api/v1/chat/completions"
MODEL = os.environ.get("MODEL", "gpt-4o")  # Default fallback
PROVIDER = os.environ.get("PROVIDER", "openai")  # Default fallback

# Validate required environment variables
if not OPENROUTER_API_KEY and PROVIDER == "openrouter":
    logger.error("OPENROUTER_API_KEY not found in environment variables")
if not os.environ.get("OPENAI_API_KEY") and PROVIDER == "openai":
    logger.error("OPENAI_API_KEY not found in environment variables")

# Lazy-initialized async OpenAI client (created on first use)
async_openai_client = None


def get_async_openai_client():
    """Get or create the async OpenAI client with proper timeout configuration."""
    global async_openai_client
    if async_openai_client is None:
        api_key = os.environ.get("OPENAI_API_KEY")
        if not api_key:
            logger.error("OPENAI_API_KEY not found in environment variables")
            return None
        async_openai_client = AsyncOpenAI(
            api_key=api_key,
            timeout=600.0,  # 10 minute timeout for GPT-5 and other models
        )
    return async_openai_client


def reduce_input_size(input_text, max_tokens=800000, repo_url=None):
    """
    Reduce the size of the input text to fit within the specified token limit.
    """
    limiter_encoding = tiktoken.get_encoding("cl100k_base")
    tokens = limiter_encoding.encode(input_text)

    url_prefix = f"{repo_url} :: " if repo_url else ""

    logger.info(f"Original amount of tokens: {len(tokens)}")
    if len(tokens) > max_tokens:
        tokens = tokens[:max_tokens]
        reduced_text = limiter_encoding.decode(tokens)
        logger.warning(
            f"{url_prefix}Token count exceeded limit, truncated to {max_tokens} tokens",
        )
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
        ".cff": 0,
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
        with open(file, encoding="utf-8") as f:
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
            "git",
            "clone",
            "-c",
            "core.symlinks=false",
            repo_url,
            temp_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            logger.info("Repository cloned successfully.")
            return temp_dir
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
            "repo-to-text",
            cwd=temp_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            logger.info("repo-to-text command completed successfully.")
            return True
        logger.error(f"'repo-to-text' command failed: {stderr.decode()}")
        return False
    except Exception as e:
        logger.error(f"'repo-to-text' command failed: {e}")
        return False


async def extract_git_authors(temp_dir):
    """
    Extract git authors from the cloned repository using git shortlog.
    Returns a list of GitAuthor objects with commit counts and first/last commit dates.

    Example output from git shortlog -sne:
        120  Alice <alice@example.com>
         95  Bob <bob@example.com>
         10  Carlos <carlos@example.com>
    """
    import re
    from datetime import datetime

    from .models import Commits, GitAuthor

    try:
        # First, get the list of authors with commit counts
        process = await asyncio.create_subprocess_exec(
            "git",
            "shortlog",
            "-sne",
            "--all",
            cwd=temp_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            git_authors = []
            output = stdout.decode("utf-8").strip()

            # Parse each line: "   120  Alice <alice@example.com>"
            # Pattern: optional whitespace, number, whitespace, name, optional email in <>
            pattern = r"^\s*(\d+)\s+(.+?)(?:\s+<([^>]+)>)?$"

            for line in output.split("\n"):
                if not line.strip():
                    continue

                match = re.match(pattern, line)
                if match:
                    total_commits = int(match.group(1))
                    name = match.group(2).strip()
                    email = match.group(3) if match.group(3) else None

                    # Get first and last commit dates for this author
                    # We'll use the email if available, otherwise the name
                    author_identifier = email if email else name

                    # Get first commit date (oldest)
                    first_date_process = await asyncio.create_subprocess_exec(
                        "git",
                        "log",
                        "--author=" + author_identifier,
                        "--format=%ad",
                        "--date=short",
                        "--reverse",
                        "--all",
                        cwd=temp_dir,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    first_stdout, _ = await first_date_process.communicate()

                    # Get last commit date (newest)
                    last_date_process = await asyncio.create_subprocess_exec(
                        "git",
                        "log",
                        "--author=" + author_identifier,
                        "--format=%ad",
                        "--date=short",
                        "--all",
                        "-1",
                        cwd=temp_dir,
                        stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE,
                    )
                    last_stdout, _ = await last_date_process.communicate()

                    # Parse dates
                    first_commit_date = None
                    last_commit_date = None

                    if first_date_process.returncode == 0:
                        first_date_str = (
                            first_stdout.decode("utf-8").strip().split("\n")[0]
                            if first_stdout.decode("utf-8").strip()
                            else None
                        )
                        if first_date_str:
                            try:
                                first_commit_date = datetime.strptime(
                                    first_date_str,
                                    "%Y-%m-%d",
                                ).date()
                            except ValueError:
                                logger.warning(
                                    f"Failed to parse first commit date: {first_date_str}",
                                )

                    if last_date_process.returncode == 0:
                        last_date_str = (
                            last_stdout.decode("utf-8").strip().split("\n")[0]
                            if last_stdout.decode("utf-8").strip()
                            else None
                        )
                        if last_date_str:
                            try:
                                last_commit_date = datetime.strptime(
                                    last_date_str,
                                    "%Y-%m-%d",
                                ).date()
                            except ValueError:
                                logger.warning(
                                    f"Failed to parse last commit date: {last_date_str}",
                                )

                    # Create Commits object
                    commits = Commits(
                        total=total_commits,
                        firstCommitDate=first_commit_date,
                        lastCommitDate=last_commit_date,
                    )

                    git_authors.append(
                        GitAuthor(name=name, email=email, commits=commits),
                    )

            logger.info(f"Extracted {len(git_authors)} git authors from repository.")
            return git_authors
        logger.error(f"Failed to extract git authors: {stderr.decode()}")
        return []
    except Exception as e:
        logger.error(f"Failed to extract git authors: {e}")
        return []


def sanitize_special_tokens(text):
    """
    Remove special tokens by replacing them with safe placeholders.
    This prevents encoding errors when sending to OpenAI API.
    """
    import re

    # List of known special tokens that can cause issues
    special_tokens_patterns = [
        r"<\|endoftext\|>",
        r"<\|startoftext\|>",
        r"<\|fim_prefix\|>",
        r"<\|fim_suffix\|>",
        r"<\|fim_middle\|>",
    ]

    # Replace all special tokens with safe placeholders
    clean_text = text
    for pattern in special_tokens_patterns:
        clean_text = re.sub(pattern, "[SPECIAL_TOKEN]", clean_text, flags=re.IGNORECASE)

    return clean_text


async def llm_request_repo_infos(
    repo_url,
    output_format="json-ld",
    gimie_output=None,
    max_tokens=40000,
):
    """
    Async version of llm_request_repo_infos
    """
    # Check if the repository is public before proceeding
    if not is_github_repo_public(repo_url):
        logger.error(
            f"Cannot process repository: {repo_url} is not public or not accessible",
        )
        return None

    # Clone the GitHub repository into a temporary folder
    with tempfile.TemporaryDirectory() as temp_dir:
        # Clone repository asynchronously
        clone_result = await clone_repo(repo_url, temp_dir)
        if not clone_result:
            return None

        # Extract git authors from the cloned repository
        git_authors = await extract_git_authors(temp_dir)

        # Run repo-to-text asynchronously
        repo_to_text_success = await run_repo_to_text(temp_dir)
        if not repo_to_text_success:
            return None

        input_text = combine_text_files(temp_dir)
        input_text = sanitize_special_tokens(input_text)
        input_text = reduce_input_size(
            input_text,
            max_tokens=max_tokens,
            repo_url=repo_url,
        )

        if gimie_output:
            # Sanitize GIMIE output to remove special tokens before adding
            gimie_text = str(gimie_output)
            gimie_text = sanitize_special_tokens(gimie_text)
            input_text += "\n\n" + gimie_text

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
                # All OpenAI models now use .parsed with beta.chat.completions.parse
                json_data = response.choices[0].message.parsed
                logger.info("Clean result from OpenAI response:")
                json_data = json_data.model_dump(mode="json")

            logger.info("Successfully JSON API response")

            # Add git authors to the JSON data
            if git_authors:
                json_data["gitAuthors"] = [
                    {
                        "name": author.name,
                        "email": author.email,
                        "commits": {
                            "total": author.commits.total,
                            "firstCommitDate": (
                                author.commits.firstCommitDate.isoformat()
                                if author.commits.firstCommitDate
                                else None
                            ),
                            "lastCommitDate": (
                                author.commits.lastCommitDate.isoformat()
                                if author.commits.lastCommitDate
                                else None
                            ),
                        }
                        if author.commits
                        else None,
                    }
                    for author in git_authors
                ]

            # Run verification before converting to JSON-LD
            verifier = Verification(json_data, repo_url)
            verifier.run()
            verifier.summary()

            cleaned_json = verifier.sanitize_metadata()

            context_path = "src/files/json-ld-context.json"
            if output_format == "json-ld":
                return json_to_jsonLD(cleaned_json, context_path)
            if output_format == "json":
                return cleaned_json
            logger.error(f"Unsupported output format: {output_format}")
            return None

        except Exception as e:
            logger.error(f"Error parsing response: {e}")
            return None


async def get_openrouter_response_async(
    input_text,
    system_prompt=system_prompt_json,
    model="google/gemini-2.5-flash",
    temperature=0.2,
    schema=SoftwareSourceCode,
):
    """
    Get structured response from openrouter asynchronously
    """
    # Sanitize the input to remove special tokens
    input_text = sanitize_special_tokens(input_text)
    system_prompt = sanitize_special_tokens(system_prompt)

    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": input_text},
        ],
        "response_format": {
            "type": "json_schema",
            "json_schema": schema.model_json_schema(),
        },
        "temperature": temperature,
    }

    headers = {
        "Authorization": f"Bearer {OPENROUTER_API_KEY}",
        "Content-Type": "application/json",
    }

    timeout = aiohttp.ClientTimeout(total=300)  # 5 minute timeout

    for attempt in range(3):
        try:
            async with aiohttp.ClientSession(timeout=timeout) as session:
                async with session.post(
                    OPENROUTER_ENDPOINT,
                    headers=headers,
                    json=payload,
                ) as response:
                    logger.info(f"API response status: {response.status}")
                    if response.status == 200:
                        return await response.json()
                    logger.error(
                        f"API request failed with status {response.status}",
                    )
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


async def get_openai_response_async(
    prompt,
    system_prompt=system_prompt_json,
    model="gpt-4o",
    temperature=0.2,
    schema=SoftwareSourceCode,
):
    """
    Get structured response from OpenAI API using SoftwareSourceCode schema asynchronously.
    """
    # Sanitize the prompt to remove special tokens
    prompt = sanitize_special_tokens(prompt)
    system_prompt = sanitize_special_tokens(system_prompt)

    # Get or create the async OpenAI client
    client = get_async_openai_client()
    if not client:
        logger.error("Failed to initialize OpenAI client")
        return None

    # Log the model being used
    logger.info(f"Making OpenAI API call with model: {model}")

    # Retry logic for connection errors
    for attempt in range(3):
        try:
            # Use the async OpenAI client
            # GPT-5 and reasoning models (o3, o4) have different requirements
            if model.startswith("gpt-5"):
                # GPT-5: use beta.parse like other models, it should work with structured outputs
                logger.info(
                    f"Using GPT-5 model configuration with structured outputs for: {model}",
                )
                response = await client.beta.chat.completions.parse(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    response_format=convert_httpurl_to_str(schema),
                    max_tokens=16000,
                )
            elif model.split("-")[0] == "o3" or model.split("-")[0] == "o4":
                # O3/O4 reasoning models: use beta parse without temperature
                logger.info(f"Using reasoning model configuration for: {model}")
                response = await client.beta.chat.completions.parse(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    response_format=convert_httpurl_to_str(schema),
                    max_tokens=16000,
                )
            else:
                # Standard models (gpt-4o, etc.): use beta parse with temperature
                logger.info(f"Using standard model configuration for: {model}")
                response = await client.beta.chat.completions.parse(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=temperature,
                    response_format=convert_httpurl_to_str(schema),
                    max_tokens=16000,
                )

            logger.info(f"Successfully received response from {model}")
            return response

        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)
            logger.error(
                f"OpenAI API error (attempt {attempt + 1}/{3}): [{error_type}] {error_msg}",
            )
            logger.error(f"Model: {model}, Error details: {e!r}")
            if attempt == 2:  # Last attempt
                return None
            # Wait before retry
            await asyncio.sleep(2**attempt)  # Exponential backoff


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
        response = await get_openrouter_response_async(
            input_text,
            system_prompt=system_prompt,
            model=MODEL,
            schema=schema,
        )
    elif PROVIDER == "openai":
        response = await get_openai_response_async(
            input_text,
            system_prompt=system_prompt,
            model=MODEL,
            schema=schema,
        )
    else:
        logger.error("No provider provided")
        return None

    try:
        if PROVIDER == "openrouter":
            raw_result = response["choices"][0]["message"]["content"]
            parsed_result = clean_json_string(raw_result)
            json_data = json.loads(parsed_result)
        elif PROVIDER == "openai":
            # All OpenAI models now use .parsed with beta.chat.completions.parse
            json_data = response.choices[0].message.parsed
            json_data = json_data.model_dump(mode="json")
        else:
            logger.error("Unknown provider")
            return None

        logger.info("Successfully parsed API response")
        return json_data

    except Exception as e:
        logger.error(f"Error parsing response: {e}")
        return None


# Keep the synchronous versions for backward compatibility
def get_openrouter_response(
    input_text,
    system_prompt=system_prompt_json,
    model="google/gemini-2.5-flash",
    temperature=0.2,
    schema=SoftwareSourceCode,
):
    """
    Synchronous wrapper for backward compatibility
    """
    import asyncio

    return asyncio.run(
        get_openrouter_response_async(
            input_text,
            system_prompt,
            model,
            temperature,
            schema,
        ),
    )


def get_openai_response(
    prompt,
    system_prompt=system_prompt_json,
    model="gpt-4o",
    temperature=0.2,
    schema=SoftwareSourceCode,
):
    """
    Synchronous wrapper for backward compatibility
    """
    import time

    from openai import OpenAI

    # Check API key first
    api_key = os.environ.get("OPENAI_API_KEY")
    if not api_key:
        logger.error("OPENAI_API_KEY not found in environment variables")
        return None

    # Log the model being used
    logger.info(f"Making sync OpenAI API call with model: {model}")

    # Retry logic for connection errors
    for attempt in range(3):
        try:
            sync_client = OpenAI(
                api_key=api_key,
                timeout=600.0,  # 10 minute timeout for GPT-5 and other models
            )

            # GPT-5 and reasoning models (o3, o4) have different requirements
            if model.startswith("gpt-5"):
                # GPT-5: use beta.parse like other models, it should work with structured outputs
                logger.info(
                    f"Using GPT-5 model configuration with structured outputs for: {model}",
                )
                response = sync_client.beta.chat.completions.parse(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    response_format=convert_httpurl_to_str(schema),
                )
            elif model.split("-")[0] == "o3" or model.split("-")[0] == "o4":
                # O3/O4 reasoning models: use beta parse without temperature
                logger.info(f"Using reasoning model configuration for: {model}")
                response = sync_client.beta.chat.completions.parse(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    response_format=convert_httpurl_to_str(schema),
                )
            else:
                # Standard models (gpt-4o, etc.): use beta parse with temperature
                logger.info(f"Using standard model configuration for: {model}")
                response = sync_client.beta.chat.completions.parse(
                    model=model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompt},
                    ],
                    temperature=temperature,
                    response_format=convert_httpurl_to_str(schema),
                )

            logger.info(f"Successfully received response from {model}")
            return response

        except Exception as e:
            error_type = type(e).__name__
            error_msg = str(e)
            logger.error(
                f"OpenAI API error (attempt {attempt + 1}/{3}): [{error_type}] {error_msg}",
            )
            logger.error(f"Model: {model}, Error details: {e!r}")
            if attempt == 2:  # Last attempt
                return None
            # Wait before retry
            time.sleep(2**attempt)  # Exponential backoff
