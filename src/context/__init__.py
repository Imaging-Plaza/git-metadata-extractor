"""Context available depending on the item type."""

"""Context available depending on the item type."""

from .repository import (
    # Repository cloning and setup
    clone_repo,
    # Text processing
    combine_text_files,
    # Git operations
    extract_git_authors,
    # Main context preparation
    prepare_repository_context,
    reduce_input_size,
    run_repo_to_text,
    sort_files_by_priority,
    store_combined_text,
)

__all__ = [
    # Repository cloning and setup
    "clone_repo",
    "run_repo_to_text",
    # Text processing
    "combine_text_files",
    "store_combined_text",
    "reduce_input_size",
    "sort_files_by_priority",
    # Git operations
    "extract_git_authors",
    # Main context preparation
    "prepare_repository_context",
]
