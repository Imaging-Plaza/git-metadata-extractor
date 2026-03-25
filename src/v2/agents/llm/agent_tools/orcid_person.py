from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from pydantic_ai import Tool

if TYPE_CHECKING:
    from src.v2.ingest.providers.base import ORCIDProvider

logger = logging.getLogger(__name__)

_DESCRIPTION = (
    "Fetch a person's ORCID profile by their ORCID identifier "
    "(format: XXXX-XXXX-XXXX-XXXX, e.g. 0000-0002-1825-0097). "
    "Returns name, employment history, education history, and affiliations. "
    "Use the returned name for schema:name if it is more complete than the GitHub name. "
    "Use employment/education affiliations to build org:hasMembership entries. "
    "Call this tool when an orcid_hint is present in the context."
)


def make_orcid_person_tool(orcid_provider: ORCIDProvider) -> Tool:
    """Create a pydantic-ai Tool that fetches a single ORCID record by ID.

    Returns a Tool whose closure captures ``orcid_provider`` so it can
    be called by the LLM agent at runtime without any additional context.
    """

    def get_orcid_record(orcid_id: str) -> dict[str, Any]:
        """Fetch a person's ORCID record by their ORCID identifier.

        Accepts ORCID in the format XXXX-XXXX-XXXX-XXXX.
        Returns orcid_id, name, employment, education, and affiliations.
        """
        logger.info("tool call: get_orcid_record — orcid_id=%r", orcid_id)
        record = orcid_provider.get_person_by_orcid(orcid_id)
        return {
            "orcid_id": record.get("orcid_id"),
            "name": record.get("name"),
            "employment": record.get("employment", []),
            "education": record.get("education", []),
            "affiliations": record.get("affiliations", []),
        }

    return Tool(
        get_orcid_record,
        name="get_orcid_record",
        description=_DESCRIPTION,
    )
