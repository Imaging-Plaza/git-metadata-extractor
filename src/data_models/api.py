"""
API data models
"""
from datetime import datetime
from typing import Union

from pydantic import (
    BaseModel,
    HttpUrl,
)

from .models import ResourceType
from .organization import GitHubOrganization
from .repository import SoftwareSourceCode
from .user import GitHubUser


class APIOutput(BaseModel):
    link: HttpUrl = None
    type: ResourceType = None
    parsedTimestamp: datetime = None
    output: Union[SoftwareSourceCode, GitHubOrganization, GitHubUser] = None
