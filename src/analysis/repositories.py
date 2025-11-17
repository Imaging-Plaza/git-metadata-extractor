import logging
from datetime import datetime
from typing import Optional

from ..agents.atomic_agents import (
    check_epfl_relationship,
    compile_repository_context,
    generate_structured_output,
)
from ..agents.epfl_assessment import assess_epfl_relationship
from ..agents.linked_entities_enrichment import enrich_repository_linked_entities
from ..agents.organization_enrichment import enrich_organizations_from_dict
from ..agents.user_enrichment import enrich_users_from_dict
from ..cache.cache_manager import CacheManager, get_cache_manager
from ..context import prepare_repository_context
from ..data_models import Affiliation, Organization, SoftwareSourceCode
from ..gimie_utils.gimie_methods import extract_gimie
from ..utils.utils import enrich_authors_with_orcid

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

from ..utils.utils import is_github_repo_public


class Repository:
    def __init__(self, full_path: str, force_refresh: bool = False):
        # Initialize all attributes first
        self.full_path: str = full_path
        self.data: SoftwareSourceCode = None
        self.gimie = None
        self.log: list[str] = []
        self.cache_manager: CacheManager = get_cache_manager()
        self.force_refresh: bool = force_refresh

        # Track official API-reported token usage across all agents
        self.total_input_tokens: int = 0
        self.total_output_tokens: int = 0

        # Track estimated token usage (client-side counts)
        self.estimated_input_tokens: int = 0
        self.estimated_output_tokens: int = 0

        # Track timing and status
        self.start_time: datetime = None
        self.end_time: datetime = None
        self.analysis_successful: bool = False

        # Check if the repository is public before proceeding
        self.is_public: bool = is_github_repo_public(full_path)
        if not self.is_public:
            logger.error(
                f"Cannot process repository: {full_path} is not public or not accessible",
            )

    def run_gimie_analysis(self):
        def fetch_gimie_data():
            return extract_gimie(self.full_path, format="json-ld")

        # Get GIMIE data
        jsonld_gimie_data = self.cache_manager.get_cached_or_fetch(
            api_type="gimie",
            params={"full_path": self.full_path, "format": "json-ld"},
            fetch_func=fetch_gimie_data,
            force_refresh=self.force_refresh,
        )

        if jsonld_gimie_data:
            # self.data = SoftwareSourceCode.model_validate(
            #     SoftwareSourceCode.convert_jsonld_to_pydantic(jsonld_gimie_data),
            # )
            self.gimie = jsonld_gimie_data

    async def run_llm_analysis(self):
        """Run LLM analysis using the atomic agent pipeline."""
        await self.run_atomic_llm_pipeline()

    async def run_atomic_llm_pipeline(self):
        """
        Run atomic agent pipeline: context compilation -> structured output -> EPFL check.

        This implements a two-stage pipeline:
        1. Context compiler: Gathers repository information using tools
        2. Structured output: Produces structured metadata from compiled context
        3. EPFL checker: Assesses EPFL relationship from compiled context
        """
        logger.info(f"Starting atomic LLM pipeline for {self.full_path}")

        # Prepare repository context (clone, extract content, etc.)
        context_result = await prepare_repository_context(
            self.full_path,
            max_tokens=40000,
        )

        if not context_result["success"]:
            logger.error(
                f"Failed to prepare repository context: {context_result.get('error')}",
            )
            return

        repository_content = context_result["input_text"]
        git_authors = context_result.get("git_authors", [])

        logger.debug("=" * 80)
        logger.debug("REPOSITORY CONTENT PROVIDED TO CONTEXT COMPILER:")
        logger.debug("=" * 80)
        logger.debug(f"Content length: {len(repository_content)} chars")
        logger.debug(f"First 1000 chars: {repository_content[:1000]}")
        logger.debug("=" * 80)

        # Extract structured authors and organizations from GIMIE
        gimie_authors_orgs = self._extract_gimie_authors_and_organizations()

        # Prepare GIMIE data as string if available
        gimie_data = None
        if self.gimie:
            import json as json_module

            # Include structured authors/orgs in GIMIE data for context compiler
            # Convert Pydantic models to dicts for JSON serialization
            authors_list = gimie_authors_orgs.get("authors", [])
            orgs_list = gimie_authors_orgs.get("organizations", [])

            gimie_data_dict = {
                "raw_gimie": self.gimie,
                "extracted_authors": [
                    a.model_dump() if hasattr(a, "model_dump") else a
                    for a in authors_list
                ],
                "extracted_organizations": [
                    o.model_dump() if hasattr(o, "model_dump") else o for o in orgs_list
                ],
            }

            gimie_data = json_module.dumps(gimie_data_dict, indent=2, default=str)
            logger.debug("=" * 80)
            logger.debug("GIMIE DATA PROVIDED TO CONTEXT COMPILER:")
            logger.debug("=" * 80)
            logger.debug(
                f"Extracted {len(gimie_authors_orgs.get('authors', []))} authors and {len(gimie_authors_orgs.get('organizations', []))} organizations",
            )
            logger.debug(
                gimie_data[:2000] if len(gimie_data) > 2000 else gimie_data,
            )  # First 2000 chars
            if len(gimie_data) > 2000:
                logger.debug(f"... (truncated, total length: {len(gimie_data)} chars)")
            logger.debug("=" * 80)
        else:
            logger.warning("No GIMIE data available for context compiler")

        # Stage 1: Compile repository context
        logger.info("Stage 1: Compiling repository context...")
        compiled_result = await compile_repository_context(
            repo_url=self.full_path,
            repository_content=repository_content,
            gimie_data=gimie_data,
            git_authors=git_authors,
        )

        compiled_context = compiled_result.get("data")
        usage = compiled_result.get("usage")

        if not compiled_context:
            logger.error("Context compilation failed")
            return

        # Accumulate usage from context compiler
        if usage:
            self.total_input_tokens += usage.get("input_tokens", 0)
            self.total_output_tokens += usage.get("output_tokens", 0)
            if "estimated_input_tokens" in usage:
                self.estimated_input_tokens += usage.get("estimated_input_tokens", 0)
                self.estimated_output_tokens += usage.get("estimated_output_tokens", 0)

        # Stage 2: Generate structured output
        logger.info("Stage 2: Generating structured output...")
        # Get simplified schema from the dynamically generated model
        # Import the simplified model (it's generated at module level in structured_output)
        from ..agents.atomic_agents.structured_output import _SIMPLIFIED_MODEL

        # Generate schema from the simplified model's JSON schema
        schema = _SIMPLIFIED_MODEL.model_json_schema()
        # Create a minimal example for reference
        example = {
            "name": "Example Repository",
            "repositoryType": "software",
            "repositoryTypeJustification": ["Contains source code"],
        }

        structured_result = await generate_structured_output(
            compiled_context=compiled_context,
            schema=schema,
            example=example,
        )

        structured_output = structured_result.get("data")
        usage = structured_result.get("usage")

        if not structured_output:
            logger.error("Structured output generation failed")
            return

        # Accumulate usage from structured output
        if usage:
            self.total_input_tokens += usage.get("input_tokens", 0)
            self.total_output_tokens += usage.get("output_tokens", 0)
            if "estimated_input_tokens" in usage:
                self.estimated_input_tokens += usage.get("estimated_input_tokens", 0)
                self.estimated_output_tokens += usage.get("estimated_output_tokens", 0)

        # Convert simplified output to SoftwareSourceCode
        # First convert to dict
        if hasattr(structured_output, "model_dump"):
            simplified_dict = structured_output.model_dump()
        else:
            simplified_dict = structured_output

        # Get union_metadata for reconciliation
        union_metadata = structured_result.get("union_metadata", {})

        # Convert simplified dict to full SoftwareSourceCode format
        full_dict = self._convert_simplified_to_full(
            simplified_dict,
            union_metadata,
            git_authors=git_authors,
        )

        # Stage 3: EPFL relationship check
        logger.info("Stage 3: Checking EPFL relationship...")
        epfl_result = await check_epfl_relationship(compiled_context=compiled_context)

        epfl_assessment = epfl_result.get("data")
        usage = epfl_result.get("usage")

        # Accumulate usage from EPFL checker
        if usage:
            self.total_input_tokens += usage.get("input_tokens", 0)
            self.total_output_tokens += usage.get("output_tokens", 0)
            if "estimated_input_tokens" in usage:
                self.estimated_input_tokens += usage.get("estimated_input_tokens", 0)
                self.estimated_output_tokens += usage.get("estimated_output_tokens", 0)

        # Add EPFL assessment to full_dict
        if epfl_assessment:
            if hasattr(epfl_assessment, "model_dump"):
                epfl_dict = epfl_assessment.model_dump()
            else:
                epfl_dict = epfl_assessment

            full_dict["relatedToEPFL"] = epfl_dict.get("relatedToEPFL")
            full_dict["relatedToEPFLConfidence"] = epfl_dict.get(
                "relatedToEPFLConfidence",
            )
            full_dict["relatedToEPFLJustification"] = epfl_dict.get(
                "relatedToEPFLJustification",
            )

        # Validate and create SoftwareSourceCode
        try:
            self.data = SoftwareSourceCode.model_validate(full_dict)
            logger.info("Atomic LLM pipeline completed successfully")
        except Exception as e:
            logger.error(f"Failed to validate SoftwareSourceCode: {e}", exc_info=True)

    def _extract_gimie_fields(self) -> dict:
        """
        Extract fields from GIMIE JSON-LD data that can be automatically populated.

        Returns:
            Dictionary with GIMIE-extracted fields
        """
        gimie_dict = {}

        if not self.gimie:
            logger.debug("No GIMIE data available for field extraction")
            return gimie_dict

        # GIMIE can be in different formats:
        # 1. A list directly: [{"@id": "...", "@type": [...]}, ...]
        # 2. A dict with @graph: {"@graph": [{"@id": "...", ...}, ...]}
        # 3. A dict that is the graph itself
        graph = None
        if isinstance(self.gimie, list):
            # Format 1: Direct list
            graph = self.gimie
            logger.debug(f"GIMIE data is a list with {len(graph)} entities")
        elif isinstance(self.gimie, dict):
            # Format 2: Dict with @graph
            if "@graph" in self.gimie:
                graph = self.gimie.get("@graph", [])
                logger.debug(f"GIMIE data has @graph with {len(graph)} entities")
            else:
                # Format 3: Single entity dict (treat as list with one item)
                graph = [self.gimie]
                logger.debug("GIMIE data is a single entity dict")
        else:
            logger.warning(f"Unexpected GIMIE data type: {type(self.gimie)}")
            return gimie_dict

        if not graph:
            logger.debug("GIMIE graph is empty")
            return gimie_dict
        for entity in graph:
            if not isinstance(entity, dict):
                continue

            entity_types = entity.get("@type", [])
            if not isinstance(entity_types, list):
                entity_types = [entity_types]

            # Check if this is a SoftwareSourceCode entity
            if "http://schema.org/SoftwareSourceCode" not in entity_types:
                logger.debug(f"Skipping entity type: {entity_types}")
                continue

            logger.debug("Found SoftwareSourceCode entity, extracting fields...")

            # Extract @id (repository URL) - this becomes the id field
            entity_id = entity.get("@id")
            if entity_id:
                gimie_dict["id"] = entity_id
                logger.debug(f"Extracted id from GIMIE: {entity_id}")

            # Helper to get value from JSON-LD
            def get_ld_value(key: str):
                value = entity.get(key)
                if value is None:
                    return None
                if isinstance(value, dict):
                    return value.get("@value") or value.get("@id")
                if isinstance(value, list):
                    return [
                        v.get("@value") if isinstance(v, dict) else v for v in value
                    ]
                return value

            # Extract name (can be string or array with @value objects)
            # Check both prefixed and full URI formats
            name_val = entity.get("http://schema.org/name") or entity.get("schema:name")
            if name_val:
                if isinstance(name_val, list):
                    # Get first name from array
                    if name_val and isinstance(name_val[0], dict):
                        name_str = name_val[0].get("@value")
                        if name_str:
                            gimie_dict["name"] = name_str
                    elif name_val:
                        gimie_dict["name"] = name_val[0]
                elif isinstance(name_val, dict):
                    name_str = name_val.get("@value")
                    if name_str:
                        gimie_dict["name"] = name_str
                else:
                    gimie_dict["name"] = name_val

            # Extract codeRepository
            # Check both prefixed and full URI formats
            code_repo = entity.get("http://schema.org/codeRepository") or entity.get(
                "schema:codeRepository",
            )
            if code_repo:
                if isinstance(code_repo, list):
                    gimie_dict["codeRepository"] = [
                        r.get("@id") if isinstance(r, dict) else r for r in code_repo
                    ]
                elif isinstance(code_repo, dict):
                    gimie_dict["codeRepository"] = [code_repo.get("@id", code_repo)]
                else:
                    gimie_dict["codeRepository"] = [code_repo]

            # Extract license (can be array with @id objects)
            # Check both prefixed and full URI formats
            license_val = entity.get("http://schema.org/license") or entity.get(
                "schema:license",
            )
            if license_val:
                if isinstance(license_val, list):
                    # Get first license from array
                    if license_val and isinstance(license_val[0], dict):
                        license_str = license_val[0].get("@id")
                        if license_str:
                            gimie_dict["license"] = license_str
                elif isinstance(license_val, dict):
                    license_str = license_val.get("@id")
                    if license_str:
                        gimie_dict["license"] = license_str
                else:
                    gimie_dict["license"] = license_val

            # Extract dateCreated (can be array with @value objects)
            # Check both prefixed and full URI formats
            date_created = entity.get("http://schema.org/dateCreated") or entity.get(
                "schema:dateCreated",
            )
            if date_created:
                if isinstance(date_created, list):
                    # Get first date from array
                    if date_created and isinstance(date_created[0], dict):
                        date_str = date_created[0].get("@value")
                        if date_str:
                            gimie_dict["dateCreated"] = date_str
                elif isinstance(date_created, dict):
                    date_str = date_created.get("@value")
                    if date_str:
                        gimie_dict["dateCreated"] = date_str
                else:
                    gimie_dict["dateCreated"] = date_created

            # Extract datePublished (can be array with @value objects)
            # Check both prefixed and full URI formats
            date_pub = entity.get("http://schema.org/datePublished") or entity.get(
                "schema:datePublished",
            )
            if date_pub:
                if isinstance(date_pub, list):
                    # Get first date from array
                    if date_pub and isinstance(date_pub[0], dict):
                        date_str = date_pub[0].get("@value")
                        if date_str:
                            gimie_dict["datePublished"] = date_str
                elif isinstance(date_pub, dict):
                    date_str = date_pub.get("@value")
                    if date_str:
                        gimie_dict["datePublished"] = date_str
                else:
                    gimie_dict["datePublished"] = date_pub

            # Extract dateModified (can be array with @value objects)
            # Check both prefixed and full URI formats
            date_modified = entity.get("http://schema.org/dateModified") or entity.get(
                "schema:dateModified",
            )
            if date_modified:
                if isinstance(date_modified, list):
                    # Get first date from array
                    if date_modified and isinstance(date_modified[0], dict):
                        date_str = date_modified[0].get("@value")
                        if date_str:
                            gimie_dict["dateModified"] = date_str
                elif isinstance(date_modified, dict):
                    date_str = date_modified.get("@value")
                    if date_str:
                        gimie_dict["dateModified"] = date_str
                else:
                    gimie_dict["dateModified"] = date_modified

            # Extract url
            # Check both prefixed and full URI formats
            url_val = entity.get("http://schema.org/url") or entity.get("schema:url")
            if url_val:
                url_str = url_val.get("@id") if isinstance(url_val, dict) else url_val
                if url_str:
                    gimie_dict["url"] = url_str

            # Extract programmingLanguage
            # Check both prefixed and full URI formats
            prog_lang = entity.get(
                "http://schema.org/programmingLanguage",
            ) or entity.get(
                "schema:programmingLanguage",
            )
            if prog_lang:
                if isinstance(prog_lang, list):
                    gimie_dict["programmingLanguage"] = [
                        lang.get("@value") if isinstance(lang, dict) else lang
                        for lang in prog_lang
                    ]
                else:
                    lang_val = (
                        prog_lang.get("@value")
                        if isinstance(prog_lang, dict)
                        else prog_lang
                    )
                    if lang_val:
                        gimie_dict["programmingLanguage"] = [lang_val]

            # Extract keywords (can be array with @value objects)
            # Check both prefixed and full URI formats
            keywords = entity.get("http://schema.org/keywords") or entity.get(
                "schema:keywords",
            )
            if keywords:
                if isinstance(keywords, list):
                    # Extract @value from each keyword object
                    keyword_list = []
                    for kw in keywords:
                        if isinstance(kw, dict):
                            kw_val = kw.get("@value")
                            if kw_val:
                                keyword_list.append(kw_val)
                        else:
                            keyword_list.append(kw)
                    if keyword_list:
                        gimie_dict["keywords"] = keyword_list
                else:
                    kw_val = (
                        keywords.get("@value")
                        if isinstance(keywords, dict)
                        else keywords
                    )
                    if kw_val:
                        gimie_dict["keywords"] = (
                            [kw_val] if isinstance(kw_val, str) else kw_val
                        )

            # Extract readme
            # Check both prefixed and full URI formats
            readme_val = entity.get("https://w3id.org/okn/o/sd#readme") or entity.get(
                "sd:readme",
            )
            if readme_val:
                readme_str = (
                    readme_val.get("@id")
                    if isinstance(readme_val, dict)
                    else readme_val
                )
                if readme_str:
                    gimie_dict["readme"] = readme_str

            # Extract citation
            # Check both prefixed and full URI formats
            citation = entity.get("http://schema.org/citation") or entity.get(
                "schema:citation",
            )
            if citation:
                if isinstance(citation, list):
                    gimie_dict["citation"] = [
                        cit.get("@id") if isinstance(cit, dict) else cit
                        for cit in citation
                    ]
                else:
                    cit_val = (
                        citation.get("@id") if isinstance(citation, dict) else citation
                    )
                    if cit_val:
                        gimie_dict["citation"] = [cit_val]

            # Only process the first SoftwareSourceCode entity
            break

        return gimie_dict

    def _extract_gimie_authors_and_organizations(self) -> dict:
        """
        Extract authors (Person) and organizations from GIMIE JSON-LD data.
        Resolves affiliations and maintains @id references.

        Returns:
            Dictionary with 'authors' and 'organizations' lists in structured format
        """
        result = {
            "authors": [],
            "organizations": [],
        }

        if not self.gimie:
            return result

        # Get graph (handle different formats)
        graph = None
        if isinstance(self.gimie, list):
            graph = self.gimie
        elif isinstance(self.gimie, dict):
            if "@graph" in self.gimie:
                graph = self.gimie.get("@graph", [])
            else:
                graph = [self.gimie]

        if not graph:
            return result

        # Build entity lookup by @id for affiliation resolution
        entity_lookup = {}
        for entity in graph:
            if isinstance(entity, dict) and "@id" in entity:
                entity_lookup[entity["@id"]] = entity

        # Helper to extract value from JSON-LD field
        def extract_value(field_value):
            """Extract actual value from JSON-LD field (handles @value, @id, arrays)"""
            if field_value is None:
                return None
            if isinstance(field_value, list):
                if not field_value:
                    return None
                # Get first value
                first = field_value[0]
                if isinstance(first, dict):
                    return first.get("@value") or first.get("@id")
                return first
            if isinstance(field_value, dict):
                return field_value.get("@value") or field_value.get("@id")
            return field_value

        # Helper to extract list of values
        def extract_list(field_value):
            """Extract list of values from JSON-LD field"""
            if field_value is None:
                return []
            if isinstance(field_value, list):
                result_list = []
                for item in field_value:
                    if isinstance(item, dict):
                        value = item.get("@value") or item.get("@id")
                        if value:
                            result_list.append(value)
                    else:
                        result_list.append(item)
                return result_list
            # Single value
            if isinstance(field_value, dict):
                value = field_value.get("@value") or field_value.get("@id")
                return [value] if value else []
            return [field_value]

        # Extract organizations first (needed for affiliation resolution)
        organizations_by_id = {}
        for entity in graph:
            if not isinstance(entity, dict):
                continue

            entity_types = entity.get("@type", [])
            if not isinstance(entity_types, list):
                entity_types = [entity_types]

            if "http://schema.org/Organization" not in entity_types:
                continue

            entity_id = entity.get("@id")
            if not entity_id:
                continue

            org_data = {
                "id": entity_id,
                "legalName": extract_value(
                    entity.get("http://schema.org/legalName")
                    or entity.get("schema:legalName"),
                ),
                "name": extract_value(
                    entity.get("http://schema.org/name") or entity.get("schema:name"),
                ),
                "description": extract_value(
                    entity.get("http://schema.org/description")
                    or entity.get("schema:description"),
                ),
            }

            # Extract logo if available
            logo = extract_value(
                entity.get("http://schema.org/logo") or entity.get("schema:logo"),
            )
            if logo:
                org_data["logo"] = logo

            organizations_by_id[entity_id] = org_data
            result["organizations"].append(org_data)

        # Extract authors (Person entities)
        for entity in graph:
            if not isinstance(entity, dict):
                continue

            entity_types = entity.get("@type", [])
            if not isinstance(entity_types, list):
                entity_types = [entity_types]

            if "http://schema.org/Person" not in entity_types:
                continue

            entity_id = entity.get("@id")
            if not entity_id:
                continue

            # Extract basic person fields
            person_data = {
                "id": entity_id,
                "name": extract_value(
                    entity.get("http://schema.org/name") or entity.get("schema:name"),
                ),
            }

            # Extract identifier (GitHub username, etc.)
            identifier = extract_value(
                entity.get("http://schema.org/identifier")
                or entity.get("schema:identifier"),
            )
            if identifier:
                person_data["identifier"] = identifier

            # Extract ORCID
            orcid = extract_value(
                entity.get("http://w3id.org/nfdi4ing/metadata4ing#orcidId")
                or entity.get("md4i:orcidId"),
            )
            if orcid:
                person_data["orcid"] = orcid

            # Extract affiliations and resolve them
            affiliations_raw = entity.get(
                "http://schema.org/affiliation",
            ) or entity.get(
                "schema:affiliation",
            )
            affiliations = []
            if affiliations_raw:
                affiliation_list = extract_list(affiliations_raw)
                for aff in affiliation_list:
                    if isinstance(aff, str):
                        org_name = None
                        org_id = None

                        # Could be an @id reference or a string value
                        if aff.startswith("http://") or aff.startswith("https://"):
                            # It's an @id reference - resolve to organization
                            if aff in organizations_by_id:
                                org_data = organizations_by_id[aff]
                                # Extract name from organization data
                                org_name = (
                                    org_data.get("legalName")
                                    or org_data.get("name")
                                    or aff
                                )
                                org_id = aff  # Store the URL as ID
                            else:
                                # Reference not found, use as string
                                org_name = aff
                                org_id = aff
                        else:
                            # String value (organization name)
                            org_name = aff

                        affiliations.append(
                            Affiliation(
                                name=org_name,
                                organizationId=org_id,
                                source="gimie",
                            ),
                        )

            if affiliations:
                person_data["affiliations"] = affiliations

            result["authors"].append(person_data)

        logger.info(
            f"Extracted {len(result['authors'])} authors and {len(result['organizations'])} organizations from GIMIE",
        )
        return result

    def _convert_simplified_to_full(
        self,
        simplified_dict: dict,
        union_metadata: Optional[dict] = None,
        git_authors: Optional[list] = None,
    ) -> dict:
        """
        Convert simplified output dict to full SoftwareSourceCode format.
        Merges model output with GIMIE/git extracted data.

        Args:
            simplified_dict: Simplified output from structured output agent
            union_metadata: Metadata about Union fields that were split (for reconciliation)
            git_authors: Optional list of GitAuthor objects extracted from the repository

        Returns:
            Dictionary in full SoftwareSourceCode format with merged GIMIE/git data
        """
        from datetime import date

        from pydantic import BaseModel, HttpUrl

        from ..data_models.models import RepositoryType
        from ..data_models.repository import GitAuthor

        if union_metadata is None:
            union_metadata = {}

        # Start with model output
        full_dict = simplified_dict.copy()

        # Extract GIMIE fields
        gimie_dict = self._extract_gimie_fields()
        logger.debug(
            f"Extracted {len(gimie_dict)} fields from GIMIE: {list(gimie_dict.keys())}",
        )

        # Extract GIMIE authors and organizations for merging
        gimie_authors_orgs = self._extract_gimie_authors_and_organizations()

        # id - prioritize GIMIE @id, then repository full_path, then model
        if "id" in gimie_dict and gimie_dict.get("id"):
            full_dict["id"] = gimie_dict["id"]
            logger.info(f"Using id from GIMIE: {gimie_dict['id']}")
        elif self.full_path:
            full_dict["id"] = self.full_path
            logger.info(f"Using repository full_path as id: {self.full_path}")
        elif "id" in simplified_dict and simplified_dict.get("id"):
            full_dict["id"] = simplified_dict["id"]
            logger.debug(f"Using id from model: {simplified_dict['id']}")
        else:
            # Fallback: use empty string (default)
            full_dict["id"] = ""

        # Handle Union fields first
        for original_field, union_info_list in union_metadata.items():
            reconciled_values = []
            is_list = False  # Default to not being a list
            for union_info in union_info_list:
                is_list = union_info.get("is_list", False)
                for new_field_name, field_data in union_info["fields"].items():
                    if (
                        new_field_name in simplified_dict
                        and simplified_dict[new_field_name] is not None
                    ):
                        values = simplified_dict[new_field_name]

                        # Ensure values is a list for consistent processing
                        if not isinstance(values, list):
                            values = [values]

                        for value in values:
                            target_type = field_data["type"]
                            if isinstance(target_type, type) and issubclass(
                                target_type,
                                BaseModel,
                            ):
                                if isinstance(value, dict):
                                    reconciled_values.append(target_type(**value))
                                else:
                                    reconciled_values.append(
                                        value,
                                    )  # Already a model instance
                            else:
                                reconciled_values.append(value)

                        # Remove the split field from the dictionary
                        if new_field_name in full_dict:
                            del full_dict[new_field_name]

            if reconciled_values:
                if is_list:
                    full_dict[original_field] = reconciled_values
                else:
                    full_dict[original_field] = (
                        reconciled_values[0] if reconciled_values else None
                    )
            else:
                full_dict[original_field] = [] if is_list else None

        # Merge GIMIE authors with model authors (after Union reconciliation)
        # Convert GIMIE authors to Person objects and merge with model output
        if gimie_authors_orgs.get("authors"):
            from ..data_models.models import Person

            gimie_authors = []
            for gimie_author in gimie_authors_orgs["authors"]:
                # Convert GIMIE author dict to Person object
                person_data = {
                    "type": "Person",
                    "id": gimie_author.get("id", ""),
                    "name": gimie_author.get("name", ""),
                }

                # Add ORCID if available
                if gimie_author.get("orcid"):
                    person_data["orcid"] = gimie_author["orcid"]

                # Add identifier if available
                if gimie_author.get("identifier"):
                    # Store identifier in a way that can be used later
                    # For now, we'll add it to affiliations or keep it separate
                    pass

                # Convert affiliations - handle Affiliation objects, organization objects, and strings
                affiliations = []
                if gimie_author.get("affiliations"):
                    for aff in gimie_author["affiliations"]:
                        if isinstance(aff, dict):
                            # Could be Affiliation dict or Organization dict
                            if "name" in aff and "source" in aff:
                                # Affiliation object - validate name is a string
                                aff_name = aff.get("name")
                                if isinstance(aff_name, str):
                                    affiliations.append(aff)
                                else:
                                    # Name is not a string, try to extract it
                                    logger.warning(
                                        f"Affiliation name is not a string: {type(aff_name)}",
                                    )
                                    if isinstance(aff_name, dict):
                                        aff_name = aff_name.get(
                                            "legalName",
                                        ) or aff_name.get("name")
                                    if isinstance(aff_name, str):
                                        affiliations.append(
                                            Affiliation(
                                                name=aff_name,
                                                organizationId=aff.get(
                                                    "organizationId",
                                                ),
                                                source=aff.get("source", "gimie"),
                                            ),
                                        )
                            elif "legalName" in aff or "name" in aff:
                                # Organization object - convert to Affiliation
                                org_name = aff.get("legalName") or aff.get("name")
                                # Ensure org_name is a string
                                if isinstance(org_name, dict):
                                    org_name = org_name.get(
                                        "legalName",
                                    ) or org_name.get("name")
                                if org_name and isinstance(org_name, str):
                                    affiliations.append(
                                        Affiliation(
                                            name=org_name,
                                            organizationId=aff.get("id"),
                                            source="gimie",
                                        ),
                                    )
                        elif isinstance(aff, str):
                            # String affiliation - convert to Affiliation
                            affiliations.append(
                                Affiliation(
                                    name=aff,
                                    organizationId=None,
                                    source="gimie",
                                ),
                            )

                if affiliations:
                    person_data["affiliations"] = affiliations

                try:
                    gimie_authors.append(Person(**person_data))
                except Exception as e:
                    logger.warning(
                        f"Failed to create Person from GIMIE author {gimie_author.get('name')}: {e}",
                    )

            # Get existing authors from model output
            existing_authors = full_dict.get("author", [])
            if not isinstance(existing_authors, list):
                existing_authors = []

            # Convert existing authors to Person objects if they're dicts
            existing_person_objects = []
            for author in existing_authors:
                if isinstance(author, dict):
                    try:
                        existing_person_objects.append(Person(**author))
                    except Exception as e:
                        logger.warning(f"Failed to convert author dict to Person: {e}")
                        # Keep as dict if conversion fails
                        existing_person_objects.append(author)
                elif isinstance(author, Person):
                    existing_person_objects.append(author)
                else:
                    existing_person_objects.append(author)

            # Build lookup of existing authors by name (normalized), id, and ORCID
            existing_by_name = {}
            existing_by_id = {}
            existing_by_orcid = {}
            for idx, author in enumerate(existing_person_objects):
                if isinstance(author, Person):
                    name = author.name.lower() if author.name else None
                    author_id = author.id if author.id else None
                    orcid = author.orcid if author.orcid else None

                    if name:
                        existing_by_name[name] = idx
                    if author_id:
                        existing_by_id[author_id] = idx
                    if orcid:
                        existing_by_orcid[orcid] = idx
                elif isinstance(author, dict):
                    name = (
                        author.get("name", "").lower() if author.get("name") else None
                    )
                    author_id = author.get("id")
                    orcid = author.get("orcid")

                    if name:
                        existing_by_name[name] = idx
                    if author_id:
                        existing_by_id[author_id] = idx
                    if orcid:
                        existing_by_orcid[orcid] = idx

            # Merge: update existing authors with GIMIE data, add new ones
            merged_authors = list(existing_person_objects)
            gimie_processed = set()

            for gimie_author in gimie_authors:
                author_name = gimie_author.name.lower() if gimie_author.name else None
                author_id = gimie_author.id if gimie_author.id else None
                author_orcid = gimie_author.orcid if gimie_author.orcid else None

                # Try to find matching existing author by ORCID (most reliable), then ID, then name
                matched_idx = None
                if author_orcid and author_orcid in existing_by_orcid:
                    matched_idx = existing_by_orcid[author_orcid]
                elif author_id and author_id in existing_by_id:
                    matched_idx = existing_by_id[author_id]
                elif author_name and author_name in existing_by_name:
                    matched_idx = existing_by_name[author_name]

                if matched_idx is not None:
                    # Update existing author with GIMIE data (especially ID)
                    existing_author = merged_authors[matched_idx]
                    if isinstance(existing_author, Person):
                        # Create updated Person object (Pydantic V2 is immutable)
                        updated_data = existing_author.model_dump()
                        updated = False

                        # Update ID if missing
                        if not updated_data.get("id") and gimie_author.id:
                            updated_data["id"] = gimie_author.id
                            updated = True
                            logger.info(
                                f"Updated author {existing_author.name} with GIMIE ID: {gimie_author.id}",
                            )

                        # Update ORCID if missing
                        if not updated_data.get("orcid") and gimie_author.orcid:
                            updated_data["orcid"] = gimie_author.orcid
                            updated = True

                        # Merge affiliations
                        if gimie_author.affiliations:
                            existing_affs = updated_data.get("affiliations", [])
                            existing_names = {
                                aff.name.lower(): aff
                                for aff in existing_affs
                                if isinstance(aff, Affiliation)
                            }
                            for aff in gimie_author.affiliations:
                                if (
                                    isinstance(aff, Affiliation)
                                    and aff.name.lower() not in existing_names
                                ):
                                    updated_data.setdefault("affiliations", []).append(
                                        aff,
                                    )
                                    updated = True

                        if updated:
                            try:
                                merged_authors[matched_idx] = Person(**updated_data)
                            except Exception as e:
                                logger.warning(f"Failed to update Person object: {e}")
                    elif isinstance(existing_author, dict):
                        # Update dict
                        updated = False
                        if not existing_author.get("id") and gimie_author.id:
                            existing_author["id"] = gimie_author.id
                            updated = True
                            logger.info(
                                f"Updated author {existing_author.get('name')} with GIMIE ID: {gimie_author.id}",
                            )
                        if not existing_author.get("orcid") and gimie_author.orcid:
                            existing_author["orcid"] = gimie_author.orcid
                            updated = True
                        # Merge affiliations
                        if gimie_author.affiliations:
                            existing_affs = existing_author.get("affiliations", [])
                            existing_names = {
                                aff.name.lower()
                                if isinstance(aff, Affiliation)
                                else str(aff).lower()
                                for aff in existing_affs
                            }
                            for aff in gimie_author.affiliations:
                                aff_name = (
                                    aff.name.lower()
                                    if isinstance(aff, Affiliation)
                                    else str(aff).lower()
                                )
                                if aff_name not in existing_names:
                                    existing_author.setdefault(
                                        "affiliations",
                                        [],
                                    ).append(aff)
                                    updated = True
                    gimie_processed.add(gimie_author.id or gimie_author.name)
                else:
                    # New author from GIMIE - add it
                    merged_authors.append(gimie_author)
                    logger.info(
                        f"Added new GIMIE author: {gimie_author.name} (id: {gimie_author.id})",
                    )
                    gimie_processed.add(gimie_author.id or gimie_author.name)

            # Convert all merged authors to Person objects for consistency
            final_authors = []
            for author in merged_authors:
                if isinstance(author, Person):
                    final_authors.append(author)
                elif isinstance(author, dict):
                    try:
                        final_authors.append(Person(**author))
                    except Exception as e:
                        logger.warning(
                            f"Failed to convert merged author dict to Person: {e}",
                        )
                        # Keep as dict if conversion fails
                        final_authors.append(author)
                else:
                    final_authors.append(author)

            if final_authors:
                full_dict["author"] = final_authors
                logger.info(
                    f"Merged {len(gimie_authors)} GIMIE authors with {len(existing_person_objects)} model authors, total: {len(final_authors)}",
                )

        # name - prioritize GIMIE, then model
        if "name" in gimie_dict and gimie_dict.get("name"):
            full_dict["name"] = gimie_dict["name"]
            logger.info(f"Using name from GIMIE: {gimie_dict['name']}")
        elif "name" in simplified_dict and simplified_dict.get("name"):
            full_dict["name"] = simplified_dict["name"]

        # applicationCategory
        if "applicationCategory" in simplified_dict:
            full_dict["applicationCategory"] = simplified_dict["applicationCategory"]

        # codeRepository - merge from model and GIMIE, convert strings to HttpUrl
        # Prioritize GIMIE, then merge with model
        code_repos = []
        if "codeRepository" in gimie_dict and gimie_dict.get("codeRepository"):
            # Start with GIMIE repositories
            code_repos.extend(gimie_dict["codeRepository"])
            logger.info(
                f"Using codeRepository from GIMIE: {gimie_dict['codeRepository']}",
            )

        if "codeRepository" in simplified_dict and simplified_dict.get(
            "codeRepository",
        ):
            # Merge model codeRepository, avoiding duplicates
            for model_repo in simplified_dict["codeRepository"]:
                if model_repo not in code_repos:
                    code_repos.append(model_repo)
                    logger.debug(f"Adding codeRepository from model: {model_repo}")

        if code_repos:
            try:
                full_dict["codeRepository"] = [
                    HttpUrl(url) if not isinstance(url, HttpUrl) else url
                    for url in code_repos
                ]
            except Exception as e:
                logger.warning(f"Failed to convert codeRepository URLs: {e}")
                full_dict["codeRepository"] = []
        else:
            # No codeRepository from either source
            full_dict["codeRepository"] = []

        # dateCreated - prioritize GIMIE, then git authors, then model
        # Model should NOT be asked for dateCreated (it comes from GIMIE/git)
        date_created = None

        # Priority 1: GIMIE (most reliable source)
        if "dateCreated" in gimie_dict and gimie_dict.get("dateCreated"):
            try:
                date_created = date.fromisoformat(gimie_dict["dateCreated"])
                logger.info(f"Using dateCreated from GIMIE: {date_created}")
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to parse GIMIE dateCreated: {e}")

        # Priority 2: Oldest commit date from git authors
        if date_created is None and git_authors:
            oldest_date = None
            for git_author in git_authors:
                if isinstance(git_author, GitAuthor) and git_author.commits:
                    if git_author.commits.firstCommitDate:
                        if (
                            oldest_date is None
                            or git_author.commits.firstCommitDate < oldest_date
                        ):
                            oldest_date = git_author.commits.firstCommitDate
                elif isinstance(git_author, dict):
                    commits = git_author.get("commits")
                    if commits:
                        first_date = None
                        if isinstance(commits, dict):
                            first_date = commits.get("firstCommitDate")
                        elif hasattr(commits, "firstCommitDate"):
                            first_date = commits.firstCommitDate

                        if first_date:
                            if isinstance(first_date, str):
                                try:
                                    first_date = date.fromisoformat(first_date)
                                except (ValueError, TypeError):
                                    continue
                            if oldest_date is None or first_date < oldest_date:
                                oldest_date = first_date

            if oldest_date:
                date_created = oldest_date
                logger.info(f"Using oldest commit date as dateCreated: {oldest_date}")

        # Priority 3: Model output (fallback only - model shouldn't be asked for this)
        if (
            date_created is None
            and "dateCreated" in simplified_dict
            and simplified_dict.get("dateCreated")
        ):
            try:
                date_created = date.fromisoformat(simplified_dict["dateCreated"])
                logger.info(f"Using dateCreated from model (fallback): {date_created}")
            except (ValueError, TypeError):
                logger.warning(
                    f"Failed to parse model dateCreated: {simplified_dict['dateCreated']}",
                )

        if date_created:
            full_dict["dateCreated"] = date_created

        # license - merge from model and GIMIE (prefer model if both exist)
        if "license" in simplified_dict and simplified_dict.get("license"):
            full_dict["license"] = simplified_dict["license"]
        elif "license" in gimie_dict and gimie_dict.get("license"):
            full_dict["license"] = gimie_dict["license"]

        # gitAuthors - use extracted git_authors if available, otherwise convert from simplified_dict
        if git_authors:
            # Use the extracted git authors directly (they're already GitAuthor objects)
            full_dict["gitAuthors"] = git_authors
            logger.info(
                f"Added {len(git_authors)} git authors from repository extraction",
            )
        elif "gitAuthors" in simplified_dict and simplified_dict.get("gitAuthors"):
            # Fallback: convert from simplified_dict if git_authors not provided
            converted_git_authors = []
            for git_auth in simplified_dict["gitAuthors"]:
                if not git_auth:
                    continue
                git_author_dict = {
                    "name": git_auth.get("name", ""),
                }
                if git_auth.get("email"):
                    git_author_dict["email"] = git_auth["email"]
                if git_auth.get("commits"):
                    commits_dict = git_auth["commits"]
                    commits_obj = {
                        "total": commits_dict.get(
                            "count",
                            commits_dict.get("total", 0),
                        ),
                    }
                    # Handle firstCommit/firstCommitDate (simplified model uses firstCommit as string)
                    first_commit = commits_dict.get("firstCommit") or commits_dict.get(
                        "firstCommitDate",
                    )
                    if first_commit:
                        if isinstance(first_commit, str):
                            try:
                                commits_obj["firstCommitDate"] = date.fromisoformat(
                                    first_commit,
                                )
                            except (ValueError, TypeError):
                                logger.warning(
                                    f"Failed to parse firstCommit date: {first_commit}",
                                )
                        else:
                            commits_obj["firstCommitDate"] = first_commit
                    # Handle lastCommit/lastCommitDate
                    last_commit = commits_dict.get("lastCommit") or commits_dict.get(
                        "lastCommitDate",
                    )
                    if last_commit:
                        if isinstance(last_commit, str):
                            try:
                                commits_obj["lastCommitDate"] = date.fromisoformat(
                                    last_commit,
                                )
                            except (ValueError, TypeError):
                                logger.warning(
                                    f"Failed to parse lastCommit date: {last_commit}",
                                )
                        else:
                            commits_obj["lastCommitDate"] = last_commit
                    git_author_dict["commits"] = commits_obj
                converted_git_authors.append(git_author_dict)
            if converted_git_authors:
                full_dict["gitAuthors"] = converted_git_authors

        # discipline - convert strings to Discipline enum
        if "discipline" in simplified_dict and simplified_dict.get("discipline"):
            from ..data_models.models import Discipline

            disciplines = []
            for disc_str in simplified_dict["discipline"]:
                try:
                    # Try to match enum value
                    for disc in Discipline:
                        if disc.value.lower() == disc_str.lower():
                            disciplines.append(disc)
                            break
                    else:
                        # If no match, try to create from string
                        disciplines.append(Discipline(disc_str))
                except Exception:
                    logger.warning(f"Failed to convert discipline: {disc_str}")
            full_dict["discipline"] = disciplines if disciplines else None

        # disciplineJustification
        if "disciplineJustification" in simplified_dict:
            full_dict["disciplineJustification"] = simplified_dict[
                "disciplineJustification"
            ]

        # repositoryType - convert string to RepositoryType enum
        if "repositoryType" in simplified_dict and simplified_dict.get(
            "repositoryType",
        ):
            try:
                repo_type_str = simplified_dict["repositoryType"]
                for repo_type in RepositoryType:
                    if repo_type.value.lower() == repo_type_str.lower():
                        full_dict["repositoryType"] = repo_type
                        break
                else:
                    # Default to "other" if not found
                    full_dict["repositoryType"] = RepositoryType.OTHER
            except Exception as e:
                logger.warning(f"Failed to convert repositoryType: {e}")
                full_dict["repositoryType"] = RepositoryType.OTHER

        # repositoryTypeJustification
        if "repositoryTypeJustification" in simplified_dict:
            full_dict["repositoryTypeJustification"] = simplified_dict[
                "repositoryTypeJustification"
            ]
        else:
            full_dict["repositoryTypeJustification"] = []

        # keywords - merge from GIMIE and model (prioritize GIMIE)
        keywords_list = []
        if "keywords" in gimie_dict and gimie_dict.get("keywords"):
            gimie_keywords = gimie_dict["keywords"]
            if isinstance(gimie_keywords, list):
                keywords_list.extend(gimie_keywords)
                logger.info(f"Using keywords from GIMIE: {gimie_keywords}")
            else:
                keywords_list.append(gimie_keywords)
                logger.info(f"Using keyword from GIMIE: {gimie_keywords}")

        if "keywords" in simplified_dict and simplified_dict.get("keywords"):
            model_keywords = simplified_dict["keywords"]
            if isinstance(model_keywords, list):
                # Merge, avoiding duplicates
                for kw in model_keywords:
                    if kw not in keywords_list:
                        keywords_list.append(kw)
                        logger.debug(f"Adding keyword from model: {kw}")
            else:
                if model_keywords not in keywords_list:
                    keywords_list.append(model_keywords)
                    logger.debug(f"Adding keyword from model: {model_keywords}")

        if keywords_list:
            full_dict["keywords"] = keywords_list
            logger.info(f"Final merged keywords: {keywords_list}")
        else:
            full_dict["keywords"] = []

        # url - from GIMIE
        if "url" in gimie_dict and gimie_dict.get("url"):
            try:
                full_dict["url"] = (
                    HttpUrl(gimie_dict["url"])
                    if not isinstance(gimie_dict["url"], HttpUrl)
                    else gimie_dict["url"]
                )
            except Exception as e:
                logger.warning(f"Failed to convert GIMIE url: {e}")

        # datePublished - merge from model and GIMIE (prefer model if both exist)
        if "datePublished" in simplified_dict and simplified_dict.get("datePublished"):
            try:
                full_dict["datePublished"] = date.fromisoformat(
                    simplified_dict["datePublished"],
                )
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to parse model datePublished: {e}")
        elif "datePublished" in gimie_dict and gimie_dict.get("datePublished"):
            try:
                date_pub_str = gimie_dict["datePublished"]
                if isinstance(date_pub_str, str):
                    full_dict["datePublished"] = date.fromisoformat(date_pub_str)
                    logger.info(
                        f"Using datePublished from GIMIE: {full_dict['datePublished']}",
                    )
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to parse GIMIE datePublished: {e}")

        # dateModified - from GIMIE
        if "dateModified" in gimie_dict and gimie_dict.get("dateModified"):
            try:
                date_mod_str = gimie_dict["dateModified"]
                if isinstance(date_mod_str, str):
                    full_dict["dateModified"] = date.fromisoformat(date_mod_str)
                    logger.info(
                        f"Using dateModified from GIMIE: {full_dict['dateModified']}",
                    )
            except (ValueError, TypeError) as e:
                logger.warning(f"Failed to parse GIMIE dateModified: {e}")

        # programmingLanguage - merge from model and GIMIE
        prog_langs = []
        if "programmingLanguage" in simplified_dict and simplified_dict.get(
            "programmingLanguage",
        ):
            model_langs = simplified_dict["programmingLanguage"]
            if isinstance(model_langs, list):
                prog_langs.extend(model_langs)
            else:
                prog_langs.append(model_langs)

        if "programmingLanguage" in gimie_dict and gimie_dict.get(
            "programmingLanguage",
        ):
            gimie_langs = gimie_dict["programmingLanguage"]
            if isinstance(gimie_langs, list):
                for lang in gimie_langs:
                    if lang not in prog_langs:
                        prog_langs.append(lang)
            else:
                if gimie_langs not in prog_langs:
                    prog_langs.append(gimie_langs)

        if prog_langs:
            full_dict["programmingLanguage"] = prog_langs

        # readme - from GIMIE
        if "readme" in gimie_dict and gimie_dict.get("readme"):
            try:
                full_dict["readme"] = (
                    HttpUrl(gimie_dict["readme"])
                    if not isinstance(gimie_dict["readme"], HttpUrl)
                    else gimie_dict["readme"]
                )
            except Exception as e:
                logger.warning(f"Failed to convert GIMIE readme URL: {e}")

        # citation - merge from model and GIMIE
        citations = []
        if "citation" in simplified_dict and simplified_dict.get("citation"):
            model_citations = simplified_dict["citation"]
            if isinstance(model_citations, list):
                citations.extend(model_citations)
            else:
                citations.append(model_citations)

        if "citation" in gimie_dict and gimie_dict.get("citation"):
            gimie_citations = gimie_dict["citation"]
            if isinstance(gimie_citations, list):
                for cit in gimie_citations:
                    if cit not in citations:
                        citations.append(cit)
            else:
                if gimie_citations not in citations:
                    citations.append(gimie_citations)

        if citations:
            try:
                full_dict["citation"] = [
                    HttpUrl(cit) if not isinstance(cit, HttpUrl) else cit
                    for cit in citations
                ]
            except Exception as e:
                logger.warning(f"Failed to convert citation URLs: {e}")

        return full_dict

    def run_authors_enrichment(self):
        logger.info(f"ORCID enrichment for {self.full_path}")

        # Check if data exists before enrichment
        if self.data is None:
            logging.warning(
                f"Cannot enrich authors: no data available for {self.full_path}",
            )
            return

        llm_result = enrich_authors_with_orcid(self.data)

        if isinstance(llm_result, SoftwareSourceCode):
            self.data = llm_result
        else:
            logging.warning(f"Author enrichment failed for {self.full_path}")

    async def run_organization_enrichment(self):
        logger.info(f"Organization enrichment for {self.full_path}")

        # Check if data exists before enrichment
        if self.data is None:
            logging.warning(
                f"Cannot enrich organizations: no data available for {self.full_path}",
            )
            return

        try:
            result = await enrich_organizations_from_dict(
                self.data.model_dump(),
                self.full_path,
            )

            # Extract data and usage
            organization_enrichment = (
                result.get("data") if isinstance(result, dict) else result
            )
            usage = result.get("usage") if isinstance(result, dict) else None

            # Accumulate official API-reported usage data
            if usage:
                self.total_input_tokens += usage.get("input_tokens", 0)
                self.total_output_tokens += usage.get("output_tokens", 0)
                logger.info(
                    f"Organization enrichment usage: {usage.get('input_tokens', 0)} input, {usage.get('output_tokens', 0)} output tokens",
                )

            # Accumulate estimated tokens
            if usage and "estimated_input_tokens" in usage:
                self.estimated_input_tokens += usage.get("estimated_input_tokens", 0)
                self.estimated_output_tokens += usage.get("estimated_output_tokens", 0)

            # organization_enrichment is an OrganizationEnrichmentResult, not a dict
            enriched_orgs = (
                organization_enrichment.organizations
            )  # Direct attribute access

            # Replace relatedToOrganizations with enriched Organization objects only
            # Don't add both org name strings and Organization objects - just objects
            self.data.relatedToOrganizations = (
                list(enriched_orgs) if enriched_orgs else None
            )

            # These values are overwritten only if provided by the enrichment
            if organization_enrichment.relatedToEPFL is not None:
                self.data.relatedToEPFL = organization_enrichment.relatedToEPFL
            if organization_enrichment.relatedToEPFLJustification is not None:
                self.data.relatedToEPFLJustification = (
                    organization_enrichment.relatedToEPFLJustification
                )
            if organization_enrichment.relatedToEPFLConfidence is not None:
                self.data.relatedToEPFLConfidence = (
                    organization_enrichment.relatedToEPFLConfidence
                )
        except Exception as e:
            logger.error(f"Organization enrichment failed: {e}", exc_info=True)
            # Don't fail the entire analysis, just skip organization enrichment
            return

        # enriched_orgs = organization_enrichment.get("organizations", [])

        # # Safely handle relatedToOrganizations list
        # related_orgs = getattr(self.data, "relatedToOrganizations", None)
        # if related_orgs is None:
        #     related_orgs = []
        #     self.data.relatedToOrganizations = related_orgs
        # for org in enriched_orgs:
        #     legal_name = org.get("legalName")
        #     if legal_name:
        #         related_orgs.append(legal_name)

        # # Safely handle relatedToOrganizationsROR list
        # related_orgs_ror = getattr(self.data, "relatedToOrganizationsROR", None)
        # if related_orgs_ror is None:
        #     related_orgs_ror = []
        #     self.data.relatedToOrganizationsROR = related_orgs_ror
        # related_orgs_ror.extend(enriched_orgs)

        # # These values are overwritten only if provided by the enrichment
        # self.data.relatedToEPFL = organization_enrichment.get(
        #     "relatedToEPFL",
        #     getattr(self.data, "relatedToEPFL", None)
        # )

        # self.data.relatedToEPFLJustification = organization_enrichment.get(
        #     "relatedToEPFLJustification",
        #     getattr(self.data, "relatedToEPFLJustification", None)
        # )

    async def run_user_enrichment(self):
        logger.info(f"User enrichment for {self.full_path}")

        # Check if data exists before enrichment
        if self.data is None:
            logging.warning(
                f"Cannot enrich users: no data available for {self.full_path}",
            )
            return

        # Convert Pydantic models to dictionaries for the enrichment function
        git_authors_raw = getattr(self.data, "gitAuthors", [])
        git_authors_data = (
            [
                ga.model_dump() if hasattr(ga, "model_dump") else ga
                for ga in git_authors_raw
            ]
            if git_authors_raw
            else []
        )

        existing_authors_raw = getattr(self.data, "author", [])
        existing_authors_data = []
        if existing_authors_raw:
            for author in existing_authors_raw:
                # Convert to dict first if it's a Pydantic model
                author_dict = (
                    author.model_dump() if hasattr(author, "model_dump") else author
                )

                # Only include Person/EnrichedAuthor objects, skip Organization objects
                # Organizations have 'legalName', Person/EnrichedAuthor have 'name'
                if isinstance(author_dict, dict):
                    if "name" in author_dict:  # Person or EnrichedAuthor
                        existing_authors_data.append(author_dict)
                    elif "legalName" in author_dict:  # Organization - skip it
                        logger.debug(
                            f"Skipping Organization object in user enrichment: {author_dict.get('legalName')}",
                        )
                        continue
                else:
                    existing_authors_data.append(author_dict)

        result = await enrich_users_from_dict(
            git_authors_data=git_authors_data,
            existing_authors_data=existing_authors_data,
            repository_url=self.full_path,
        )
        # This method should validate and return a compatible object

        # Extract usage data
        usage = result.get("usage") if isinstance(result, dict) else None
        user_enrichment = (
            result if not isinstance(result, dict) or "usage" not in result else result
        )

        # Accumulate official API-reported usage data
        if usage:
            self.total_input_tokens += usage.get("input_tokens", 0)
            self.total_output_tokens += usage.get("output_tokens", 0)
            logger.info(
                f"User enrichment usage: {usage.get('input_tokens', 0)} input, {usage.get('output_tokens', 0)} output tokens",
            )

        # Accumulate estimated tokens
        if usage and "estimated_input_tokens" in usage:
            self.estimated_input_tokens += usage.get("estimated_input_tokens", 0)
            self.estimated_output_tokens += usage.get("estimated_output_tokens", 0)

    def _names_match(self, name1: str, name2: str) -> bool:
        """
        Check if two names match, handling variations like:
        - "Mackenzie Mathis" vs "Mackenzie Weygandt Mathis"
        - "Alexander Mathis" vs "Mathis, Alexander" (last, first format)
        - Different punctuation and formatting

        Returns True if the names likely refer to the same person.
        """
        if not name1 or not name2:
            return False

        import re

        # Normalize: lowercase, remove punctuation, split into words
        def normalize_name(name):
            # Remove punctuation and extra whitespace
            cleaned = re.sub(r"[^\w\s]", " ", name.lower())
            # Split and filter empty strings
            return set(word for word in cleaned.split() if word)

        n1_parts = normalize_name(name1)
        n2_parts = normalize_name(name2)

        # If all parts of the shorter name are in the longer name, it's a match
        # e.g., {"mackenzie", "mathis"} ⊆ {"mackenzie", "weygandt", "mathis"}
        # Also matches {"alexander", "mathis"} with {"mathis", "alexander"}
        if len(n1_parts) <= len(n2_parts):
            return n1_parts.issubset(n2_parts)
        else:
            return n2_parts.issubset(n1_parts)

    async def run_linked_entities_enrichment(self):
        """Enrich repository with linked entities relations (Infoscience, etc.)"""
        logger.info(f"linked entities enrichment for {self.full_path}")

        # Check if data exists before enrichment
        if self.data is None:
            logger.warning(
                f"Cannot enrich linked entities: no data available for {self.full_path}",
            )
            return

        try:
            # Extract repository information for the enrichment
            repository_name = self.data.name or self.full_path.split("/")[-1]
            description = self.data.description or ""

            # Get README excerpt (first 1000 chars from readme content if available)
            readme_excerpt = ""

            # Try to get some text from description or other fields
            if self.data.description:
                readme_excerpt = self.data.description[:1000]

            # Extract author names and organization names from existing data
            author_names = []
            organization_names = []

            if hasattr(self.data, "author") and self.data.author:
                for author in self.data.author:
                    if hasattr(author, "name") and author.name:
                        author_names.append(author.name)
                    elif hasattr(author, "legalName") and author.legalName:
                        organization_names.append(author.legalName)

            # Also check relatedToOrganizations for Organization objects
            if self.data.relatedToOrganizations:
                for org in self.data.relatedToOrganizations:
                    if (
                        isinstance(org, Organization)
                        and hasattr(org, "legalName")
                        and org.legalName
                    ):
                        if org.legalName not in organization_names:
                            organization_names.append(org.legalName)

            result = await enrich_repository_linked_entities(
                repository_url=self.full_path,
                repository_name=repository_name,
                description=description,
                readme_excerpt=readme_excerpt,
                authors=author_names,
                organizations=organization_names,
            )

            # Extract data and usage
            enrichment_data = result.get("data") if isinstance(result, dict) else result
            usage = result.get("usage") if isinstance(result, dict) else None

            # Accumulate token usage
            if usage:
                self.total_input_tokens += usage.get("input_tokens", 0)
                self.total_output_tokens += usage.get("output_tokens", 0)
                logger.info(
                    f"linked entities enrichment usage: {usage.get('input_tokens', 0)} input, "
                    f"{usage.get('output_tokens', 0)} output tokens",
                )

            if usage and "estimated_input_tokens" in usage:
                self.estimated_input_tokens += usage.get("estimated_input_tokens", 0)
                self.estimated_output_tokens += usage.get("estimated_output_tokens", 0)

            # Store the linked entities relations at repository level
            if enrichment_data:
                # Repository-level relations (publications about the repository itself)
                if hasattr(enrichment_data, "repository_relations"):
                    self.data.linkedEntities = enrichment_data.repository_relations
                    logger.info(
                        f"Stored {len(enrichment_data.repository_relations)} repository-level linked entities relations",
                    )
                # Fallback for backward compatibility
                elif hasattr(enrichment_data, "relations"):
                    self.data.linkedEntities = enrichment_data.relations
                    logger.info(
                        f"Stored {len(enrichment_data.relations)} linked entities relations at repository level",
                    )

                # Directly assign relations to authors and organizations using the structured output
                if hasattr(self.data, "author") and self.data.author:
                    # For Person objects - match by author name
                    if hasattr(enrichment_data, "author_relations"):
                        for author in self.data.author:
                            if hasattr(author, "name") and author.name:
                                # Direct lookup using the exact author name as key
                                if author.name in enrichment_data.author_relations:
                                    author_rels = enrichment_data.author_relations[
                                        author.name
                                    ]
                                    author.linkedEntities = author_rels
                                    logger.info(
                                        f"✓ Directly assigned {len(author_rels)} relations to author: {author.name}",
                                    )
                                else:
                                    # Author had no results
                                    author.linkedEntities = []
                                    logger.info(
                                        f"ℹ No relations found for author: {author.name}",
                                    )

                            # For Organization objects in the author list - match by legalName
                            elif hasattr(author, "legalName") and author.legalName:
                                if (
                                    hasattr(enrichment_data, "organization_relations")
                                    and author.legalName
                                    in enrichment_data.organization_relations
                                ):
                                    org_rels = enrichment_data.organization_relations[
                                        author.legalName
                                    ]
                                    author.linkedEntities = org_rels
                                    logger.info(
                                        f"✓ Directly assigned {len(org_rels)} relations to organization: {author.legalName}",
                                    )
                                else:
                                    author.linkedEntities = []
                                    logger.info(
                                        f"ℹ No relations found for organization: {author.legalName}",
                                    )

        except Exception as e:
            logger.error(f"linked entities enrichment failed: {e}", exc_info=True)
            # Don't fail the entire analysis, just skip linked entities enrichment
            return

    async def run_epfl_final_assessment(self):
        """Run final EPFL relationship assessment after all enrichments complete"""
        logger.info(f"Final EPFL assessment for {self.full_path}")

        # Check if data exists
        if self.data is None:
            logging.warning(
                f"Cannot run EPFL assessment: no data available for {self.full_path}",
            )
            return

        try:
            # Convert data to dict for assessment
            data_dict = self.data.model_dump()

            # Call the EPFL assessment agent
            result = await assess_epfl_relationship(
                data=data_dict,
                item_type="repository",
            )

            # Extract assessment and usage
            assessment = result.get("data") if isinstance(result, dict) else result
            usage = result.get("usage") if isinstance(result, dict) else None

            # Accumulate token usage
            if usage:
                self.total_input_tokens += usage.get("input_tokens", 0)
                self.total_output_tokens += usage.get("output_tokens", 0)
                logger.info(
                    f"EPFL assessment usage: {usage.get('input_tokens', 0)} input, {usage.get('output_tokens', 0)} output tokens",
                )

            # Accumulate estimated tokens
            if usage and "estimated_input_tokens" in usage:
                self.estimated_input_tokens += usage.get("estimated_input_tokens", 0)
                self.estimated_output_tokens += usage.get("estimated_output_tokens", 0)

            # Update data with final assessment (overwrite previous values)
            self.data.relatedToEPFL = assessment.relatedToEPFL
            self.data.relatedToEPFLConfidence = assessment.relatedToEPFLConfidence
            self.data.relatedToEPFLJustification = assessment.relatedToEPFLJustification

            logger.info(
                f"Final EPFL assessment: relatedToEPFL={assessment.relatedToEPFL}, "
                f"confidence={assessment.relatedToEPFLConfidence:.2f}",
            )
            logger.info(
                f"Justification: {assessment.relatedToEPFLJustification[:200]}...",
            )

        except Exception as e:
            logger.error(
                f"EPFL final assessment failed for {self.full_path}: {e}",
                exc_info=True,
            )
            # Don't fail the entire analysis, just log the error

    def run_validation(self) -> bool:
        if self.data is None:
            logging.warning("No data to validate")
            return False
        else:
            self.data = SoftwareSourceCode.model_validate(self.data)
            logging.info(f"Data validation passed for {self.full_path}")
            return True

    def check_in_cache(self, api_type: str, cache_params: dict) -> bool:
        result = self.cache_manager.load_from_cache(api_type, cache_params)

        if result is not None:
            logging.info(f"Found cached data for {self.full_path}")
            return True
        else:
            logging.info(f"No cached data for {self.full_path}")
            return False

    def save_in_cache(self):
        if self.data is not None:
            self.cache_manager.cache.set(
                api_type="repository",
                params={"full_path": self.full_path},
                response_data=self.data.model_dump_json(),
                ttl_days=365,  # Cache for 365 days
            )
            logging.info(f"Cached results for {self.full_path}")
        else:
            logging.warning(f"No data to cache for {self.full_path}")

    def load_from_cache(self, api_type: str, cache_params: dict):
        result = self.cache_manager.load_from_cache(api_type, cache_params)

        # Validate
        if isinstance(result, dict):
            result = SoftwareSourceCode.model_validate(result)
        elif isinstance(result, str):
            result = SoftwareSourceCode.model_validate_json(result)

        self.data = result

        logging.info(f"Loaded data from cache for {self.full_path}")

    def get_usage_stats(self) -> dict:
        """
        Get accumulated token usage statistics and timing from all agents.

        Returns:
            Dictionary with official API-reported tokens, estimated tokens, and timing info
        """
        # Calculate duration if we have start and end times
        duration = None
        if self.start_time and self.end_time:
            duration = (self.end_time - self.start_time).total_seconds()

        return {
            "input_tokens": self.total_input_tokens,
            "output_tokens": self.total_output_tokens,
            "total_tokens": self.total_input_tokens + self.total_output_tokens,
            "estimated_input_tokens": self.estimated_input_tokens,
            "estimated_output_tokens": self.estimated_output_tokens,
            "estimated_total_tokens": self.estimated_input_tokens
            + self.estimated_output_tokens,
            "duration": duration,
            "start_time": self.start_time,
            "end_time": self.end_time,
            "status_code": 200 if self.analysis_successful else 500,
        }

    def dump_results(self, output_type="json") -> str | dict | None:
        """
        Dump results in specified format: json, dict, or json-ld
        """
        if self.data is None:
            logging.warning("No data to dump")
            return None
        if output_type == "pydantic":
            return self.data
        elif output_type == "json":
            return self.data.model_dump_json(indent=2)
        elif output_type == "dict":
            return self.data.model_dump()
        elif output_type == "json-ld":
            return self.data.convert_pydantic_to_jsonld()
        else:
            logging.error(f"Unsupported output type: {output_type}")
            return None

    ################################################################
    # Analysis
    ################################################################

    async def run_analysis(
        self,
        run_gimie: bool = True,
        run_llm: bool = True,
        run_user_enrichment: bool = True,
        run_organization_enrichment: bool = True,
    ):
        """
        Run the full analysis pipeline with optional steps.
        Checks cache before running each step unless force_refresh is True.
        """
        # Check if repository is public
        if not self.is_public:
            logger.error(
                f"Cannot run analysis: repository {self.full_path} is not public",
            )
            self.analysis_successful = False
            return

        # Track start time
        self.start_time = datetime.now()

        # Check if complete repository analysis exists in cache
        cache_params = {"full_path": self.full_path}
        if not self.force_refresh and self.check_in_cache("repository", cache_params):
            self.load_from_cache("repository", cache_params)
            logging.info(f"Loaded complete analysis from cache for {self.full_path}")
            # Mark as successful since we loaded from cache
            self.analysis_successful = True
            self.end_time = datetime.now()
            return

        # Run GIMIE analysis
        if run_gimie:
            logging.info(f"GIMIE analysis for {self.full_path}")
            self.run_gimie_analysis()
            logging.info(f"GIMIE analysis completed for {self.full_path}")

        # Run LLM analysis
        if run_llm:
            logging.info(f"LLM analysis for {self.full_path}")
            await self.run_llm_analysis()

            # Only run author enrichment if LLM analysis succeeded
            # COMMENTED OUT FOR TESTING - ORCID enrichment uses external APIs
            # if self.data is not None:
            #     self.run_authors_enrichment()
            # else:
            #     logging.warning(
            #         f"Skipping author enrichment: LLM analysis failed for {self.full_path}",
            #     )

            logging.info(f"LLM analysis completed for {self.full_path}")

        # Run user enrichment
        if run_user_enrichment and self.data is not None:
            logging.info(f"User enrichment for {self.full_path}")
            await self.run_user_enrichment()
            logging.info(f"User enrichment completed for {self.full_path}")

        # Run organization enrichment
        if run_organization_enrichment and self.data is not None:
            logging.info(f"Organization enrichment for {self.full_path}")
            await self.run_organization_enrichment()
            logging.info(f"Organization enrichment completed for {self.full_path}")

        # Run academic catalog enrichment
        # COMMENTED OUT FOR TESTING - uses tools (Infoscience)
        # if self.data is not None:
        #     logging.info(f"Academic catalog enrichment for {self.full_path}")
        #     await self.run_linked_entities_enrichment()
        #     logging.info(f"Academic catalog enrichment completed for {self.full_path}")

        # Run final EPFL assessment after all enrichments complete
        # COMMENTED OUT FOR TESTING - EPFL assessment is already done in atomic pipeline (Stage 3)
        # if self.data is not None:
        #     logging.info(f"Final EPFL assessment for {self.full_path}")
        #     await self.run_epfl_final_assessment()
        #     logging.info(f"Final EPFL assessment completed for {self.full_path}")

        # Only validate and cache if we have data
        if self.data is not None:
            self.run_validation()
            self.save_in_cache()
            self.analysis_successful = True
        else:
            logging.error(f"Analysis failed for {self.full_path}: no data generated")
            self.analysis_successful = False

        # Track end time
        self.end_time = datetime.now()

        # Log duration
        if self.start_time and self.end_time:
            duration = (self.end_time - self.start_time).total_seconds()
            logging.info(f"Analysis completed in {duration:.2f} seconds")
