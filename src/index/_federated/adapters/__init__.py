"""Concrete `IndexAdapter` implementations, one per registered index.

Each module here calls `register()` at import time so simply importing
`src.index._federated.adapters.<name>` is enough to make that index
available via the federated CLI.
"""
