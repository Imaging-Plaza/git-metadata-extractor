"""
API data models
"""
from datetime import datetime
from typing import Any, Union

from pydantic import (
    BaseModel,
    HttpUrl,
    field_validator,
    model_serializer,
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
    
    # GitHub API rate limit information
    github_rate_limit: int = None
    github_rate_remaining: int = None
    github_rate_reset: datetime = None
    
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
    model_config = {"arbitrary_types_allowed": True}
    
    link: HttpUrl = None
    type: ResourceType = None
    parsedTimestamp: datetime = None
    output: Union[dict, list, SoftwareSourceCode, GitHubOrganization, GitHubUser, Any] = None
    stats: APIStats = None
    
    @field_validator("output", mode="before")
    @classmethod
    def preserve_dict_output(cls, v):
        """Preserve dict/list output as-is without converting to Pydantic models."""
        # If it's already a dict or list (e.g., JSON-LD), don't try to convert it
        if isinstance(v, (dict, list)):
            return v
        # Otherwise, let Pydantic handle it normally
        return v
    
    @model_serializer(mode='wrap')
    def serialize_model(self, serializer):
        """Custom serializer to preserve dict/list in output field."""
        # Serialize the model normally
        data = serializer(self)
        
        # If output is a dict or list, keep it as-is (don't convert to model)
        if isinstance(self.output, (dict, list)):
            data['output'] = self.output
        
        return data
