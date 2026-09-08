"""Describe one `/v2/extract` invocation as a `pulse:ExtractionRun`.

First step of the provenance architecture's phase 2, and the first production
use of the models generated from the v3 SHACL shapes — everything under
`schema/generated/` had been generated and tested but unwired until now.

**Why this is not part of `@graph`.** The four-layer model puts extraction runs
in the substrate/provenance layer, not the canonical graph: a run describes
*how* the graph was produced, and `graph:canonical` is meant to hold only what
was produced. So the descriptor is returned beside `output`, and the corpus
signature — which reads `output["@graph"]` — is untouched by it.

**Why the IRI looks provisional.** `PROVENANCE_ARCHITECTURE.md` gap 3 wants a
substrate graph IRI to *be* an `ExtractionRun`, so that `observedFrom → graph`
resolves. That graph does not exist until the substrate writer (phase 3), so the
run gets a `urn:pulse:run:{id}` IRI now and phase 3 is expected to replace it
with the named-graph IRI. `run_iri()` exists so that swap is one function.

Validated against `ExtractionRunModel` on the way out. Both shapes are
`sh:closed`, so a typo in a property name fails here rather than surfacing as a
SHACL violation three stages later.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from git_metadata_extractor.schema.generated.provenance import (
    ExtractionRunModel,
    SoftwareAgentModel,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

#: The tool itself, as a `prov:SoftwareAgent`. Stable across runs, so it is an
#: identity rather than a per-run node.
SOFTWARE_AGENT_IRI = "urn:pulse:agent:git-metadata-extractor"


def run_iri(run_id: str) -> str:
    """The run's IRI. Phase 3 replaces this with the substrate graph IRI."""
    return f"urn:pulse:run:{run_id}"


def software_agent(*, name: str, version: str) -> dict[str, Any]:
    """The `prov:SoftwareAgent` node for this build of the extractor."""
    model = SoftwareAgentModel(
        **{"schema:name": name, "schema:softwareVersion": version},
    )
    return {
        "@id": SOFTWARE_AGENT_IRI,
        "@type": "prov:SoftwareAgent",
        **model.model_dump(by_alias=True, exclude_none=True),
    }


def build_extraction_run(  # noqa: PLR0913 — one keyword per recorded fact
    *,
    run_id: str,
    seeds: Sequence[str],
    started_at: datetime,
    ended_at: datetime | None = None,
    package_name: str,
    package_version: str,
) -> dict[str, Any]:
    """Assemble and validate the run descriptor as a two-node JSON-LD graph.

    `ended_at` defaults to now: the run is described at the point the graph is
    finished, which is the last thing that happens to it.
    """
    finished = ended_at or datetime.now(timezone.utc)
    model = ExtractionRunModel(
        **{
            "prov:startedAtTime": started_at,
            "prov:endedAtTime": finished,
            "pulse:extractedBy": SOFTWARE_AGENT_IRI,
            "pulse:extractionSeed": [seed for seed in seeds if seed],
        },
    )
    payload = model.model_dump(by_alias=True, exclude_none=True, mode="json")
    run = {
        "@id": run_iri(run_id),
        "@type": "pulse:ExtractionRun",
        **payload,
    }
    # Two nodes, not one nested inside the other. `pulse:extractedBy` is
    # declared as a link, so the run carries the agent's IRI and the agent is a
    # sibling node — the agent is shared across every run, and nesting it would
    # both duplicate it and require a class IRI (`prov:SoftwareAgent`) to be
    # used as a property name, which means nothing in RDF.
    return {
        "@graph": [
            run,
            software_agent(name=package_name, version=package_version),
        ],
    }


__all__ = [
    "SOFTWARE_AGENT_IRI",
    "build_extraction_run",
    "run_iri",
    "software_agent",
]
