"""/v2 API package — the router plus its route modules.

Split from the former 2,500-line api.py: extract.py (GET/POST extract),
jobs.py (job polling / cancel / crawl), system.py (cache clear, health),
auto_ingest.py (post-extract index write-through), _helpers.py (gates +
app-state resolution). The URL prefix stays /v2 — public contract.
"""
from ._router import v2_router
from . import extract, jobs, system  # noqa: F401 — route registration

__all__ = ["v2_router"]
