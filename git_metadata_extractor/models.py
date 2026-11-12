"""Common policy for hand-written internal data models."""

from pydantic import BaseModel, ConfigDict


class DataModel(BaseModel):
    """Declared data fields only; no opaque runtime objects."""

    model_config = ConfigDict(extra="forbid")


class InternalModel(DataModel):
    """Validate declared fields while retaining live runtime objects by reference.

    This is not an API response model. Public payloads retain their own schemas
    and explicit serialization boundaries.
    """

    model_config = ConfigDict(extra="forbid", arbitrary_types_allowed=True)
