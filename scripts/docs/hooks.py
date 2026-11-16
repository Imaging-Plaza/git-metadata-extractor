"""Only show Material's version selector for Mike's versioned builds."""

# ruff: noqa: INP001 -- MkDocs loads this hook by file path, not as a package.

import os


def on_config(config):
    if not os.environ.get("MIKE_DOCS_VERSION"):
        config["extra"].pop("version", None)
    return config
