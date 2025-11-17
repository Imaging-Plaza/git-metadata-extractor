"""
Conversion functions for the data models
"""

from datetime import date, datetime
from enum import Enum
from typing import (
    Any,
    Dict,
    Literal,
    Optional,
    Tuple,
    Type,
    Union,
    get_args,
    get_origin,
)
from typing import Dict as DictType
from typing import List as ListType

from pydantic import BaseModel, Field, HttpUrl, create_model

from .models import (
    Organization,
    Person,
)
from .repository import (
    DataFeed,
    ExecutableNotebook,
    FormalParameter,
    FundingInformation,
    GitAuthor,
    Image,
    ImageKeyword,
    SoftwareImage,
    SoftwareSourceCode,
)

############################################################
#
# JSON-LD to Pydantic Model Conversion
#
############################################################

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
    # MD4I properties
    "http://w3id.org/nfdi4ing/metadata4ing#orcid": "orcid",
    "http://w3id.org/nfdi4ing/metadata4ing#hasRorId": "hasRorId",
    # Git metadata
    "https://imaging-plaza.epfl.ch/ontology#gitAuthors": "gitAuthors",
    "https://imaging-plaza.epfl.ch/ontology#commits": "commits",
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
        # Extract core fields that are commonly in JSON-LD
        person_data = {
            "type": "Person",  # Explicit type discriminator
            "name": _get_value(entity.get("http://schema.org/name")),
            "orcid": _get_value(
                entity.get("http://w3id.org/nfdi4ing/metadata4ing#orcid"),
            ),
            "affiliation": [
                _get_value(v)
                for v in _get_list(entity, "http://schema.org/affiliation")
            ]
            or None,
        }

        # Extract email if present (support both single and list)
        email_value = entity.get("http://schema.org/email")
        if email_value:
            email_extracted = _get_value(email_value)
            if email_extracted:
                person_data["email"] = email_extracted

        # All other fields (gitAuthorIds, affiliations, currentAffiliation,
        # affiliationHistory, contributionSummary, biography, infoscienceEntity)
        # will use their default values as defined in the Person model

        return Person(**person_data)
    if "http://schema.org/Organization" in entity_types:
        return Organization(
            type="Organization",  # Explicit type discriminator
            legalName=_get_value(entity.get("http://schema.org/legalName")),
            hasRorId=_get_value(
                entity.get("http://w3id.org/nfdi4ing/metadata4ing#hasRorId"),
            ),
        )
    if "https://imaging-plaza.epfl.ch/ontology#GitAuthor" in entity_types:
        return GitAuthor(
            name=_get_value(entity.get("http://schema.org/name")),
            email=_get_value(entity.get("http://schema.org/email")),
            commits=_get_value(
                entity.get("https://imaging-plaza.epfl.ch/ontology#commits"),
            ),
        )
    if "https://w3id.org/okn/o/sd#FundingInformation" in entity_types:
        source_ref = _get_value(entity.get("https://w3id.org/okn/o/sd#fundingSource"))
        return FundingInformation(
            identifier=_get_value(entity.get("http://schema.org/identifier")),
            fundingGrant=_get_value(
                entity.get("https://w3id.org/okn/o/sd#fundingGrant"),
            ),
            fundingSource=_convert_entity(all_entities[source_ref], all_entities)
            if source_ref in all_entities
            else None,
        )
    if "https://w3id.org/okn/o/sd#FormalParameter" in entity_types:
        return FormalParameter(
            name=_get_value(entity.get("http://schema.org/name")),
            description=_get_value(entity.get("http://schema.org/description")),
            encodingFormat=_get_value(entity.get("http://schema.org/encodingFormat")),
            hasDimensionality=_get_value(
                entity.get("https://w3id.org/okn/o/sd#hasDimensionality"),
            ),
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
            availableInRegistry=_get_value(
                entity.get("https://w3id.org/okn/o/sd#availableInRegistry"),
            ),
        )
    if "http://schema.org/DataFeed" in entity_types:
        return DataFeed(
            name=_get_value(entity.get("http://schema.org/name")),
            description=_get_value(entity.get("http://schema.org/description")),
            contentUrl=_get_value(entity.get("http://schema.org/contentUrl")),
            measurementTechnique=_get_value(
                entity.get("http://schema.org/measurementTechnique"),
            ),
            variableMeasured=_get_value(
                entity.get("http://schema.org/variableMeasured"),
            ),
        )
    if "http://schema.org/SoftwareSourceCode" in entity_types:
        data = {}
        for key, value in entity.items():
            if key in JSONLD_TO_PYDANTIC_MAPPING:
                pydantic_key = JSONLD_TO_PYDANTIC_MAPPING[key]

                # Handle nested objects and lists of objects by reference
                if pydantic_key in [
                    "author",
                    "supportingData",
                    "hasExecutableNotebook",
                    "hasFunding",
                    "hasSoftwareImage",
                    "gitAuthors",
                ]:
                    refs = [_get_value(v) for v in _get_list(entity, key)]
                    data[pydantic_key] = [
                        _convert_entity(all_entities[ref], all_entities)
                        for ref in refs
                        if ref in all_entities
                    ]
                elif pydantic_key == "image":
                    urls = [_get_value(v) for v in _get_list(entity, key)]
                    data[pydantic_key] = [
                        Image(contentUrl=url, keywords=ImageKeyword.ILLUSTRATIVE_IMAGE)
                        for url in urls
                        if url
                    ]
                else:
                    # Check if the target field is a list type (including Optional[List[...]])
                    field_annotation = SoftwareSourceCode.model_fields[
                        pydantic_key
                    ].annotation
                    origin = get_origin(field_annotation)

                    is_list = origin is list or origin is ListType
                    if origin is Union:  # Handles Optional[List[...]]
                        is_list = any(
                            get_origin(arg) in (list, ListType)
                            for arg in get_args(field_annotation)
                        )

                    if is_list:
                        # Handle lists of strings/URLs
                        data[pydantic_key] = [
                            _get_value(v) for v in _get_list(entity, key)
                        ]
                    else:
                        # Handle single values
                        data[pydantic_key] = _get_value(value)
        return SoftwareSourceCode(**data)
    return None


def convert_jsonld_to_pydantic(
    jsonld_graph: ListType[Dict[str, Any]],
) -> Optional[SoftwareSourceCode]:
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
        "orcid": "md4i:orcid",
        "affiliation": "schema:affiliation",
        "source": "imag:source",
    },
    "Affiliation": {
        "name": "schema:name",
        "organizationId": "schema:identifier",
        "source": "imag:source",
    },
    "Organization": {
        "legalName": "schema:legalName",
        "hasRorId": "md4i:hasRorId",
        "organizationType": "schema:additionalType",
        "attributionConfidence": "imag:attributionConfidence",
        "source": "imag:source",
    },
    "Commits": {
        "total": "imag:totalCommits",
        "firstCommitDate": "imag:firstCommitDate",
        "lastCommitDate": "imag:lastCommitDate",
    },
    "GitAuthor": {
        "name": "schema:name",
        "email": "schema:email",
        "commits": "imag:commits",
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
    "InfoscienceEntity": {
        "name": "schema:name",
        "url": "schema:url",
        "confidence": "imag:confidence",
        "justification": "imag:justification",
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
        "readme": "sd:readme",
        "hasFunding": "sd:hasFunding",
        "hasSoftwareImage": "sd:hasSoftwareImage",
        "imagingModality": "imag:imagingModality",
        "gitAuthors": "imag:gitAuthors",
        "relatedToOrganizations": "imag:relatedToOrganizations",
        "relatedToOrganizationJustification": "imag:relatedToOrganizationJustification",
        "repositoryType": "imag:repositoryType",
        "repositoryTypeJustification": "imag:repositoryTypeJustification",
        "relatedToEPFL": "imag:relatedToEPFL",
        "relatedToEPFLConfidence": "imag:relatedToEPFLConfidence",
        "relatedToEPFLJustification": "imag:relatedToEPFLJustification",
        "infoscienceEntities": "imag:infoscienceEntities",
        "relatedDatasets": "imag:relatedDatasets",
        "relatedPublications": "imag:relatedPublications",
        "relatedModels": "imag:relatedModels",
        "relatedAPIs": "imag:relatedAPIs",
        "discipline": "imag:discipline",
        "disciplineJustification": "imag:disciplineJustification",
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


############################################################
#
# Pydantic to JSON-LD Conversion
#
############################################################


def convert_pydantic_to_jsonld(
    pydantic_obj: Any,
    base_url: Optional[str] = None,
) -> Union[Dict, ListType]:
    """
    Converts a Pydantic model instance into JSON-LD format.

    This function creates a JSON-LD graph structure with proper @context, @type,
    and semantic URIs. It's extensible and works with SoftwareSourceCode,
    GitHubUser, GitHubOrganization, and nested models.

    Args:
        pydantic_obj: A Pydantic model instance to convert
        base_url: Optional base URL for generating @id values

    Returns:
        A dictionary or list representing the JSON-LD graph
    """

    # Define namespace prefixes for the @context
    context = {
        "schema": "http://schema.org/",
        "sd": "https://w3id.org/okn/o/sd#",
        "imag": "https://imaging-plaza.epfl.ch/ontology/",
        "md4i": "https://w3id.org/md4i/",
    }

    # Helper function to convert a single entity
    def _convert_entity_to_jsonld(
        obj: Any,
        entity_id: Optional[str] = None,
    ) -> Optional[Dict]:
        if obj is None:
            return None

        # Handle primitive types
        if not isinstance(obj, BaseModel):
            if isinstance(obj, HttpUrl):
                return {"@id": str(obj)}
            if isinstance(obj, date):
                return {"@value": obj.isoformat()}
            if isinstance(obj, Enum):
                return {"@value": obj.value}
            # Return primitives as-is (strings, numbers, booleans)
            return obj

        # Get the model name and mapping
        model_name = obj.__class__.__name__
        if model_name not in PYDANTIC_TO_ZOD_MAPPING:
            # Fallback: return simple dump for unmapped models
            return obj.model_dump(exclude_unset=True, exclude_none=True)

        key_map = PYDANTIC_TO_ZOD_MAPPING[model_name]

        # Build the JSON-LD entity
        jsonld_entity = {}

        # Add @id if provided or generate one
        if entity_id:
            jsonld_entity["@id"] = entity_id
        elif base_url and model_name == "SoftwareSourceCode":
            jsonld_entity["@id"] = base_url

        # Add @type based on model name
        type_mapping = {
            "SoftwareSourceCode": "http://schema.org/SoftwareSourceCode",
            "Person": "http://schema.org/Person",
            "Organization": "http://schema.org/Organization",
            "Affiliation": "http://schema.org/Organization",
            "GitHubUser": "http://schema.org/Person",
            "GitHubOrganization": "http://schema.org/Organization",
            "DataFeed": "http://schema.org/DataFeed",
            "FormalParameter": "http://schema.org/PropertyValue",
            "ExecutableNotebook": "http://schema.org/SoftwareApplication",
            "SoftwareImage": "http://schema.org/SoftwareApplication",
            "Image": "http://schema.org/ImageObject",
            "FundingInformation": "http://schema.org/Grant",
            "GitAuthor": "http://schema.org/Person",
            "InfoscienceEntity": "http://schema.org/Thing",
        }

        if model_name in type_mapping:
            jsonld_entity["@type"] = type_mapping[model_name]

        # Convert each field
        for pydantic_key, value in obj:
            if value is None:
                continue

            if pydantic_key not in key_map:
                continue

            jsonld_key = key_map[pydantic_key]

            # Handle lists
            if isinstance(value, list):
                jsonld_values = []
                for item in value:
                    if isinstance(item, BaseModel):
                        # Nested model - convert recursively
                        converted = _convert_entity_to_jsonld(item)
                        if converted:
                            jsonld_values.append(converted)
                    else:
                        # Primitive or HttpUrl
                        converted = _convert_entity_to_jsonld(item)
                        if converted is not None:
                            jsonld_values.append(converted)

                if jsonld_values:
                    jsonld_entity[jsonld_key] = jsonld_values

            # Handle nested models
            elif isinstance(value, BaseModel):
                converted = _convert_entity_to_jsonld(value)
                if converted:
                    jsonld_entity[jsonld_key] = converted

            # Handle other types
            else:
                # Special handling for ORCID field - always output as @id format
                if pydantic_key == "orcid" and value:
                    # Convert ORCID to URL format if it's just an ID
                    orcid_value = str(value)
                    if not orcid_value.startswith("http"):
                        orcid_value = f"https://orcid.org/{orcid_value}"
                    jsonld_entity[jsonld_key] = {"@id": orcid_value}
                else:
                    converted = _convert_entity_to_jsonld(value)
                    if converted is not None:
                        jsonld_entity[jsonld_key] = converted

        return jsonld_entity

    # Convert the main object
    main_entity = _convert_entity_to_jsonld(pydantic_obj, base_url)

    if not main_entity:
        return {}

    # Return as JSON-LD graph structure
    result = {
        "@context": context,
        "@graph": [main_entity],
    }

    return result


############################################################
#
# Simplified Model Generation for vLLM Compatibility
#
############################################################

# Module-level cache for generated simplified models
_SIMPLIFIED_MODEL_CACHE: Dict[
    Type[BaseModel],
    Tuple[Type[BaseModel], Dict[str, Any]],
] = {}


def _is_pydantic_model(annotation: Any) -> bool:
    """Check if an annotation is a Pydantic BaseModel class."""
    return (
        isinstance(annotation, type)
        and issubclass(annotation, BaseModel)
        and annotation is not BaseModel
    )


def _get_type_name(type_obj: Any) -> str:
    """Get a clean type name for field naming."""
    if isinstance(type_obj, type):
        # Capitalize primitive types for better field names
        if type_obj is str:
            return "String"
        elif type_obj is int:
            return "Int"
        elif type_obj is float:
            return "Float"
        elif type_obj is bool:
            return "Bool"
        return type_obj.__name__
    type_str = str(type_obj).replace("typing.", "").replace("'", "")
    # Capitalize common types
    if type_str == "str":
        return "String"
    elif type_str == "int":
        return "Int"
    elif type_str == "float":
        return "Float"
    elif type_str == "bool":
        return "Bool"
    return type_str


def _simplify_type(
    annotation: Any,
    memo: Dict[Type[BaseModel], Tuple[Type[BaseModel], Dict[str, Any]]],
    union_metadata: Dict[str, Any],
    field_name: str,
) -> Tuple[Any, Optional[str]]:
    """
    Convert a type annotation to a simplified type.

    Returns:
        Tuple of (simplified_type, description_addition)
    """
    origin = get_origin(annotation)

    # Handle Optional (Union with None)
    if origin is Union:
        args = get_args(annotation)
        # Filter out NoneType
        non_none_args = [arg for arg in args if arg is not type(None)]

        if len(non_none_args) == 0:
            return (Optional[str], " (Original type: None)")

        # If only one non-None type, simplify it
        if len(non_none_args) == 1:
            simplified, desc = _simplify_type(
                non_none_args[0],
                memo,
                union_metadata,
                field_name,
            )
            return (Optional[simplified], desc)

        # Multiple types in Union - need to split into separate fields
        # Store metadata for reconciliation
        union_info = {
            "original_field": field_name,
            "types": non_none_args,
            "fields": {},
        }

        simplified_types = []
        for union_type in non_none_args:
            simplified, desc = _simplify_type(
                union_type,
                memo,
                union_metadata,
                field_name,
            )
            type_name = _get_type_name(union_type)
            field_suffix = type_name.replace("typing.", "").replace("'", "")
            new_field_name = f"{field_name}{field_suffix}"
            union_info["fields"][new_field_name] = {
                "type": union_type,
                "simplified_type": simplified,
                "description": desc,
            }
            simplified_types.append((new_field_name, simplified, desc))

        # Store in union_metadata
        if field_name not in union_metadata:
            union_metadata[field_name] = []
        union_metadata[field_name].append(union_info)

        # Return None to indicate this field should be split
        return (None, None)

    # Handle Dict types
    if origin is dict or origin is DictType:
        args = get_args(annotation)
        if len(args) >= 2:
            key_type = args[0]
            value_type = args[1]

            # Simplify both key and value types
            simplified_key, key_desc = _simplify_type(
                key_type,
                memo,
                union_metadata,
                field_name,
            )
            simplified_value, value_desc = _simplify_type(
                value_type,
                memo,
                union_metadata,
                field_name,
            )

            # Return Dict with simplified types
            return (
                DictType[simplified_key, simplified_value],
                f" (Original type: Dict[{key_type}, {value_type}])",
            )
        return (DictType[str, Any], " (Original type: Dict)")

    # Handle List types
    if origin is list or origin is ListType:
        args = get_args(annotation)
        if args:
            inner_type = args[0]
            inner_origin = get_origin(inner_type)

            # Check if inner type is a Union that needs splitting
            if inner_origin is Union:
                inner_args = get_args(inner_type)
                non_none_inner_args = [
                    arg for arg in inner_args if arg is not type(None)
                ]

                if len(non_none_inner_args) > 1:
                    # List[Union[A, B]] - split into separate List fields
                    union_info = {
                        "original_field": field_name,
                        "types": non_none_inner_args,
                        "fields": {},
                        "is_list": True,
                    }

                    for union_type in non_none_inner_args:
                        simplified, desc = _simplify_type(
                            union_type,
                            memo,
                            union_metadata,
                            field_name,
                        )
                        type_name = _get_type_name(union_type)
                        field_suffix = type_name.replace("typing.", "").replace("'", "")
                        new_field_name = f"{field_name}{field_suffix}"
                        union_info["fields"][new_field_name] = {
                            "type": union_type,
                            "simplified_type": ListType[simplified],
                            "description": desc,
                        }

                    # Store in union_metadata
                    if field_name not in union_metadata:
                        union_metadata[field_name] = []
                    union_metadata[field_name].append(union_info)

                    # Return None to indicate this field should be split
                    return (None, None)

            # Normal List handling
            simplified_inner, desc = _simplify_type(
                inner_type,
                memo,
                union_metadata,
                field_name,
            )
            if simplified_inner is None:
                # This shouldn't happen after the Union check above, but handle it
                return (None, None)
            return (ListType[simplified_inner], desc)
        return (ListType[str], " (Original type: List)")

    # Handle HttpUrl -> str
    if annotation is HttpUrl or (
        isinstance(annotation, type) and issubclass(annotation, HttpUrl)
    ):
        return (str, " (Original type: HttpUrl)")

    # Handle date -> str
    if annotation is date:
        return (str, " (Original type: date, ISO format: YYYY-MM-DD)")

    # Handle datetime -> str
    if annotation is datetime:
        return (str, " (Original type: datetime, ISO format)")

    # Handle Enum -> Literal with enum values
    if isinstance(annotation, type) and issubclass(annotation, Enum):
        enum_values = [e.value for e in annotation]
        # Convert to Literal type with the specific enum values
        # This ensures the LLM must use one of these exact strings
        # Create Literal dynamically with unpacked values
        if enum_values:
            # Use eval to create Literal with unpacked values
            # This is safe since enum_values come from the Enum class
            # Pass Literal in the namespace so eval can access it
            literal_type = eval(
                f"Literal[{', '.join(repr(v) for v in enum_values)}]",
                {"Literal": Literal},
            )
        else:
            literal_type = str
        return (
            literal_type,
            f" (Original type: {annotation.__name__} enum, values: {enum_values})",
        )

    # Handle Pydantic models - recursively simplify
    if _is_pydantic_model(annotation):
        simplified_model, _ = create_simplified_model(annotation, memo)
        return (simplified_model, f" (Original type: {annotation.__name__})")

    # Primitive types - keep as-is
    if annotation in (str, int, float, bool):
        return (annotation, None)

    # Default: convert to str
    return (str, f" (Original type: {annotation})")


def create_simplified_model(
    source_model: Type[BaseModel],
    memo: Optional[
        Dict[Type[BaseModel], Tuple[Type[BaseModel], Dict[str, Any]]]
    ] = None,
    field_filter: Optional[list[str]] = None,
) -> Tuple[Type[BaseModel], Dict[str, Any]]:
    """
    Dynamically create a simplified Pydantic model from a source model.

    Converts complex Pydantic types (HttpUrl, date, datetime, Enum) to primitives (str)
    and splits Union types into separate fields for vLLM compatibility.

    Args:
        source_model: The source Pydantic model class to simplify (e.g., SoftwareSourceCode)
        memo: Optional memoization cache (uses module-level cache if None)
        field_filter: Optional list of field names to include. If None, includes all fields.

    Returns:
        Tuple of (simplified_model_class, union_metadata)
        - simplified_model_class: The dynamically created simplified model
        - union_metadata: Dict mapping original field names to Union field info for reconciliation

    Example:
        SimplifiedSoftwareSourceCode, union_meta = create_simplified_model(SoftwareSourceCode)
        # Use SimplifiedSoftwareSourceCode as output_type in PydanticAI agent

        # With field filtering:
        fields_to_extract = ["name", "description", "discipline", "repositoryType"]
        SimplifiedModel, union_meta = create_simplified_model(SoftwareSourceCode, field_filter=fields_to_extract)
    """
    # Use module-level cache if memo not provided
    # Cache key includes field_filter to avoid collisions
    use_module_cache = memo is None
    if use_module_cache:
        cache_key = (source_model, tuple(field_filter) if field_filter else None)
        if cache_key in _SIMPLIFIED_MODEL_CACHE:
            return _SIMPLIFIED_MODEL_CACHE[cache_key]
        memo = {}

    # Check memoization cache
    if source_model in memo:
        return memo[source_model]

    # Track union metadata for this model
    union_metadata: Dict[str, Any] = {}

    # Get all fields from source model
    new_fields: Dict[str, Any] = {}

    for field_name, field_info in source_model.model_fields.items():
        # Filter fields if field_filter is provided
        if field_filter is not None and field_name not in field_filter:
            continue
        annotation = field_info.annotation
        default = field_info.default if field_info.default is not ... else None
        default_factory = (
            field_info.default_factory
            if field_info.default_factory is not ...
            else None
        )

        # Simplify the type (this may populate union_metadata)
        simplified_type, desc_addition = _simplify_type(
            annotation,
            memo,
            union_metadata,
            field_name,
        )

        # If simplified_type is None, it's a Union that needs splitting
        # Skip this field - it will be split into separate fields later
        if simplified_type is None:
            continue

        # Double-check: if field_name is now in union_metadata, skip it
        # (this handles the case where union_metadata was populated during _simplify_type)
        if field_name in union_metadata:
            continue

        # Build description
        description = field_info.description or ""
        if desc_addition:
            description = f"{description}{desc_addition}".strip()

        # Create Field with description
        if default_factory is not None:
            # Handle default_factory (e.g., default_factory=list)
            # For LLM compatibility, convert to Optional with default=None
            # This allows LLMs to return None instead of empty lists
            # We'll convert None back to empty lists when reconstructing the full model
            new_fields[field_name] = (
                Optional[simplified_type],
                Field(default=None, description=description),
            )
        elif default is None and not field_info.is_required():
            new_fields[field_name] = (
                Optional[simplified_type],
                Field(default=None, description=description),
            )
        elif default is not None:
            new_fields[field_name] = (
                simplified_type,
                Field(default=default, description=description),
            )
        else:
            new_fields[field_name] = (simplified_type, Field(description=description))

    # Handle Union field splitting - add separate fields
    for field_name, union_info_list in union_metadata.items():
        for union_info in union_info_list:
            for new_field_name, field_data in union_info["fields"].items():
                simplified_type = field_data["simplified_type"]
                description = f"Part of Union field '{field_name}'. {field_data['description'] or ''}"
                description = description.strip()
                new_fields[new_field_name] = (
                    Optional[simplified_type],
                    Field(default=None, description=description),
                )

    # Create the simplified model
    simplified_model_name = f"Simplified{source_model.__name__}"
    simplified_model = create_model(simplified_model_name, **new_fields)

    # Cache the result
    result = (simplified_model, union_metadata)
    memo[source_model] = result
    if use_module_cache:
        cache_key = (source_model, tuple(field_filter) if field_filter else None)
        _SIMPLIFIED_MODEL_CACHE[cache_key] = result

    return result
