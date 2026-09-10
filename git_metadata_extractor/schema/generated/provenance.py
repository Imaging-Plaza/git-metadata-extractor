"""Generated from the Open Pulse SHACL shapes. Do not edit by hand.

    layer:  provenance
    source: ontology-shapes-provenance.ttl

Regenerate with:

    just ontology-prepare
    python scripts/v2/generate_from_ontology.py
"""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field


class ExtractionRunModel(BaseModel):
    """pulse:ExtractionRun

    Closed shape: unknown properties are rejected.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    prov_endedAtTime: datetime | None = Field(
        None, alias="prov:endedAtTime", description="Ended At"
    )
    prov_startedAtTime: datetime | None = Field(
        None, alias="prov:startedAtTime", description="Started At"
    )
    pulse_extractedBy: str | None = Field(
        None, alias="pulse:extractedBy", description="Extracted By"
    )
    pulse_extractionSeed: list[str] | None = Field(
        None, alias="pulse:extractionSeed", description="Extraction Seed"
    )


class SoftwareAgentModel(BaseModel):
    """prov:SoftwareAgent

    Closed shape: unknown properties are rejected.
    """

    model_config = ConfigDict(
        populate_by_name=True,
        extra="forbid",
    )

    schema_name: str | None = Field(None, alias="schema:name", description="Name")
    schema_softwareVersion: str | None = Field(
        None, alias="schema:softwareVersion", description="Version"
    )


#: `sh:targetClass` -> the model generated for its shape.
MODELS_BY_TARGET_CLASS: dict[str, type[BaseModel]] = {
    "pulse:ExtractionRun": ExtractionRunModel,
    "prov:SoftwareAgent": SoftwareAgentModel,
}


#: Properties this layer gives `sh:maxCount 1`, per target class. Read off
#: the shapes at generation time so no consumer has to re-derive it.
SINGLE_VALUED_BY_TARGET_CLASS: dict[str, frozenset[str]] = {
    "pulse:ExtractionRun": frozenset(
        {"prov:endedAtTime", "prov:startedAtTime", "pulse:extractedBy"}
    ),
    "prov:SoftwareAgent": frozenset({"schema:name", "schema:softwareVersion"}),
}
