"""
API data models
"""
from datetime import datetime
from pydantic import (
    BaseModel,
    HttpUrl,
)
from typing import Union

from .repository import SoftwareSourceCode
from .organization import GitHubOrganization
from .user import GitHubUser
from .models import ResourceType

class APIOutput(BaseModel):
    link: HttpUrl = None
    type: ResourceType = None
    parsdTimestamp: datetime = None
    output: Union[SoftwareSourceCode, GitHubOrganization, GitHubUser] = None


