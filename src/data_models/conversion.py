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
# Updated to align with PULSE ontology
JSONLD_TO_PYDANTIC_MAPPING = {
    # Schema.org properties
    "http://schema.org/name": "name",
    "schema:name": "name",
    "http://schema.org/description": "description",
    "schema:description": "description",
    "http://schema.org/url": "url",
    "schema:url": "url",
    "http://schema.org/identifier": "identifier",
    "schema:identifier": "identifier",
    "http://schema.org/dateCreated": "dateCreated",
    "schema:dateCreated": "dateCreated",
    "http://schema.org/datePublished": "datePublished",
    "schema:datePublished": "datePublished",
    "http://schema.org/license": "license",
    "schema:license": "license",
    "http://schema.org/author": "author",
    "schema:author": "author",
    "http://schema.org/codeRepository": "codeRepository",
    "schema:codeRepository": "codeRepository",
    "http://schema.org/programmingLanguage": "programmingLanguage",
    "schema:programmingLanguage": "programmingLanguage",
    "http://schema.org/applicationCategory": "applicationCategory",
    "schema:applicationCategory": "applicationCategory",
    "http://schema.org/featureList": "featureList",
    "schema:featureList": "featureList",
    "http://schema.org/image": "image",
    "schema:image": "image",
    "http://schema.org/isAccessibleForFree": "isAccessibleForFree",
    "schema:isAccessibleForFree": "isAccessibleForFree",
    "http://schema.org/isBasedOn": "isBasedOn",
    "schema:isBasedOn": "isBasedOn",
    "http://schema.org/operatingSystem": "operatingSystem",
    "schema:operatingSystem": "operatingSystem",
    "http://schema.org/softwareRequirements": "softwareRequirements",
    "schema:softwareRequirements": "softwareRequirements",
    "http://schema.org/processorRequirements": "processorRequirements",
    "schema:processorRequirements": "processorRequirements",
    "http://schema.org/memoryRequirements": "memoryRequirements",
    "schema:memoryRequirements": "memoryRequirements",
    "http://schema.org/supportingData": "supportingData",
    "schema:supportingData": "supportingData",
    "http://schema.org/conditionsOfAccess": "conditionsOfAccess",
    "schema:conditionsOfAccess": "conditionsOfAccess",
    "http://schema.org/citation": "citation",
    "schema:citation": "citation",
    "http://schema.org/affiliation": "affiliation",
    "schema:affiliation": "affiliation",
    "http://schema.org/legalName": "legalName",
    "schema:legalName": "legalName",
    "http://schema.org/encodingFormat": "encodingFormat",
    "schema:encodingFormat": "encodingFormat",
    "http://schema.org/defaultValue": "defaultValue",
    "schema:defaultValue": "defaultValue",
    "http://schema.org/valueRequired": "valueRequired",
    "schema:valueRequired": "valueRequired",
    "http://schema.org/measurementTechnique": "measurementTechnique",
    "schema:measurementTechnique": "measurementTechnique",
    "http://schema.org/variableMeasured": "variableMeasured",
    "schema:variableMeasured": "variableMeasured",
    "http://schema.org/contentUrl": "contentUrl",
    "schema:contentUrl": "contentUrl",
    "http://schema.org/softwareVersion": "softwareVersion",
    "schema:softwareVersion": "softwareVersion",
    "http://schema.org/email": "email",
    "schema:email": "email",
    "http://schema.org/username": "username",
    "schema:username": "username",
    "http://schema.org/memberOf": "memberOf",
    "schema:memberOf": "memberOf",
    "http://schema.org/keywords": "keywords",
    "schema:keywords": "keywords",
    "http://schema.org/abstract": "abstract",
    "schema:abstract": "abstract",
    "http://schema.org/parentOrganization": "parentOrganization",
    "schema:parentOrganization": "parentOrganization",
    "http://schema.org/knowsAbout": "knowsAbout",
    "schema:knowsAbout": "knowsAbout",
    # SD ontology properties
    "https://w3id.org/okn/o/sd#hasDocumentation": "hasDocumentation",
    "sd:hasDocumentation": "hasDocumentation",
    "https://w3id.org/okn/o/sd#hasExecutableInstructions": "hasExecutableInstructions",
    "sd:hasExecutableInstructions": "hasExecutableInstructions",
    "https://w3id.org/okn/o/sd#hasAcknowledgements": "hasAcknowledgements",
    "sd:hasAcknowledgements": "hasAcknowledgements",
    "https://w3id.org/okn/o/sd#readme": "readme",
    "sd:readme": "readme",
    "https://w3id.org/okn/o/sd#hasFunding": "hasFunding",
    "sd:hasFunding": "hasFunding",
    "https://w3id.org/okn/o/sd#hasSoftwareImage": "hasSoftwareImage",
    "sd:hasSoftwareImage": "hasSoftwareImage",
    "https://w3id.org/okn/o/sd#hasFormat": "hasFormat",
    "sd:hasFormat": "hasFormat",
    "https://w3id.org/okn/o/sd#hasDimensionality": "hasDimensionality",
    "sd:hasDimensionality": "hasDimensionality",
    "https://w3id.org/okn/o/sd#availableInRegistry": "availableInRegistry",
    "sd:availableInRegistry": "availableInRegistry",
    "https://w3id.org/okn/o/sd#fundingGrant": "fundingGrant",
    "sd:fundingGrant": "fundingGrant",
    "https://w3id.org/okn/o/sd#fundingSource": "fundingSource",
    "sd:fundingSource": "fundingSource",
    # PULSE ontology properties (updated from imaging-plaza)
    "https://open-pulse.epfl.ch/ontology#imagingModality": "imagingModality",
    "pulse:imagingModality": "imagingModality",
    "https://open-pulse.epfl.ch/ontology#isPluginModuleOf": "isPluginModuleOf",
    "pulse:isPluginModuleOf": "isPluginModuleOf",
    "https://open-pulse.epfl.ch/ontology#relatedToOrganization": "relatedToOrganization",
    "pulse:relatedToOrganization": "relatedToOrganization",
    "https://open-pulse.epfl.ch/ontology#requiresGPU": "requiresGPU",
    "pulse:requiresGPU": "requiresGPU",
    "https://open-pulse.epfl.ch/ontology#hasExecutableNotebook": "hasExecutableNotebook",
    "pulse:hasExecutableNotebook": "hasExecutableNotebook",
    "https://open-pulse.epfl.ch/ontology#gitAuthors": "gitAuthors",
    "pulse:gitAuthors": "gitAuthors",
    "https://open-pulse.epfl.ch/ontology#commits": "commits",
    "pulse:commits": "commits",
    "https://open-pulse.epfl.ch/ontology#discipline": "discipline",
    "pulse:discipline": "discipline",
    "https://open-pulse.epfl.ch/ontology#repositoryType": "repositoryType",
    "pulse:repositoryType": "repositoryType",
    "https://open-pulse.epfl.ch/ontology#username": "username",
    "pulse:username": "username",
    "https://open-pulse.epfl.ch/ontology#hasRepository": "hasRepository",
    "pulse:hasRepository": "hasRepository",
    "https://open-pulse.epfl.ch/ontology#hasAcademicCatalogRelation": "hasAcademicCatalogRelation",
    "pulse:hasAcademicCatalogRelation": "hasAcademicCatalogRelation",
    "https://open-pulse.epfl.ch/ontology#catalogType": "catalogType",
    "pulse:catalogType": "catalogType",
    "https://open-pulse.epfl.ch/ontology#entityType": "entityType",
    "pulse:entityType": "entityType",
    "https://open-pulse.epfl.ch/ontology#hasCatalogEntity": "hasCatalogEntity",
    "pulse:hasCatalogEntity": "hasCatalogEntity",
    "https://open-pulse.epfl.ch/ontology#confidence": "confidence",
    "pulse:confidence": "confidence",
    "https://open-pulse.epfl.ch/ontology#justification": "justification",
    "pulse:justification": "justification",
    "https://open-pulse.epfl.ch/ontology#matchedOn": "matchedOn",
    "pulse:matchedOn": "matchedOn",
    "https://open-pulse.epfl.ch/ontology#uuid": "uuid",
    "pulse:uuid": "uuid",
    "https://open-pulse.epfl.ch/ontology#email": "email",
    "pulse:email": "email",
    "https://open-pulse.epfl.ch/ontology#profileUrl": "profileUrl",
    "pulse:profileUrl": "profileUrl",
    # MD4I properties
    "http://w3id.org/nfdi4ing/metadata4ing#orcid": "orcid",
    "md4i:orcid": "orcid",
    "http://w3id.org/nfdi4ing/metadata4ing#orcidId": "orcid",
    "md4i:orcidId": "orcid",
    "http://w3id.org/nfdi4ing/metadata4ing#hasRorId": "hasRorId",
    "md4i:hasRorId": "hasRorId",
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

        # All other fields (affiliationHistory, linkedEntities, etc.)
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
        funding_source = Organization(
            type="Organization",
            legalName="Unknown",
        )  # Default
        if source_ref and source_ref in all_entities:
            converted = _convert_entity(all_entities[source_ref], all_entities)
            if isinstance(converted, Organization):
                funding_source = converted
        return FundingInformation(
            identifier=_get_value(entity.get("http://schema.org/identifier")),
            fundingGrant=_get_value(
                entity.get("https://w3id.org/okn/o/sd#fundingGrant"),
            ),
            fundingSource=funding_source,
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
        "type": "@type",
        "name": "schema:name",
        "emails": "schema:email",
        "githubId": "schema:username",
        "orcid": "md4i:orcidId",
        "affiliations": "schema:affiliation",
        "affiliationHistory": "pulse:affiliationHistory",
        "source": "pulse:source",
        "linkedEntities": "pulse:linkedEntities",
    },
    "Organization": {
        "type": "@type",
        "legalName": "schema:legalName",
        "hasRorId": "md4i:hasRorId",
        "organizationType": "schema:additionalType",
        "parentOrganization": "schema:parentOrganization",
        "country": "schema:addressCountry",
        "website": "schema:url",
        "attributionConfidence": "pulse:confidence",
        "academicCatalogRelations": "pulse:hasAcademicCatalogRelation",
    },
    "GitHubOrganization": {
        "name": "schema:name",
        "organizationType": "schema:additionalType",
        "description": "schema:description",
        "discipline": "pulse:discipline",
        "disciplineJustification": "pulse:justification",
        "relatedToEPFL": "pulse:relatedToEPFL",
        "relatedToEPFLJustification": "pulse:justification",
        "relatedToEPFLConfidence": "pulse:confidence",
        "academicCatalogRelations": "pulse:hasAcademicCatalogRelation",
        "githubOrganizationMetadata": "pulse:metadata",
    },
    "GitHubUser": {
        "name": "schema:name",
        "fullname": "schema:name",
        "githubHandle": "schema:username",
        "githubUserMetadata": "pulse:metadata",
        "relatedToOrganization": "pulse:relatedToOrganization",
        "relatedToOrganizationJustification": "pulse:justification",
        "discipline": "pulse:discipline",
        "disciplineJustification": "pulse:justification",
        "position": "schema:jobTitle",
        "positionJustification": "pulse:justification",
        "relatedToEPFL": "pulse:relatedToEPFL",
        "relatedToEPFLJustification": "pulse:justification",
        "relatedToEPFLConfidence": "pulse:confidence",
        "academicCatalogRelations": "pulse:hasAcademicCatalogRelation",
    },
    "Commits": {
        "total": "pulse:totalCommits",
        "firstCommitDate": "pulse:firstCommitDate",
        "lastCommitDate": "pulse:lastCommitDate",
    },
    "GitAuthor": {
        "name": "schema:name",
        "email": "pulse:email",
        "commits": "pulse:commits",
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
        "confidence": "pulse:confidence",
        "justification": "pulse:justification",
    },
    "AcademicCatalogRelation": {
        "catalogType": "pulse:catalogType",
        "entityType": "pulse:entityType",
        "entity": "pulse:hasCatalogEntity",
        "confidence": "pulse:confidence",
        "justification": "pulse:justification",
        "matchedOn": "pulse:matchedOn",
    },
    "CatalogEntity": {
        "uuid": "pulse:uuid",
        "name": "schema:name",
        "email": "pulse:email",
        "orcid": "md4i:orcidId",
        "affiliation": "schema:affiliation",
        "profileUrl": "pulse:profileUrl",
    },
    "InfosciencePublication": {
        "type": "@type",
        "uuid": "pulse:uuid",
        "title": "schema:name",
        "authors": "schema:author",
        "abstract": "schema:abstract",
        "doi": "schema:identifier",
        "publication_date": "schema:datePublished",
        "publication_type": "schema:additionalType",
        "url": "schema:url",
        "repository_url": "schema:codeRepository",
        "lab": "schema:affiliation",
        "subjects": "schema:keywords",
    },
    "InfoscienceAuthor": {
        "type": "@type",
        "uuid": "pulse:uuid",
        "name": "schema:name",
        "email": "pulse:email",
        "orcid": "md4i:orcidId",
        "affiliation": "schema:affiliation",
        "profile_url": "pulse:profileUrl",
    },
    "InfoscienceLab": {
        "type": "@type",
        "uuid": "pulse:uuid",
        "name": "schema:name",
        "description": "schema:description",
        "url": "schema:url",
        "parent_organization": "schema:parentOrganization",
        "website": "schema:url",
        "research_areas": "schema:knowsAbout",
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
        "isPluginModuleOf": "pulse:isPluginModuleOf",
        "license": "schema:license",
        "author": "schema:author",
        "relatedToOrganizations": "pulse:relatedToOrganization",
        "operatingSystem": "schema:operatingSystem",
        "programmingLanguage": "schema:programmingLanguage",
        "softwareRequirements": "schema:softwareRequirements",
        "processorRequirements": "schema:processorRequirements",
        "memoryRequirements": "schema:memoryRequirements",
        "requiresGPU": "pulse:requiresGPU",
        "supportingData": "schema:supportingData",
        "url": "schema:url",
        "identifier": "schema:identifier",
        "hasAcknowledgements": "sd:hasAcknowledgements",
        "hasDocumentation": "sd:hasDocumentation",
        "hasExecutableInstructions": "sd:hasExecutableInstructions",
        "hasExecutableNotebook": "pulse:hasExecutableNotebook",
        "readme": "sd:readme",
        "hasFunding": "sd:hasFunding",
        "hasSoftwareImage": "sd:hasSoftwareImage",
        "imagingModality": "pulse:imagingModality",
        "gitAuthors": "pulse:gitAuthors",
        "relatedToOrganizationJustification": "pulse:justification",
        "repositoryType": "pulse:repositoryType",
        "repositoryTypeJustification": "pulse:justification",
        "relatedToEPFL": "pulse:relatedToEPFL",
        "relatedToEPFLConfidence": "pulse:confidence",
        "relatedToEPFLJustification": "pulse:justification",
        "academicCatalogRelations": "pulse:hasAcademicCatalogRelation",
        "relatedDatasets": "pulse:relatedDatasets",
        "relatedPublications": "pulse:relatedPublications",
        "relatedModels": "pulse:relatedModels",
        "relatedAPIs": "pulse:relatedAPIs",
        "discipline": "pulse:discipline",
        "disciplineJustification": "pulse:justification",
        "linkedEntities": "pulse:linkedEntities",
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
    context = {
        "schema": "http://schema.org/",
        "sd": "https://w3id.org/okn/o/sd#",
        "pulse": "https://open-pulse.epfl.ch/ontology#",
        "md4i": "http://w3id.org/nfdi4ing/metadata4ing#",
        "rdf": "http://www.w3.org/1999/02/22-rdf-syntax-ns#",
        "rdfs": "http://www.w3.org/2000/01/rdf-schema#",
        "owl": "http://www.w3.org/2002/07/owl#",
        "xsd": "http://www.w3.org/2001/XMLSchema#",
        "dcterms": "http://purl.org/dc/terms/",
        "wd": "http://www.wikidata.org/entity/",
    }

    # Helper function to generate IRI for a Person
    def _generate_person_iri(person_obj: Any) -> Optional[str]:
        """Generate a stable IRI for a Person based on their identifiers."""
        # Priority: explicit id > githubId > ORCID > email
        # New JSON structure has explicit 'id' field
        if hasattr(person_obj, "id") and person_obj.id:
            person_id = str(person_obj.id)
            if person_id.startswith("http"):
                return person_id
        
        # Fallback to githubId (new structure)
        if hasattr(person_obj, "githubId") and person_obj.githubId:
            return f"https://github.com/{person_obj.githubId}"
        
        if hasattr(person_obj, "orcid") and person_obj.orcid:
            orcid = str(person_obj.orcid)
            if orcid.startswith("http"):
                return orcid
            return f"https://orcid.org/{orcid}"
        # New structure may include emails array
        if hasattr(person_obj, "emails") and person_obj.emails:
            return f"mailto:{person_obj.emails[0]}"

        return None

    # Helper to make any value JSON-serializable
    def _make_json_serializable(obj: Any) -> Any:
        """Recursively convert objects to JSON-serializable types."""
        if obj is None or isinstance(obj, (str, int, float, bool)):
            return obj
        if isinstance(obj, HttpUrl):
            return str(obj)
        if isinstance(obj, date):
            return obj.isoformat()
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, dict):
            return {k: _make_json_serializable(v) for k, v in obj.items()}
        if isinstance(obj, (list, tuple)):
            return [_make_json_serializable(item) for item in obj]
        if isinstance(obj, BaseModel):
            return obj.model_dump(exclude_unset=True, exclude_none=True, mode='json')
        # Fallback for other types
        return str(obj)

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
            if isinstance(obj, dict):
                # Recursively ensure all values in dict are JSON-serializable
                return _make_json_serializable(obj)
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
        elif (
            model_name == "GitHubUser"
            and hasattr(obj, "githubUserMetadata")
            and obj.githubUserMetadata
        ):
            # Use html_url from githubUserMetadata for GitHubUser
            jsonld_entity["@id"] = obj.githubUserMetadata.html_url
        elif (
            model_name == "GitHubOrganization"
            and hasattr(obj, "githubOrganizationMetadata")
            and obj.githubOrganizationMetadata
        ):
            # Use html_url from githubOrganizationMetadata for GitHubOrganization
            jsonld_entity["@id"] = obj.githubOrganizationMetadata.html_url
        elif model_name == "Person":
            # Generate IRI for Person based on their identifiers
            person_iri = _generate_person_iri(obj)
            if person_iri:
                jsonld_entity["@id"] = person_iri
        elif model_name == "GitHubUser" and base_url:
            # Fallback to base_url if provided for users
            jsonld_entity["@id"] = base_url
        elif model_name == "GitHubOrganization" and base_url:
            # Fallback to base_url if provided for organizations
            jsonld_entity["@id"] = base_url

        type_mapping = {
            "SoftwareSourceCode": "schema:SoftwareSourceCode",
            "Person": "schema:Person",
            "Organization": "schema:Organization",
            "GitHubOrganization": "schema:GitHubOrganization",
            "GitHubUser": "schema:Person",
            "DataFeed": "schema:DataFeed",
            "FormalParameter": "schema:PropertyValue",
            "ExecutableNotebook": "schema:SoftwareApplication",
            "SoftwareImage": "schema:SoftwareApplication",
            "Image": "schema:ImageObject",
            "FundingInformation": "schema:Grant",
            "GitAuthor": "schema:Person",
            "InfoscienceEntity": "schema:Thing",
            "AcademicCatalogRelation": "pulse:AcademicCatalogRelation",
            "CatalogEntity": "pulse:CatalogEntity",
            "InfosciencePublication": "schema:ScholarlyArticle",
            "InfoscienceAuthor": "schema:Person",
            "InfoscienceLab": "schema:Organization",
            "Discipline": "pulse:DisciplineEnumeration",
            "RepositoryType": "pulse:RepositoryTypeEnumeration",
        }

        if model_name in type_mapping:
            jsonld_entity["@type"] = type_mapping[model_name]

        # Convert each field
        for pydantic_key, value in obj:
            if value is None:
                continue

            if pydantic_key not in key_map:
                continue

            # Skip the 'type' field for models - we handle @type via type_mapping
            if pydantic_key == "type":
                continue

            jsonld_key = key_map[pydantic_key]

            # Special handling for author field - just create IRI references
            if pydantic_key == "author" and isinstance(value, list):
                author_refs = []

                for item in value:
                    # Case 1: Pydantic Person model -> use IRI generator
                    if (
                        isinstance(item, BaseModel)
                        and item.__class__.__name__ == "Person"
                    ):
                        person_iri = _generate_person_iri(item)
                        if person_iri:
                            author_refs.append({"@id": person_iri})

                    # Case 2: Raw dict (newer JSON) - prefer explicit 'id' field
                    elif isinstance(item, dict):
                        person_id = item.get("id") or item.get("url") or None
                        if person_id:
                            author_refs.append({"@id": person_id})

                    # Fallback: primitives or other types - try converting
                    else:
                        converted = _convert_entity_to_jsonld(item)
                        if converted is not None:
                            # If converted is a string/IRI, keep as author ref
                            if isinstance(converted, str) and (
                                converted.startswith("http://") or converted.startswith("https://")
                            ):
                                author_refs.append({"@id": converted})
                            else:
                                author_refs.append(converted)

                if author_refs:
                    jsonld_entity["schema:author"] = author_refs

                continue  # Skip the normal list handling below

            # Special handling for linkedEntities - preserve linkedEntities and extract DOIs as citations
            if pydantic_key == "linkedEntities" and isinstance(value, list):
                linked_entities_jsonld = []
                citation_urls = []

                for item in value:
                    # Item may be a Pydantic model or a raw dict
                    if isinstance(item, BaseModel):
                        converted = _convert_entity_to_jsonld(item)
                        if converted:
                            linked_entities_jsonld.append(converted)

                        # Try to extract DOI if present on the model (best-effort)
                        doi_val = None
                        if hasattr(item, "entityInfosciencePublication"):
                            ent = getattr(item, "entityInfosciencePublication")
                            if isinstance(ent, dict):
                                doi_val = ent.get("doi")

                    elif isinstance(item, dict):
                        # Keep the raw dict converted normally
                        converted = _convert_entity_to_jsonld(item)
                        if converted:
                            linked_entities_jsonld.append(converted)

                        # Look for infoscience entries and pull DOI
                        catalog = item.get("catalogType")
                        if catalog and isinstance(catalog, str) and catalog.lower() == "infoscience":
                            ent = item.get("entity") or item.get("entityInfosciencePublication") or {}
                            doi_val = None
                            if isinstance(ent, dict):
                                doi_val = ent.get("doi")
                                # Sometimes DOI is nested under identifiers
                                if not doi_val and "identifiers" in ent:
                                    for ident in ent.get("identifiers", []):
                                        if isinstance(ident, dict) and ident.get("type") == "doi":
                                            doi_val = ident.get("value")

                            if doi_val:
                                doi_str = str(doi_val).strip()
                                if doi_str and not doi_str.lower().startswith("http"):
                                    citation_urls.append(f"https://doi.org/{doi_str}")
                                else:
                                    citation_urls.append(doi_str)

                    else:
                        # Fallback conversion
                        converted = _convert_entity_to_jsonld(item)
                        if converted:
                            linked_entities_jsonld.append(converted)

                if linked_entities_jsonld:
                    jsonld_entity[jsonld_key] = linked_entities_jsonld
                if citation_urls:
                    # Merge with any existing citation entries
                    existing = jsonld_entity.get("schema:citation", [])
                    jsonld_entity["schema:citation"] = list(dict.fromkeys(existing + citation_urls))

                continue

            # Special handling for relatedPublications - extract as schema:citation
            if pydantic_key == "relatedPublications" and isinstance(value, list):
                publication_urls = []
                
                for item in value:
                    # Items should be strings (URLs)
                    if isinstance(item, str):
                        url = item.strip()
                        if url:
                            publication_urls.append(url)
                    elif isinstance(item, BaseModel):
                        # Handle if it's a Pydantic model (unlikely but defensive)
                        converted = _convert_entity_to_jsonld(item)
                        if isinstance(converted, str):
                            publication_urls.append(converted)
                    elif isinstance(item, dict):
                        # Handle if it's a dict with url field (unlikely but defensive)
                        url = item.get("url") or item.get("@id")
                        if url:
                            publication_urls.append(str(url).strip())

                # Also preserve the original relatedPublications field
                if publication_urls:
                    jsonld_entity[jsonld_key] = publication_urls
                    
                    # Add to schema:citation
                    existing = jsonld_entity.get("schema:citation", [])
                    jsonld_entity["schema:citation"] = list(dict.fromkeys(existing + publication_urls))

                continue

            # Handle lists
            if isinstance(value, list):
                jsonld_values = []
                for item in value:
                    if isinstance(item, BaseModel):
                        item_model_name = item.__class__.__name__

                        # For Person objects in author field, just output IRI reference
                        if item_model_name == "Person" and pydantic_key == "author":
                            person_iri = _generate_person_iri(item)
                            if person_iri:
                                jsonld_values.append({"@id": person_iri})
                        # For Affiliation models, extract organizationId (URL) or name
                        elif item_model_name == "Affiliation" and pydantic_key in ["affiliations", "affiliation"]:
                            org_id = getattr(item, "organizationId", None)
                            if org_id and isinstance(org_id, str) and (org_id.startswith("http://") or org_id.startswith("https://")):
                                jsonld_values.append({"@id": org_id})
                            elif hasattr(item, "name") and item.name:
                                jsonld_values.append(item.name)
                        else:
                            # Nested model - convert recursively
                            converted = _convert_entity_to_jsonld(item)
                            if converted:
                                jsonld_values.append(converted)
                    else:
                        # Special handling for affiliation objects present as dicts
                        if pydantic_key in ["affiliations", "affiliation"] and isinstance(item, dict):
                            # Prefer organizationId if it's a URL, otherwise use name
                            org_id = item.get("organizationId")
                            affiliation_value = None
                            
                            if org_id and isinstance(org_id, str) and (org_id.startswith("http://") or org_id.startswith("https://")):
                                affiliation_value = {"@id": org_id}
                            elif item.get("name"):
                                affiliation_value = item.get("name")
                            
                            if affiliation_value:
                                jsonld_values.append(affiliation_value)

                        # Author dicts may contain an explicit id we should use as IRI
                        elif pydantic_key == "author" and isinstance(item, dict) and item.get("id"):
                            jsonld_values.append({"@id": item.get("id")})

                        else:
                            # Primitive or HttpUrl or other - fallback to normal conversion
                            converted = _convert_entity_to_jsonld(item)
                            if converted is not None:
                                jsonld_values.append(converted)

                if jsonld_values:
                    jsonld_entity[jsonld_key] = jsonld_values

            # Handle nested models
            elif isinstance(value, BaseModel):
                nested_model_name = value.__class__.__name__

                # For Person objects in author field, just output IRI reference
                if nested_model_name == "Person" and pydantic_key == "author":
                    person_iri = _generate_person_iri(value)
                    if person_iri:
                        jsonld_entity[jsonld_key] = {"@id": person_iri}
                else:
                    converted = _convert_entity_to_jsonld(value)
                    if converted:
                        jsonld_entity[jsonld_key] = converted

            # Handle dictionaries that might be serialized BaseModels
            elif isinstance(value, dict):
                # Special case: if this is 'entity' field in AcademicCatalogRelation,
                # it might be a dict representation of InfosciencePublication/Author/Lab
                # Try to detect and map the fields appropriately
                if pydantic_key == "entity" and model_name == "AcademicCatalogRelation":
                    # Determine the entity type and apply appropriate mapping
                    entity_dict = {}

                    # Detect which type based on fields present
                    entity_mapping = None
                    if "title" in value and "authors" in value:
                        # InfosciencePublication
                        entity_mapping = PYDANTIC_TO_ZOD_MAPPING.get(
                            "InfosciencePublication",
                            {},
                        )
                        entity_dict["@type"] = "schema:ScholarlyArticle"
                    elif "profile_url" in value or (
                        "uuid" in value and "email" in value and "orcid" in value
                    ):
                        # InfoscienceAuthor
                        entity_mapping = PYDANTIC_TO_ZOD_MAPPING.get(
                            "InfoscienceAuthor",
                            {},
                        )
                        entity_dict["@type"] = "schema:Person"
                    elif "parent_organization" in value or ("research_areas" in value):
                        # InfoscienceLab
                        entity_mapping = PYDANTIC_TO_ZOD_MAPPING.get(
                            "InfoscienceLab",
                            {},
                        )
                        entity_dict["@type"] = "schema:Organization"
                    elif "name" in value:
                        # CatalogEntity
                        entity_mapping = PYDANTIC_TO_ZOD_MAPPING.get(
                            "CatalogEntity",
                            {},
                        )
                        entity_dict["@type"] = "pulse:CatalogEntity"

                    if entity_mapping:
                        # Map the fields using the detected mapping
                        for entity_key, entity_value in value.items():
                            if entity_value is not None:
                                mapped_key = entity_mapping.get(entity_key, entity_key)
                                # Recursively convert nested values
                                converted_value = _convert_entity_to_jsonld(
                                    entity_value,
                                )
                                entity_dict[mapped_key] = (
                                    converted_value
                                    if converted_value is not None
                                    else entity_value
                                )
                        jsonld_entity[jsonld_key] = entity_dict
                    else:
                        # Fallback: use dict as-is
                        jsonld_entity[jsonld_key] = value
                else:
                    # Regular dict - use as-is but try to convert nested values
                    jsonld_entity[jsonld_key] = value

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

    # Collect all Person entities encountered during conversion
    person_entities = {}  # Dict to deduplicate by IRI

    def _collect_and_convert_person(person_obj: Any) -> Optional[str]:
        """Convert a Person object and collect it, returning its IRI."""
        person_iri = _generate_person_iri(person_obj)
        if not person_iri:
            return None

        # If we haven't seen this person yet, convert and store them
        if person_iri not in person_entities:
            person_entity = _convert_entity_to_jsonld(person_obj, entity_id=person_iri)
            if person_entity:
                person_entities[person_iri] = person_entity

        return person_iri

    # Convert the main object
    main_entity = _convert_entity_to_jsonld(pydantic_obj, base_url)

    if not main_entity:
        return {}

    # Collect Person entities from the main object
    # We need to traverse and collect all Person objects
    def _collect_persons_from_obj(obj: Any):
        """Recursively collect Person objects from the data structure."""
        if isinstance(obj, BaseModel):
            if obj.__class__.__name__ == "Person":
                _collect_and_convert_person(obj)
            # Traverse all fields
            for key, value in obj:
                _collect_persons_from_obj(value)
        elif isinstance(obj, list):
            for item in obj:
                _collect_persons_from_obj(item)
        elif isinstance(obj, dict):
            for value in obj.values():
                _collect_persons_from_obj(value)

    # Collect all Person entities
    _collect_persons_from_obj(pydantic_obj)

    # Build the graph with main entity and all collected persons
    graph_entities = [main_entity]
    graph_entities.extend(person_entities.values())

    # Return as JSON-LD graph structure
    result = {
        "@context": context,
        "@graph": graph_entities,
    }

    # Ensure all values are JSON-serializable before returning
    return _make_json_serializable(result)


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
        return (
            str,
            " (Original type: HttpUrl, format: string URL like 'https://example.com/path')",
        )

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
