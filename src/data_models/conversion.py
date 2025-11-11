"""
Conversion functions for the data models
"""

from datetime import date, datetime
from enum import Enum
from typing import Any, Dict, Optional, Union, get_args, get_origin
from typing import List as ListType

from pydantic import BaseModel, HttpUrl

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
    InfoscienceEntity,
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
        funding_source = Organization(type="Organization", legalName="Unknown")  # Default
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
        "email": "pulse:email",
        "orcid": "md4i:orcidId",
        "gitAuthorIds": "pulse:gitAuthorIds",
        "affiliations": "schema:affiliation",
        "currentAffiliation": "schema:affiliation",
        "affiliationHistory": "pulse:affiliationHistory",
        "contributionSummary": "pulse:contributionSummary",
        "biography": "schema:description",
        "academicCatalogRelations": "pulse:hasAcademicCatalogRelation",
    },
    "Organization": {
        "type": "@type",
        "legalName": "schema:legalName",
        "hasRorId": "md4i:hasRorId",
        "alternateNames": "schema:alternateName",
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


def convert_pydantic_to_jsonld(pydantic_obj: Any, base_url: Optional[str] = None) -> Union[Dict, ListType]:
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
    
    # Define namespace prefixes for the @context (aligned with PULSE ontology)
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
    
    # Helper function to convert a single entity
    def _convert_entity_to_jsonld(obj: Any, entity_id: Optional[str] = None) -> Optional[Dict]:
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
        
        # Add @type based on model name (aligned with PULSE ontology)
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
                        entity_mapping = PYDANTIC_TO_ZOD_MAPPING.get("InfosciencePublication", {})
                        entity_dict["@type"] = "schema:ScholarlyArticle"
                    elif "profile_url" in value or ("uuid" in value and "email" in value and "orcid" in value):
                        # InfoscienceAuthor
                        entity_mapping = PYDANTIC_TO_ZOD_MAPPING.get("InfoscienceAuthor", {})
                        entity_dict["@type"] = "schema:Person"
                    elif "parent_organization" in value or ("research_areas" in value):
                        # InfoscienceLab
                        entity_mapping = PYDANTIC_TO_ZOD_MAPPING.get("InfoscienceLab", {})
                        entity_dict["@type"] = "schema:Organization"
                    elif "name" in value:
                        # CatalogEntity
                        entity_mapping = PYDANTIC_TO_ZOD_MAPPING.get("CatalogEntity", {})
                        entity_dict["@type"] = "pulse:CatalogEntity"
                    
                    if entity_mapping:
                        # Map the fields using the detected mapping
                        for entity_key, entity_value in value.items():
                            if entity_value is not None:
                                mapped_key = entity_mapping.get(entity_key, entity_key)
                                # Recursively convert nested values
                                converted_value = _convert_entity_to_jsonld(entity_value)
                                entity_dict[mapped_key] = converted_value if converted_value is not None else entity_value
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
    
    # Convert the main object
    main_entity = _convert_entity_to_jsonld(pydantic_obj, base_url)
    
    if not main_entity:
        return {}
    
    # Return as JSON-LD graph structure
    result = {
        "@context": context,
        "@graph": [main_entity]
    }
    
    return result
