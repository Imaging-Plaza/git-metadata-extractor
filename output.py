from pydantic import BaseModel, Field

class OutputIsImagingSoftware(BaseModel):
    repo: str = Field(description="The URL of the repository being analyzed.")
    is_imaging_software: bool = Field(description="True if the repository counts as imaging software, False otherwise.")

class OutputRepoInfos(BaseModel):
    repo: str = Field(description="The URL of the repository being analyzed.")
    doi: str = Field(description="Citation DOI link.")
    documentation: str = Field(description="Link to an official documentation of the project.")
    license: str = Field(description="SPDX link to the project license.")