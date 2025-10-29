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

class APIStats(BaseModel):
    # Official API-reported token counts
    agent_input_tokens: int = None
    agent_output_tokens: int = None
    total_tokens: int = None
    
    # Tokenizer-based estimates (complementary/fallback)
    estimated_input_tokens: int = None
    estimated_output_tokens: int = None
    estimated_total_tokens: int = None
    
    duration: float = None
    start_time: datetime = None
    end_time: datetime = None
    status_code: int = None
    
    def calculate_total_tokens(self):
        """Calculate total tokens from input and output tokens."""
        if self.agent_input_tokens is not None and self.agent_output_tokens is not None:
            self.total_tokens = self.agent_input_tokens + self.agent_output_tokens
        elif self.agent_input_tokens is not None:
            self.total_tokens = self.agent_input_tokens
        elif self.agent_output_tokens is not None:
            self.total_tokens = self.agent_output_tokens
        
        # Calculate estimated totals
        if self.estimated_input_tokens is not None and self.estimated_output_tokens is not None:
            self.estimated_total_tokens = self.estimated_input_tokens + self.estimated_output_tokens
        elif self.estimated_input_tokens is not None:
            self.estimated_total_tokens = self.estimated_input_tokens
        elif self.estimated_output_tokens is not None:
            self.estimated_total_tokens = self.estimated_output_tokens
        
        return self


class APIOutput(BaseModel):
    link: HttpUrl = None
    type: ResourceType = None
    parsedTimestamp: datetime = None
    output: Union[SoftwareSourceCode, GitHubOrganization, GitHubUser] = None
    stats: APIStats = None
