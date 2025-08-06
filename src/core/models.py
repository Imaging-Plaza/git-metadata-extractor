from pydantic import BaseModel, HttpUrl, EmailStr
from typing import List, Optional, Union, get_origin, get_args
from datetime import date, datetime
from typing_extensions import Annotated
from pydantic import StringConstraints, conint
from enum import Enum

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

class DataFeed(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    contentUrl: Optional[HttpUrl] = None
    measurementTechnique: Optional[str] = None
    variableMeasured: Optional[str] = None

class ImageKeyword(str, Enum):
    LOGO = "logo"
    ILLUSTRATIVE_IMAGE = "illustrative image"
    BEFORE_IMAGE = "before image"
    AFTER_IMAGE = "after image"
    ANIMATED_IMAGE = "animated image"

class Image(BaseModel):
    contentUrl: HttpUrl = None
    keywords: ImageKeyword = ImageKeyword.ILLUSTRATIVE_IMAGE


class Discipline(str, Enum):
    SOCIAL_SCIENCES = "Social sciences"
    ANTHROPOLOGY = "Anthropology"
    COMMUNICATION_STUDIES = "Communication studies"
    EDUCATION = "Education"
    LINGUISTICS = "Linguistics"
    RESEARCH = "Research"
    SOCIOLOGY = "Sociology"
    GEOGRAPHY = "Geography"
    PSYCHOLOGY = "Psychology"
    POLITICS = "Politics"
    ECONOMICS = "Economics"
    APPLIED_SCIENCES = "Applied sciences"
    HEALTH_SCIENCES = "Health sciences"
    ELECTRICAL_ENGINEERING = "Electrical engineering"
    CHEMICAL_ENGINEERING = "Chemical engineering"
    CIVIL_ENGINEERING = "Civil engineering"
    ARCHITECTURE = "Architecture"
    COMPUTER_ENGINEERING = "Computer engineering"
    ENERGY_ENGINEERING = "Energy engineering"
    MILITARY_SCIENCE = "Military science"
    INDUSTRIAL_PRODUCTION_ENGINEERING = "Industrial and production engineering"
    MECHANICAL_ENGINEERING = "Mechanical engineering"
    BIOLOGICAL_ENGINEERING = "Biological engineering"
    ENVIRONMENTAL_SCIENCE = "Environmental science"
    SYSTEMS_SCIENCE_ENGINEERING = "Systems science and engineering"
    INFORMATION_ENGINEERING = "Information engineering"
    AGRICULTURAL_FOOD_SCIENCES = "Agricultural and food sciences"
    BUSINESS = "Business"
    HUMANITIES = "Humanities"
    HISTORY = "History"
    LITERATURE = "Literature"
    ART = "Art"
    RELIGION = "Religion"
    PHILOSOPHY = "Philosophy"
    LAW = "Law"
    FORMAL_SCIENCES = "Formal sciences"
    MATHEMATICS = "Mathematics"
    LOGIC = "Logic"
    STATISTICS = "Statistics"
    THEORETICAL_COMPUTER_SCIENCE = "Theoretical computer science"
    NATURAL_SCIENCES = "Natural sciences"
    PHYSICS = "Physics"
    ASTRONOMY = "Astronomy"
    BIOLOGY = "Biology"
    CHEMISTRY = "Chemistry"
    EARTH_SCIENCE = "Earth science"

class RepositoryType(str, Enum):
    SOFTWARE = "software"
    EDUCATIONAL_RESOURCE = "educational resource"
    DOCUMENTATION = "documentation"
    DATA = "data"
    OTHER = "other"

class SoftwareSourceCode(BaseModel):
    name: Optional[str] = None
    applicationCategory: Optional[List[str]] = None
    citation: List[HttpUrl] = None
    codeRepository: List[HttpUrl] = None
    conditionsOfAccess: Optional[str] = None
    dateCreated: Optional[date] = None
    datePublished: Optional[date] = None
    description: Optional[str] = None
    featureList: Optional[List[str]] = None
    image: List[Image] = None
    isAccessibleForFree: Optional[bool] = None
    isBasedOn: Optional[HttpUrl] = None
    isPluginModuleOf: Optional[List[str]] = None
    license: Annotated[str, StringConstraints(pattern=r"spdx\.org.*")] = None
    author: List[Union[Person, Organization]] = None
    relatedToOrganization: Optional[List[str]] = None
    relatedToOrganizationJustification: Optional[List[str]] = None
    operatingSystem: Optional[List[str]] = None
    programmingLanguage: Optional[List[str]] = None
    softwareRequirements: Optional[List[str]] = None
    processorRequirements: Optional[List[str]] = None
    memoryRequirements: Optional[int] = None
    requiresGPU: Optional[bool] = None
    supportingData: Optional[List[DataFeed]] = None
    url: Optional[HttpUrl] = None
    identifier: Optional[str] = None
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
    discipline: Optional[List[Discipline]] = None
    disciplineJustification: Optional[List[str]] = None
    repositoryType: Optional[RepositoryType] = None
    repositoryTypeJustification: Optional[List[str]] = None

############################################################
#
# Github Users and Organizations Models
#
############################################################

class GitHubOrganization(BaseModel):
    name: Optional[str] = None
    organizationType: Optional[str] = None
    organizationTypeJustification: Optional[str] = None
    description: Optional[str] = None
    relatedToOrganization: Optional[List[str]] = None
    relatedToOrganizationJustification: Optional[List[str]] = None
    discipline: Optional[List[Discipline]] = None
    disciplineJustification: Optional[List[str]] = None

class GitHubUser(BaseModel):
    name: Optional[str] = None
    relatedToOrganization: Optional[List[str]] = None
    relatedToOrganizationJustification: Optional[List[str]] = None
    discipline: Optional[List[Discipline]] = None
    disciplineJustification: Optional[List[str]] = None
    position: Optional[List[str]] = None
    positionJustification: Optional[List[str]] = None


############################################################
#
# JSON-LD to Pydantic Model Conversion
#
############################################################

from typing import Any, Dict, List as ListType

# A dictionary to map JSON-LD property URIs to functions that can convert them.
# This provides a clean, declarative way to define the conversion process.
JSONLD_TO_PYDANTIC_MAPPING = {
    # Schema.org properties
    "http://schema.org/name": "name",
    "http://schema.org/description": "description",
    "http://schema.org/url": "url",
    "http://schema.org/identifier": "identifier",
    "http://schema.org/dateCreated": "dateCreated",
    "http://schema.org/datePublished": "datePublished",
    "http://schema.org/license": "license",
    "http://schema.org/author": "author",
    "http://schema.org/codeRepository": "codeRepository",
    "http://schema.org/programmingLanguage": "programmingLanguage",
    "http://schema.org/applicationCategory": "applicationCategory",
    "http://schema.org/featureList": "featureList",
    "http://schema.org/image": "image",
    "http://schema.org/isAccessibleForFree": "isAccessibleForFree",
    "http://schema.org/isBasedOn": "isBasedOn",
    "http://schema.org/operatingSystem": "operatingSystem",
    "http://schema.org/softwareRequirements": "softwareRequirements",
    "http://schema.org/processorRequirements": "processorRequirements",
    "http://schema.org/memoryRequirements": "memoryRequirements",
    "http://schema.org/supportingData": "supportingData",
    "http://schema.org/conditionsOfAccess": "conditionsOfAccess",
    "http://schema.org/citation": "citation",
    "http://schema.org/affiliation": "affiliation",
    "http://schema.org/legalName": "legalName",
    "http://schema.org/encodingFormat": "encodingFormat",
    "http://schema.org/defaultValue": "defaultValue",
    "http://schema.org/valueRequired": "valueRequired",
    "http://schema.org/measurementTechnique": "measurementTechnique",
    "http://schema.org/variableMeasured": "variableMeasured",
    "http://schema.org/contentUrl": "contentUrl",
    "http://schema.org/softwareVersion": "softwareVersion",

    # SD ontology properties
    "https://w3id.org/okn/o/sd#hasDocumentation": "hasDocumentation",
    "https://w3id.org/okn/o/sd#hasExecutableInstructions": "hasExecutableInstructions",
    "https://w3id.org/okn/o/sd#hasAcknowledgements": "hasAcknowledgements",
    "https://w3id.org/okn/o/sd#hasParameter": "hasParameter",
    "https://w3id.org/okn/o/sd#readme": "readme",
    "https://w3id.org/okn/o/sd#hasFunding": "hasFunding",
    "https://w3id.org/okn/o/sd#hasSoftwareImage": "hasSoftwareImage",
    "https://w3id.org/okn/o/sd#hasFormat": "hasFormat",
    "https://w3id.org/okn/o/sd#hasDimensionality": "hasDimensionality",
    "https://w3id.org/okn/o/sd#availableInRegistry": "availableInRegistry",
    "https://w3id.org/okn/o/sd#fundingGrant": "fundingGrant",
    "https://w3id.org/okn/o/sd#fundingSource": "fundingSource",

    # Imaging Plaza specific properties
    "https://imaging-plaza.epfl.ch/ontology#imagingModality": "imagingModality",
    "https://imaging-plaza.epfl.ch/ontology#isPluginModuleOf": "isPluginModuleOf",
    "https://imaging-plaza.epfl.ch/ontology#relatedToOrganization": "relatedToOrganization",
    "https://imaging-plaza.epfl.ch/ontology#requiresGPU": "requiresGPU",
    "https://imaging-plaza.epfl.ch/ontology#hasExecutableNotebook": "hasExecutableNotebook",
    "https://imaging-plaza.epfl.ch/ontology#fairLevel": "fairLevel",
    "https://imaging-plaza.epfl.ch/ontology#graph": "graph",

    # MD4I properties
    "http://w3id.org/nfdi4ing/metadata4ing#orcidId": "orcidId",
    "http://w3id.org/nfdi4ing/metadata4ing#hasRorId": "hasRorId",
}

def _get_value(obj: Any) -> Any:
    """Extracts a primitive value from a JSON-LD value object."""
    if isinstance(obj, dict):
        return obj.get("@value", obj.get("@id"))
    if isinstance(obj, list) and obj:
        return _get_value(obj[0])
    return obj

def _get_list(entity: Dict, key: str) -> ListType[Any]:
    """Ensures the value for a key is a list."""
    value = entity.get(key, [])
    return value if isinstance(value, list) else [value]

def _convert_entity(entity: Dict, all_entities: Dict) -> Optional[BaseModel]:
    """Converts a single JSON-LD entity node to its corresponding Pydantic model."""
    entity_types = _get_list(entity, "@type")

    if "http://schema.org/Person" in entity_types:
        return Person(
            name=_get_value(entity.get("http://schema.org/name")),
            orcidId=_get_value(entity.get("http://w3id.org/nfdi4ing/metadata4ing#orcidId")),
            affiliation=[_get_value(v) for v in _get_list(entity, "http://schema.org/affiliation")] or None,
        )
    if "http://schema.org/Organization" in entity_types:
        return Organization(
            legalName=_get_value(entity.get("http://schema.org/legalName")),
            hasRorId=_get_value(entity.get("http://w3id.org/nfdi4ing/metadata4ing#hasRorId")),
        )
    if "https://w3id.org/okn/o/sd#FundingInformation" in entity_types:
        source_ref = _get_value(entity.get("https://w3id.org/okn/o/sd#fundingSource"))
        return FundingInformation(
            identifier=_get_value(entity.get("http://schema.org/identifier")),
            fundingGrant=_get_value(entity.get("https://w3id.org/okn/o/sd#fundingGrant")),
            fundingSource=_convert_entity(all_entities[source_ref], all_entities) if source_ref in all_entities else None,
        )
    if "https://w3id.org/okn/o/sd#FormalParameter" in entity_types:
        return FormalParameter(
            name=_get_value(entity.get("http://schema.org/name")),
            description=_get_value(entity.get("http://schema.org/description")),
            encodingFormat=_get_value(entity.get("http://schema.org/encodingFormat")),
            hasDimensionality=_get_value(entity.get("https://w3id.org/okn/o/sd#hasDimensionality")),
            hasFormat=_get_value(entity.get("https://w3id.org/okn/o/sd#hasFormat")),
            defaultValue=_get_value(entity.get("http://schema.org/defaultValue")),
            valueRequired=_get_value(entity.get("http://schema.org/valueRequired")),
        )
    if "https://imaging-plaza.epfl.ch/ontology#ExecutableNotebook" in entity_types:
        return ExecutableNotebook(
            name=_get_value(entity.get("http://schema.org/name")),
            description=_get_value(entity.get("http://schema.org/description")),
            url=_get_value(entity.get("http://schema.org/url")),
        )
    if "https://w3id.org/okn/o/sd#SoftwareImage" in entity_types:
        return SoftwareImage(
            name=_get_value(entity.get("http://schema.org/name")),
            description=_get_value(entity.get("http://schema.org/description")),
            softwareVersion=_get_value(entity.get("http://schema.org/softwareVersion")),
            availableInRegistry=_get_value(entity.get("https://w3id.org/okn/o/sd#availableInRegistry")),
        )
    if "http://schema.org/DataFeed" in entity_types:
        return DataFeed(
            name=_get_value(entity.get("http://schema.org/name")),
            description=_get_value(entity.get("http://schema.org/description")),
            contentUrl=_get_value(entity.get("http://schema.org/contentUrl")),
            measurementTechnique=_get_value(entity.get("http://schema.org/measurementTechnique")),
            variableMeasured=_get_value(entity.get("http://schema.org/variableMeasured")),
        )
    if "http://schema.org/SoftwareSourceCode" in entity_types:
        data = {}
        for key, value in entity.items():
            if key in JSONLD_TO_PYDANTIC_MAPPING:
                pydantic_key = JSONLD_TO_PYDANTIC_MAPPING[key]
                
                # Handle nested objects and lists of objects by reference
                if pydantic_key in ["author", "supportingData", "hasExecutableNotebook", "hasParameter", "hasFunding", "hasSoftwareImage"]:
                    refs = [_get_value(v) for v in _get_list(entity, key)]
                    data[pydantic_key] = [_convert_entity(all_entities[ref], all_entities) for ref in refs if ref in all_entities]
                elif pydantic_key == "image":
                    urls = [_get_value(v) for v in _get_list(entity, key)]
                    data[pydantic_key] = [Image(contentUrl=url, keywords=ImageKeyword.ILLUSTRATIVE_IMAGE) for url in urls if url]
                else:
                    # Check if the target field is a list type (including Optional[List[...]])
                    field_annotation = SoftwareSourceCode.model_fields[pydantic_key].annotation
                    origin = get_origin(field_annotation)
                    
                    is_list = origin is list or origin is ListType
                    if origin is Union: # Handles Optional[List[...]]
                        is_list = any(get_origin(arg) in (list, ListType) for arg in get_args(field_annotation))

                    if is_list:
                        # Handle lists of strings/URLs
                        data[pydantic_key] = [_get_value(v) for v in _get_list(entity, key)]
                    else:
                        # Handle single values
                        data[pydantic_key] = _get_value(value)
        return SoftwareSourceCode(**data)
    return None

def convert_jsonld_to_pydantic(jsonld_graph: ListType[Dict[str, Any]]) -> Optional[SoftwareSourceCode]:
    """
    Converts a JSON-LD graph into a Pydantic SoftwareSourceCode object.

    Args:
        jsonld_graph: A list of dictionaries representing the JSON-LD graph.

    Returns:
        An instance of the SoftwareSourceCode Pydantic model, or None if no
        SoftwareSourceCode entity is found in the graph.
    """
    if not jsonld_graph:
        return None

    all_entities = {item["@id"]: item for item in jsonld_graph if "@id" in item}
    
    for entity in jsonld_graph:
        entity_types = _get_list(entity, "@type")
        if "http://schema.org/SoftwareSourceCode" in entity_types:
            # Found the main entity, convert it and return
            converted = _convert_entity(entity, all_entities)
            if isinstance(converted, SoftwareSourceCode):
                return converted
    
    return None


############################################################
#
# Pydantic to Zod-compatible Dictionary Conversion
#
############################################################

PYDANTIC_TO_ZOD_MAPPING = {
    "Person": {
        "name": "schema:name",
        "orcidId": "md4i:orcidId",
        "affiliation": "schema:affiliation",
    },
    "Organization": {
        "legalName": "schema:legalName",
        "hasRorId": "md4i:hasRorId",
    },
    "FundingInformation": {
        "identifier": "schema:identifier",
        "fundingGrant": "sd:fundingGrant",
        "fundingSource": "sd:fundingSource",
    },
    "FormalParameter": {
        "name": "schema:name",
        "description": "schema:description",
        "encodingFormat": "schema:encodingFormat",
        "hasDimensionality": "sd:hasDimensionality",
        "hasFormat": "sd:hasFormat",
        "defaultValue": "schema:defaultValue",
        "valueRequired": "schema:valueRequired",
    },
    "ExecutableNotebook": {
        "name": "schema:name",
        "description": "schema:description",
        "url": "schema:url",
    },
    "SoftwareImage": {
        "name": "schema:name",
        "description": "schema:description",
        "softwareVersion": "schema:softwareVersion",
        "availableInRegistry": "sd:availableInRegistry",
    },
    "DataFeed": {
        "name": "schema:name",
        "description": "schema:description",
        "contentUrl": "schema:contentUrl",
        "measurementTechnique": "schema:measurementTechnique",
        "variableMeasured": "schema:variableMeasured",
    },
    "Image": {
        "contentUrl": "schema:contentUrl",
        "keywords": "schema:keywords",
    },
    "SoftwareSourceCode": {
        "name": "schema:name",
        "applicationCategory": "schema:applicationCategory",
        "citation": "schema:citation",
        "codeRepository": "schema:codeRepository",
        "conditionsOfAccess": "schema:conditionsOfAccess",
        "dateCreated": "schema:dateCreated",
        "datePublished": "schema:datePublished",
        "description": "schema:description",
        "featureList": "schema:featureList",
        "image": "schema:image",
        "isAccessibleForFree": "schema:isAccessibleForFree",
        "isBasedOn": "schema:isBasedOn",
        "isPluginModuleOf": "imag:isPluginModuleOf",
        "license": "schema:license",
        "author": "schema:author",
        "relatedToOrganization": "imag:relatedToOrganization",
        "operatingSystem": "schema:operatingSystem",
        "programmingLanguage": "schema:programmingLanguage",
        "softwareRequirements": "schema:softwareRequirements",
        "processorRequirements": "schema:processorRequirements",
        "memoryRequirements": "schema:memoryRequirements",
        "requiresGPU": "imag:requiresGPU",
        "supportingData": "schema:supportingData",
        "url": "schema:url",
        "identifier": "schema:identifier",
        "hasAcknowledgements": "sd:hasAcknowledgements",
        "hasDocumentation": "sd:hasDocumentation",
        "hasExecutableInstructions": "sd:hasExecutableInstructions",
        "hasExecutableNotebook": "imag:hasExecutableNotebook",
        "hasParameter": "sd:hasParameter",
        "readme": "sd:readme",
        "hasFunding": "sd:hasFunding",
        "hasSoftwareImage": "sd:hasSoftwareImage",
        "imagingModality": "imag:imagingModality",
        "fairLevel": "imag:fairLevel",
        "graph": "imag:graph",
    },
}

def convert_pydantic_to_zod_form_dict(pydantic_obj: Any) -> Any:
    """
    Recursively converts a Pydantic model instance into a dictionary
    with keys compatible with the frontend Zod schema.
    """
    if isinstance(pydantic_obj, list):
        return [convert_pydantic_to_zod_form_dict(item) for item in pydantic_obj]

    if not isinstance(pydantic_obj, BaseModel):
        if isinstance(pydantic_obj, HttpUrl):
            return str(pydantic_obj)
        if isinstance(pydantic_obj, date):
            # Convert date to a full ISO 8601 datetime string at midnight UTC.
            # This is more robust for JavaScript's `new Date()`.
            return datetime.combine(pydantic_obj, datetime.min.time()).isoformat() + "Z"
        if isinstance(pydantic_obj, Enum):
            return pydantic_obj.value
        return pydantic_obj

    model_name = pydantic_obj.__class__.__name__
    if model_name not in PYDANTIC_TO_ZOD_MAPPING:
        # Fallback for any unmapped models
        return pydantic_obj.model_dump(exclude_unset=True)

    key_map = PYDANTIC_TO_ZOD_MAPPING[model_name]
    zod_dict = {}
    
    # By iterating over the model directly (`for key, value in pydantic_obj`),
    # we process its fields. This ensures that nested Pydantic models are passed
    # to the recursive call as model instances, not as pre-converted dictionaries.
    # This was the source of the bug where nested object keys were not being converted.
    for pydantic_key, value in pydantic_obj:
        if value is not None and pydantic_key in key_map:
            zod_key = key_map[pydantic_key]
            
            # Recursively convert nested models or lists
            zod_dict[zod_key] = convert_pydantic_to_zod_form_dict(value)

    return zod_dict


