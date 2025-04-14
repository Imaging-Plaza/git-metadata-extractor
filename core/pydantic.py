from pydantic import BaseModel, HttpUrl, EmailStr
from typing import List, Optional, Union
from datetime import date
from typing_extensions import Annotated
from pydantic import StringConstraints, conint

class Person(BaseModel):
    name: str = None
    orcidId: Optional[HttpUrl] = None
    affiliation: Optional[List[str]] = None

class Organization(BaseModel):
    legalName: str = None
    hasRorId: Optional[HttpUrl] = None

class FundingInformation(BaseModel):
    identifier: str = None
    fundingGrant: str = None
    fundingSource: Organization

class FormalParameter(BaseModel):
    name: Annotated[str, StringConstraints(max_length=60)] = None
    description: Optional[Annotated[str, StringConstraints(max_length=2000)]] = None
    encodingFormat: Optional[HttpUrl] = None
    hasDimensionality: Annotated[int, conint(gt=0)] = None
    hasFormat: Optional[str] = None
    defaultValue: Optional[str] = None
    valueRequired: Optional[bool] = None

class ExecutableNotebook(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    url: HttpUrl = None

class SoftwareImage(BaseModel):
    name: str = None
    description: str = None
    softwareVersion: Annotated[str, StringConstraints(pattern=r"[0-9]+\.[0-9]+\.[0-9]+")] = None
    availableInRegistry: HttpUrl = None

# class DataFeed(BaseModel):
#     name: Optional[str] = None
#     description: Optional[str] = None
#     contentURL: Optional[HttpUrl] = None
#     measurementTechnique: Optional[str] = None
#     variableMeasured: Optional[str] = None

class SoftwareSourceCode(BaseModel):
    name: str = None
    applicationCategory: Optional[List[str]] = None
    citation: List[HttpUrl] = None
    codeRepository: List[HttpUrl] = None
    conditionsOfAccess: Optional[str] = None
    dateCreated: date = None
    datePublished: date = None
    description: str = None
    featureList: Optional[List[str]] = None
    image: List[HttpUrl] = None
    isAccessibleForFree: Optional[bool] = None
    isBasedOn: Optional[HttpUrl] = None
    isPluginModuleOf: Optional[List[str]] = None
    license: Annotated[str, StringConstraints(pattern=r"spdx\.org.*")] = None
    author: List[Union[Person, Organization]] = None
    relatedToOrganization: Optional[List[str]] = None
    operatingSystem: Optional[List[str]] = None
    programmingLanguage: Optional[List[str]] = None
    softwareRequirements: Optional[List[str]] = None
    processorRequirements: Optional[List[str]] = None
    memoryRequirements: Optional[int] = None
    requiresGPU: Optional[bool] = None
    # supportingData: Optional[List[DataFeed]] = None
    url: HttpUrl = None
    identifier: str = None
    hasAcknowledgements: Optional[str] = None
    hasDocumentation: Optional[HttpUrl] = None
    hasExecutableInstructions: Optional[str] = None
    hasExecutableNotebook: Optional[List[ExecutableNotebook]] = None
    hasParameter: Optional[List[FormalParameter]] = None
    readme: Optional[HttpUrl] = None
    hasFunding: Optional[List[FundingInformation]] = None
    hasSoftwareImage: Optional[List[SoftwareImage]] = None
    imagingModality: Optional[List[str]] = None
    fairLevel: Optional[str] = None
    graph: Optional[str] = None
