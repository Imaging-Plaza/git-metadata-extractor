"""
Repository Context Generation

Handles repository cloning, text extraction, and context preparation for LLM analysis.
Extracted from genai_model.py to improve modularity.
"""

import asyncio
import json
import logging
import os
import re
import shutil
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from bs4 import BeautifulSoup

from ..data_models import Commits, GitAuthor
from ..utils.utils import sanitize_special_tokens

logger = logging.getLogger(__name__)

# File size limit (1 MB)
MAX_FILE_SIZE = 1024 * 1024  # 1 MB in bytes

# Directories to skip during repository traversal
SKIP_DIRECTORIES = {
    ".git",
    "node_modules",
    "__pycache__",
    ".venv",
    "venv",
    "dist",
    "build",
    ".eggs",
    "*.egg-info",
    ".tox",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    "htmlcov",
    ".coverage",
}

# File extensions and patterns to include
DOCUMENTATION_EXTENSIONS = {".md", ".txt", ".rst", ".cff"}
CODE_EXTENSIONS = {".py", ".r"}
CONFIG_EXTENSIONS = {
    ".json",
    ".yaml",
    ".yml",
    ".toml",
    ".ini",
    ".cfg",
    ".env",
}
RICH_CONTENT_EXTENSIONS = {".html", ".ipynb"}
CONFIG_FILENAMES = {
    "requirements.txt",
    "setup.py",
    "pyproject.toml",
    "Makefile",
    "Dockerfile",
    ".dockerignore",
    ".gitignore",
    # Citation and attribution metadata files
    "CITATION.cff",
    "codemeta.json",
}

# Additional important documentation and metadata files (without extensions)
IMPORTANT_FILENAMES = {
    "AUTHORS",
    "CONTRIBUTORS",
    "CHANGELOG",
    "CHANGES",
    "HISTORY",
    "NOTICE",
    "CODE_OF_CONDUCT",
    "SECURITY",
    "SUPPORT",
    "ACKNOWLEDGMENTS",
    "ACKNOWLEDGEMENTS",
    "THANKS",
    # Additional attribution files
    "ATTRIBUTION",
    "ATTRIBUTIONS",
}

# All relevant extensions combined
RELEVANT_EXTENSIONS = (
    DOCUMENTATION_EXTENSIONS
    | CODE_EXTENSIONS
    | CONFIG_EXTENSIONS
    | RICH_CONTENT_EXTENSIONS
)

# Extraction mode constants
EXTRACTION_MODE_README_ONLY = "readme_only"
EXTRACTION_MODE_MARKDOWN_ONLY = "markdown_only"
EXTRACTION_MODE_ALL = "all"


async def clone_repo(
    repo_url: str,
    temp_dir: str,
    max_retries: int = 3,
) -> Optional[str]:
    """
    Clone a GitHub repository into a temporary directory asynchronously.
    Includes retry logic and optimizations for large repositories.

    Args:
        repo_url: Repository URL to clone
        temp_dir: Temporary directory path
        max_retries: Maximum number of retry attempts

    Returns:
        Path to cloned repository or None if failed
    """
    logger.info(f"Cloning {repo_url} into {temp_dir}...")

    for attempt in range(1, max_retries + 1):
        try:
            logger.info(f"Clone attempt {attempt}/{max_retries}")

            # Clean up any partial clone from previous attempt
            if attempt > 1 and os.path.exists(temp_dir):
                try:
                    shutil.rmtree(temp_dir)
                    logger.debug("Cleaned up partial clone from previous attempt")
                except Exception as e:
                    logger.warning(f"Failed to clean up partial clone: {e}")

            process = await asyncio.create_subprocess_exec(
                "git",
                "clone",
                # Configuration for large repositories and network reliability
                "-c",
                "core.symlinks=false",
                "-c",
                "http.postBuffer=524288000",  # 500 MB buffer
                "-c",
                "http.lowSpeedLimit=1000",  # 1KB/s minimum speed
                "-c",
                "http.lowSpeedTime=60",  # for 60 seconds
                "--progress",  # Show progress
                repo_url,
                temp_dir,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Use asyncio.wait_for to add timeout
            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=600.0,  # 10 minute timeout
                )
            except asyncio.TimeoutError:
                logger.error(f"Clone attempt {attempt} timed out after 10 minutes")
                process.kill()
                await process.wait()
                if attempt < max_retries:
                    logger.info(
                        f"Retrying clone (attempt {attempt + 1}/{max_retries})...",
                    )
                    await asyncio.sleep(2)  # Brief delay before retry
                    continue
                return None

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

            # Check if error is retryable (network issues)
            retryable_errors = [
                "Connection reset by peer",
                "RPC failed",
                "early EOF",
                "fetch-pack: invalid index-pack output",
                "unexpected disconnect",
                "Connection timed out",
                "Failed to connect",
                "Recv failure",  # curl error: "curl 56 Recv failure: Connection reset by peer"
            ]

            if any(error in stderr_text for error in retryable_errors):
                if attempt < max_retries:
                    logger.warning(
                        f"Network error detected, retrying (attempt {attempt + 1}/{max_retries})...",
                    )
                    await asyncio.sleep(2**attempt)  # Exponential backoff
                    continue

            # Non-retryable error, return None
            return None

        except Exception as e:
            logger.error(
                f"Failed to clone repository with exception: {e}",
                exc_info=True,
            )
            if attempt < max_retries:
                logger.info(f"Retrying clone (attempt {attempt + 1}/{max_retries})...")
                await asyncio.sleep(2**attempt)  # Exponential backoff
                continue
            return None

    logger.error(f"Failed to clone repository after {max_retries} attempts")
    return None


def is_binary_file(filepath: str) -> bool:
    """
    Check if a file is binary by reading the first 8192 bytes.

    Args:
        filepath: Path to the file

    Returns:
        True if binary, False if text
    """
    try:
        with open(filepath, "rb") as f:
            chunk = f.read(8192)
            if b"\0" in chunk:  # Null bytes indicate binary
                return True
            # Check for high proportion of non-text bytes
            text_chars = bytearray({7, 8, 9, 10, 12, 13, 27} | set(range(0x20, 0x100)))
            non_text = sum(1 for byte in chunk if byte not in text_chars)
            return non_text / len(chunk) > 0.3 if chunk else False
    except Exception:
        return True


def is_relevant_file(
    filepath: str,
    filename: str,
    extraction_mode: str = EXTRACTION_MODE_README_ONLY,
) -> bool:
    """
    Check if a file is relevant for extraction based on the extraction mode.

    Args:
        filepath: Full path to the file
        filename: Name of the file
        extraction_mode: Extraction mode ("readme_only", "markdown_only", or "all")

    Returns:
        True if relevant, False otherwise
    """
    # Check file size
    try:
        if os.path.getsize(filepath) > MAX_FILE_SIZE:
            logger.debug(f"Skipping {filepath}: exceeds size limit")
            return False
    except OSError:
        return False

    # Check by filename (case-insensitive for special files)
    lower_filename = filename.lower()
    upper_filename = filename.upper()
    basename_upper = os.path.splitext(filename)[0].upper()

    # README-only mode: only README and AUTHORS files
    if extraction_mode == EXTRACTION_MODE_README_ONLY:
        # Check for README files (case-insensitive, with or without extension)
        if lower_filename.startswith("readme"):
            return True
        # Check for AUTHORS files (case-insensitive, with or without extension)
        if basename_upper == "AUTHORS" or upper_filename == "AUTHORS":
            return True
        return False

    # Markdown-only mode: only .md files
    if extraction_mode == EXTRACTION_MODE_MARKDOWN_ONLY:
        _, ext = os.path.splitext(filename)
        ext_lower = ext.lower()
        if ext_lower == ".md":
            # Additional check for binary files
            if not is_binary_file(filepath):
                return True
        return False

    # All mode: current behavior (all existing checks)
    # Check for README, LICENSE, CITATION (with or without extensions)
    if any(
        lower_filename.startswith(name.lower()) or lower_filename == name.lower()
        for name in ["readme", "license", "citation"]
    ):
        return True

    # Check for config files with specific names (case-sensitive)
    if filename in CONFIG_FILENAMES:
        return True

    # Check for important documentation files (typically uppercase, no extension)
    if upper_filename in IMPORTANT_FILENAMES:
        return True

    # Also check with common extensions for these files
    if basename_upper in IMPORTANT_FILENAMES:
        return True

    # Check by extension
    _, ext = os.path.splitext(filename)
    ext_lower = ext.lower()

    if ext_lower in RELEVANT_EXTENSIONS:
        # Additional check for binary files
        if not is_binary_file(filepath):
            return True

    return False


def walk_repository_tree(
    repo_dir: str,
    extraction_mode: str = EXTRACTION_MODE_README_ONLY,
) -> Tuple[List[str], str]:
    """
    Walk the repository directory tree and collect relevant files.

    Args:
        repo_dir: Root directory of the repository
        extraction_mode: Extraction mode ("readme_only", "markdown_only", or "all")

    Returns:
        Tuple of (list of file paths, tree structure as string)
    """
    relevant_files = []
    tree_lines = []

    repo_path = Path(repo_dir)

    def should_skip_directory(dir_name: str) -> bool:
        """Check if directory should be skipped."""
        return dir_name in SKIP_DIRECTORIES or dir_name.startswith(".")

    def build_tree(directory: Path, prefix: str = "", is_last: bool = True):
        """Recursively build tree structure."""
        try:
            items = sorted(directory.iterdir(), key=lambda x: (not x.is_dir(), x.name))
        except PermissionError:
            return

        # Filter items based on extraction mode
        # In restricted modes, only include relevant files/directories
        filtered_items = []
        for item in items:
            # Skip hidden files and unwanted directories
            if item.name.startswith(".") and item.name not in {
                ".env",
                ".gitignore",
                ".dockerignore",
            }:
                continue

            if item.is_dir() and should_skip_directory(item.name):
                continue

            # In restricted modes, skip files that aren't relevant
            if item.is_file():
                if not is_relevant_file(str(item), item.name, extraction_mode):
                    continue
                # File is relevant, add to both tree and relevant_files
                relevant_files.append(str(item))

            # For directories, we need to check if they contain any relevant files
            # In restricted modes, we'll only include directories that have relevant content
            if item.is_dir():
                # Check if directory contains any relevant files
                if extraction_mode in {
                    EXTRACTION_MODE_README_ONLY,
                    EXTRACTION_MODE_MARKDOWN_ONLY,
                }:
                    # In restricted modes, check if directory has relevant content
                    has_relevant_content = False
                    try:
                        for subitem in item.rglob("*"):
                            if subitem.is_file() and is_relevant_file(
                                str(subitem),
                                subitem.name,
                                extraction_mode,
                            ):
                                has_relevant_content = True
                                break
                    except (PermissionError, OSError):
                        pass

                    if not has_relevant_content:
                        continue

            filtered_items.append(item)

        # Build tree from filtered items
        for index, item in enumerate(filtered_items):
            is_last_item = index == len(filtered_items) - 1

            # Tree formatting
            connector = "└── " if is_last_item else "├── "
            tree_lines.append(f"{prefix}{connector}{item.name}")

            if item.is_dir():
                extension = "    " if is_last_item else "│   "
                build_tree(item, prefix + extension, is_last_item)

    # Build the tree
    tree_lines.append(f"{repo_path.name}/")
    build_tree(repo_path)

    tree_structure = "\n".join(tree_lines)
    logger.info(f"Found {len(relevant_files)} relevant files in repository")

    return relevant_files, tree_structure


def extract_plain_text(filepath: str) -> str:
    """
    Extract plain text from a file with encoding fallback.

    Args:
        filepath: Path to the file

    Returns:
        Text content
    """
    encodings = ["utf-8", "latin-1", "cp1252"]

    for encoding in encodings:
        try:
            with open(filepath, encoding=encoding) as f:
                return f.read()
        except (UnicodeDecodeError, LookupError):
            continue

    logger.warning(f"Could not decode {filepath} with common encodings")
    return f"[Could not decode file: {filepath}]"


def extract_html_text(filepath: str) -> str:
    """
    Extract text from HTML file using BeautifulSoup.

    Args:
        filepath: Path to HTML file

    Returns:
        Extracted text content
    """
    try:
        content = extract_plain_text(filepath)
        soup = BeautifulSoup(content, "html.parser")

        # Remove script and style elements
        for script in soup(["script", "style"]):
            script.decompose()

        # Get text
        text = soup.get_text()

        # Clean up whitespace
        lines = (line.strip() for line in text.splitlines())
        chunks = (phrase.strip() for line in lines for phrase in line.split("  "))
        text = "\n".join(chunk for chunk in chunks if chunk)

        return text
    except Exception as e:
        logger.warning(f"Failed to extract HTML from {filepath}: {e}")
        return extract_plain_text(filepath)


def extract_notebook_cells(filepath: str) -> str:
    """
    Extract text and code from Jupyter notebook cells.

    Args:
        filepath: Path to .ipynb file

    Returns:
        Extracted content with cell separators
    """
    try:
        with open(filepath, encoding="utf-8") as f:
            notebook = json.load(f)

        extracted = []
        cells = notebook.get("cells", [])

        for idx, cell in enumerate(cells, 1):
            cell_type = cell.get("cell_type", "unknown")
            source = cell.get("source", [])

            # Handle source as list or string
            if isinstance(source, list):
                content = "".join(source)
            else:
                content = source

            if content.strip():
                extracted.append(f"### Cell {idx} ({cell_type})")
                extracted.append(content)
                extracted.append("")  # Empty line for separation

        return "\n".join(extracted)
    except Exception as e:
        logger.warning(f"Failed to extract notebook {filepath}: {e}")
        return f"[Could not parse notebook: {filepath}]"


def extract_file_content(filepath: str) -> str:
    """
    Extract content from a file based on its type.

    Args:
        filepath: Path to the file

    Returns:
        Extracted content
    """
    _, ext = os.path.splitext(filepath)
    ext_lower = ext.lower()

    try:
        if ext_lower == ".html":
            return extract_html_text(filepath)
        elif ext_lower == ".ipynb":
            return extract_notebook_cells(filepath)
        else:
            return extract_plain_text(filepath)
    except Exception as e:
        logger.error(f"Error extracting content from {filepath}: {e}")
        return f"[Error extracting content: {e}]"


def extract_python_imports(content: str) -> Set[str]:
    """
    Extract Python imports from code content.

    Args:
        content: Python code content

    Returns:
        Set of imported modules
    """
    imports = set()

    # Pattern for "import module" or "import module as alias"
    import_pattern = r"^\s*import\s+([\w.]+)"

    # Pattern for "from module import ..."
    from_pattern = r"^\s*from\s+([\w.]+)\s+import"

    for line in content.split("\n"):
        # Match regular imports
        match = re.match(import_pattern, line)
        if match:
            imports.add(match.group(1))
            continue

        # Match from imports
        match = re.match(from_pattern, line)
        if match:
            imports.add(match.group(1))

    return imports


def extract_r_imports(content: str) -> Set[str]:
    """
    Extract R library/package imports from code content.

    Args:
        content: R code content

    Returns:
        Set of imported packages
    """
    imports = set()

    # Patterns for library() and require()
    patterns = [
        r"library\s*\(\s*['\"]?(\w+)['\"]?\s*\)",
        r"require\s*\(\s*['\"]?(\w+)['\"]?\s*\)",
    ]

    for line in content.split("\n"):
        for pattern in patterns:
            matches = re.findall(pattern, line)
            imports.update(matches)

    return imports


def generate_repository_markdown(
    repo_dir: str,
    extraction_mode: str = EXTRACTION_MODE_README_ONLY,
) -> str:
    """
    Generate comprehensive markdown documentation of repository contents.

    Args:
        repo_dir: Root directory of the repository
        extraction_mode: Extraction mode ("readme_only", "markdown_only", or "all")

    Returns:
        Markdown formatted string with repository information
    """
    logger.info(f"Generating repository markdown for {repo_dir}")

    # Walk repository and collect files
    file_paths, tree_structure = walk_repository_tree(repo_dir, extraction_mode)

    # Log file extraction statistics
    if file_paths:
        # Categorize files by type
        readme_files = []
        authors_files = []
        markdown_files = []
        other_files = []

        for filepath in file_paths:
            filename = os.path.basename(filepath)
            lower_filename = filename.lower()
            basename, ext = os.path.splitext(filename)

            if lower_filename.startswith("readme"):
                readme_files.append(filepath)
            elif basename.upper() == "AUTHORS" or filename.upper() == "AUTHORS":
                authors_files.append(filepath)
            elif ext.lower() == ".md":
                markdown_files.append(filepath)
            else:
                other_files.append(filepath)

        # Log summary
        logger.info(
            f"Extraction mode '{extraction_mode}': Found {len(file_paths)} file(s) - "
            f"README: {len(readme_files)}, AUTHORS: {len(authors_files)}, "
            f"Markdown: {len(markdown_files)}, Other: {len(other_files)}",
        )

        # Log file paths (up to 10 files to avoid log spam)
        if readme_files:
            logger.info(f"README files found: {readme_files[:10]}")
            if len(readme_files) > 10:
                logger.info(f"... and {len(readme_files) - 10} more README files")
        if authors_files:
            logger.info(f"AUTHORS files found: {authors_files}")
        if markdown_files and extraction_mode == EXTRACTION_MODE_MARKDOWN_ONLY:
            logger.info(f"Markdown files found: {markdown_files[:10]}")
            if len(markdown_files) > 10:
                logger.info(f"... and {len(markdown_files) - 10} more markdown files")
        if other_files and extraction_mode == EXTRACTION_MODE_ALL:
            logger.debug(f"Other files found: {other_files[:10]}")
            if len(other_files) > 10:
                logger.debug(f"... and {len(other_files) - 10} more files")
    else:
        logger.warning(
            f"No files found for extraction mode '{extraction_mode}' in {repo_dir}",
        )

    markdown_parts = []

    # Section 1: Repository Tree Structure
    markdown_parts.append("# Repository Structure\n")
    markdown_parts.append("```")
    markdown_parts.append(tree_structure)
    markdown_parts.append("```\n")

    # Section 2: Aggregate imports
    python_imports = set()
    r_imports = set()

    # First pass: collect all imports
    for filepath in file_paths:
        _, ext = os.path.splitext(filepath)
        ext_lower = ext.lower()

        if ext_lower == ".py":
            content = extract_plain_text(filepath)
            python_imports.update(extract_python_imports(content))
        elif ext_lower in {".r"}:
            content = extract_plain_text(filepath)
            r_imports.update(extract_r_imports(content))

    # Add imports section
    if python_imports or r_imports:
        markdown_parts.append("# Imported Libraries\n")

        if python_imports:
            markdown_parts.append("## Python Imports\n")
            for imp in sorted(python_imports):
                markdown_parts.append(f"- {imp}")
            markdown_parts.append("")

        if r_imports:
            markdown_parts.append("## R Packages\n")
            for imp in sorted(r_imports):
                markdown_parts.append(f"- {imp}")
            markdown_parts.append("")

    # Section 3: File Contents
    markdown_parts.append("# File Contents\n")

    repo_path = Path(repo_dir)

    for filepath in file_paths:
        relative_path = Path(filepath).relative_to(repo_path)
        file_size = os.path.getsize(filepath)
        file_size_kb = file_size / 1024

        markdown_parts.append(f"## File: {relative_path}")
        markdown_parts.append(f"**Size:** {file_size_kb:.2f} KB\n")

        # Extract content
        content = extract_file_content(filepath)

        # Determine language for code blocks
        _, ext = os.path.splitext(filepath)
        ext_lower = ext.lower()

        language_map = {
            ".py": "python",
            ".r": "r",
            ".md": "markdown",
            ".json": "json",
            ".yaml": "yaml",
            ".yml": "yaml",
            ".toml": "toml",
            ".ini": "ini",
            ".html": "html",
            ".txt": "text",
            ".rst": "rst",
            ".cfg": "ini",
            ".env": "bash",
        }

        # Special handling for specific filenames
        filename = os.path.basename(filepath)
        if filename in {"Makefile"}:
            language = "makefile"
        elif filename in {"Dockerfile"}:
            language = "dockerfile"
        else:
            language = language_map.get(ext_lower, "text")

        markdown_parts.append(f"```{language}")
        markdown_parts.append(content)
        markdown_parts.append("```\n")

    result = "\n".join(markdown_parts)
    logger.info(
        f"Generated markdown document with {len(file_paths)} files, "
        f"total size: {len(result)} characters",
    )

    return result


async def extract_git_authors(
    temp_dir: str,
    anonymize_email: bool = True,
) -> List[GitAuthor]:
    """
    Extract git authors from the cloned repository using git shortlog.
    Returns a list of GitAuthor objects with commit counts and first/last commit dates.

    Example output from git shortlog -sne:
        120  Alice <alice@example.com>
         95  Bob <bob@example.com>
         10  Carlos <carlos@example.com>

    Args:
        temp_dir: Directory containing the cloned repository.
        anonymize_email: Whether to hash the email local part while keeping the domain.

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

                    # Create GitAuthor (id will be computed automatically by model_validator)
                    git_author = GitAuthor(name=name, email=email, commits=commits)
                    if anonymize_email:
                        git_author.anonymize_email_local_part()
                    logger.debug(
                        "Created GitAuthor: %s (%s) [id: %s]",
                        name,
                        git_author.email if anonymize_email else email,
                        git_author.id,
                    )
                    git_authors.append(git_author)

            logger.info(f"Extracted {len(git_authors)} git authors from repository.")
            return git_authors
        logger.error(f"Failed to extract git authors: {stderr.decode()}")
        return []
    except Exception as e:
        logger.error(f"Failed to extract git authors: {e}")
        return []


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


async def prepare_repository_context(
    repo_url: str,
    max_tokens: int = 400000,
    extraction_mode: str = EXTRACTION_MODE_README_ONLY,
) -> Dict[str, Any]:
    """
    Prepare repository context by cloning, extracting text, and getting git authors.

    Args:
        repo_url: Repository URL to process
        max_tokens: Maximum tokens for text content
        extraction_mode: Extraction mode ("readme_only", "markdown_only", or "all").
            Defaults to "readme_only" to minimize token usage.

    Returns:
        Dictionary containing:
        - input_text: Combined text content in markdown format
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

    # Log extraction mode
    logger.info(
        f"Using extraction mode: {extraction_mode} for repository {repo_url}",
    )

    # Clone the GitHub repository into a temporary folder
    with tempfile.TemporaryDirectory() as temp_dir:
        # Clone repository asynchronously
        clone_result = await clone_repo(repo_url, temp_dir)
        if not clone_result:
            result["error"] = "Failed to clone repository"
            return result

        # Generate comprehensive markdown documentation of repository
        try:
            input_text = generate_repository_markdown(temp_dir, extraction_mode)
            input_text = sanitize_special_tokens(input_text)
        except Exception as e:
            logger.error(f"Failed to generate repository markdown: {e}", exc_info=True)
            result["error"] = f"Failed to extract repository content: {e}"
            return result

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
