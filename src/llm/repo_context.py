"""
Repository Context Generation

Handles repository cloning, text extraction, and context preparation for LLM analysis.
Extracted from genai_model.py to improve modularity.
"""

import asyncio
import glob
import logging
import os
import tempfile
from datetime import datetime
from typing import Any, Dict, List, Optional

from ..data_models import Commits, GitAuthor

logger = logging.getLogger(__name__)


async def clone_repo(repo_url: str, temp_dir: str) -> Optional[str]:
    """
    Clone a GitHub repository into a temporary directory asynchronously.

    Args:
        repo_url: Repository URL to clone
        temp_dir: Temporary directory path

    Returns:
        Path to cloned repository or None if failed
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
            # Check what was cloned
            if os.path.exists(temp_dir):
                contents = os.listdir(temp_dir)
                logger.debug(f"Cloned repository contains {len(contents)} items")
                logger.debug(f"First 10 items: {contents[:10]}")

                # Check if .git directory exists
                git_dir = os.path.join(temp_dir, ".git")
                if os.path.exists(git_dir):
                    logger.debug(".git directory exists")
                else:
                    logger.warning(f".git directory not found in {temp_dir}")
            return temp_dir

        stderr_text = stderr.decode()
        logger.error(
            f"Failed to clone repository with return code {process.returncode}",
        )
        logger.error(f"stderr: {stderr_text}")
        if stdout:
            logger.debug(f"stdout: {stdout.decode()[:500]}")
        return None
    except Exception as e:
        logger.error(f"Failed to clone repository with exception: {e}", exc_info=True)
        return None


async def run_repo_to_text(temp_dir: str) -> bool:
    """
    Run the repo-to-text command asynchronously.

    Args:
        temp_dir: Directory containing the cloned repository

    Returns:
        True if successful, False otherwise
    """
    try:
        logger.debug(f"Running repo-to-text in directory: {temp_dir}")

        # Check if directory exists and list its contents
        if os.path.exists(temp_dir):
            logger.debug(
                f"Directory exists. Contents: {os.listdir(temp_dir)[:10]}",
            )  # Show first 10 items
        else:
            logger.error(f"Directory does not exist: {temp_dir}")
            return False

        process = await asyncio.create_subprocess_exec(
            "repo-to-text",
            "--ignore-patterns",
            "*.log",
            "temp/",
            "*.lock",
            ".git",
            ".github",
            cwd=temp_dir,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await process.communicate()

        if process.returncode == 0:
            logger.info("repo-to-text command completed successfully.")
            logger.debug(f"repo-to-text stdout length: {len(stdout)} bytes")
            return True

        stderr_text = stderr.decode()
        stdout_text = stdout.decode() if stdout else ""
        logger.error(
            f"'repo-to-text' command failed with return code {process.returncode}",
        )
        logger.error(f"Full stderr output:\n{stderr_text}")
        if stdout_text:
            logger.error(f"Full stdout output:\n{stdout_text}")
        return False
    except Exception as e:
        logger.error(
            f"'repo-to-text' command failed with exception: {e}",
            exc_info=True,
        )
        return False


def combine_text_files(directory: str) -> str:
    """
    Combine all text files in the specified directory into a single string.

    Args:
        directory: Directory containing .txt files

    Returns:
        Combined text content
    """
    combined_text = ""
    txt_files = glob.glob(os.path.join(directory, "*.txt"))

    logger.info(f"Found {len(txt_files)} text files in {directory}")

    # Debug: List all files in directory to see what's actually there
    if len(txt_files) == 0:
        all_files = glob.glob(os.path.join(directory, "*"))
        logger.debug(
            f"No .txt files found. All files in directory: {[os.path.basename(f) for f in all_files[:20]]}",
        )

    for file in txt_files:
        logger.debug(f"Reading file: {file}")
        with open(file, encoding="utf-8") as f:
            combined_text += f.read() + "\n"

    return combined_text


def store_combined_text(input_text: str, output_file: str) -> str:
    """
    Store the combined text into a specified output file.

    Args:
        input_text: Text content to store
        output_file: Output file path

    Returns:
        Path to the output file
    """
    with open(output_file, "w", encoding="utf-8") as f:
        f.write(input_text)
    logger.info(f"Combined text saved to {output_file}")
    return output_file


async def extract_git_authors(temp_dir: str) -> List[GitAuthor]:
    """
    Extract git authors from the cloned repository using git shortlog.
    Returns a list of GitAuthor objects with commit counts and first/last commit dates.

    Example output from git shortlog -sne:
        120  Alice <alice@example.com>
         95  Bob <bob@example.com>
         10  Carlos <carlos@example.com>

    Args:
        temp_dir: Directory containing the cloned repository

    Returns:
        List of GitAuthor objects
    """
    import re

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


def sanitize_special_tokens(text: str) -> str:
    """
    Remove special tokens by replacing them with safe placeholders.
    This prevents encoding errors when sending to OpenAI API.

    Args:
        text: Input text to sanitize

    Returns:
        Sanitized text
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


def reduce_input_size(
    input_text: str,
    max_tokens: int = 400000,
    repo_url: Optional[str] = None,
) -> str:
    """
    Reduce the size of the input text to fit within the specified token limit.
    Reduced from 800k to 400k to prevent excessive memory usage.

    Args:
        input_text: Input text to reduce
        max_tokens: Maximum number of tokens allowed
        repo_url: Optional repository URL for logging

    Returns:
        Reduced text if necessary
    """
    import tiktoken

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


def sort_files_by_priority(file_paths: List[str]) -> List[str]:
    """
    Sorts a list of file paths based on a predefined extension priority.

    The order is:
    1. Documentation files (.md, .txt, .html)
    2. Code files (.py, .r)
    3. All other files

    Args:
        file_paths: List of file paths to sort

    Returns:
        Sorted list of file paths
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


async def prepare_repository_context(
    repo_url: str,
    max_tokens: int = 400000,
) -> Dict[str, Any]:
    """
    Prepare repository context by cloning, extracting text, and getting git authors.

    Args:
        repo_url: Repository URL to process
        max_tokens: Maximum tokens for text content

    Returns:
        Dictionary containing:
        - input_text: Combined text content
        - git_authors: List of GitAuthor objects
        - success: Boolean indicating success
        - error: Error message if failed
    """
    result = {
        "input_text": "",
        "git_authors": [],
        "success": False,
        "error": None,
    }

    # Clone the GitHub repository into a temporary folder
    with tempfile.TemporaryDirectory() as temp_dir:
        # Clone repository asynchronously
        clone_result = await clone_repo(repo_url, temp_dir)
        if not clone_result:
            result["error"] = "Failed to clone repository"
            return result

        # Run repo-to-text asynchronously to check if repository has content
        repo_to_text_success = await run_repo_to_text(temp_dir)
        if not repo_to_text_success:
            logger.warning(
                f"repo-to-text failed for {repo_url}, but will attempt to continue with available .txt files",
            )
            # Don't return None immediately - check if there are any .txt files anyway

        # Check early if repository has any analyzable content
        input_text = combine_text_files(temp_dir)
        input_text = sanitize_special_tokens(input_text)

        # Early exit for empty repositories - skip expensive operations
        if not input_text or len(input_text.strip()) < 10:
            logger.warning(
                f"Repository {repo_url} has no analyzable content (empty or minimal). Skipping further analysis.",
            )
            result["error"] = "Repository has no analyzable content"
            return result

        # Continue with normal processing for non-empty repositories
        # Extract git authors from the cloned repository
        git_authors = await extract_git_authors(temp_dir)

        input_text = reduce_input_size(
            input_text,
            max_tokens=max_tokens,
            repo_url=repo_url,
        )

        result.update(
            {
                "input_text": input_text,
                "git_authors": git_authors,
                "success": True,
            },
        )

        return result
