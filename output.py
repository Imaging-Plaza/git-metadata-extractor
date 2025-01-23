from pydantic import BaseModel, Field

class OutputIsImagingSoftware(BaseModel):
    repo: str = Field(description="The URL of the repository being analyzed.")
    is_imaging_software: bool = Field(description="True if the repository counts as imaging software, False otherwise.")

class OutputRepoInfos(BaseModel):
    repo: str = Field(description="The URL of the repository being analyzed.")
    docker: str = Field(description="Docker link.")
    documentation: str = Field(description="Link to an official documentation of the project.")
    image: str = Field(description="Project image link.")
    requirements: str = Field(description="Project requirements.")
    executable: str = Field(description="Link to executable instructions.")

class OutputDescription(BaseModel):
    repo: str = Field(description="The URL of the repository being analyzed.")
    description: str = Field(description="Short description of the project.")