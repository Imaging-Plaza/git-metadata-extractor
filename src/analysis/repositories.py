import logging
from datetime import datetime
from typing import Optional

from ..agents.atomic_agents import (
    assess_final_epfl_relationship,
    classify_repository_type_and_discipline,
    compile_enriched_data_for_epfl,
    compile_repository_context,
    generate_structured_output,
    identify_related_organizations,
    search_academic_catalogs,
    structure_linked_entities,
)
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

        logger.info(
            f"Repository content prepared: {len(repository_content):,} chars, {len(git_authors)} git authors",
        )

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
            logger.info(
                f"GIMIE data prepared: {len(gimie_authors_orgs.get('authors', []))} authors, "
                f"{len(gimie_authors_orgs.get('organizations', []))} organizations, "
                f"{len(gimie_data):,} chars",
            )
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

            # Log Stage 1 token usage
            logger.info("=" * 80)
            logger.info("STAGE 1 (Context Compiler) Token Usage:")
            logger.info(
                f"  Input tokens:  {usage.get('input_tokens', 0):,} (official) | {usage.get('estimated_input_tokens', 0):,} (estimated)",
            )
            logger.info(
                f"  Output tokens: {usage.get('output_tokens', 0):,} (official) | {usage.get('estimated_output_tokens', 0):,} (estimated)",
            )
            logger.info(
                f"  Total tokens:  {usage.get('input_tokens', 0) + usage.get('output_tokens', 0):,}",
            )
            logger.info("=" * 80)

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

            # Log Stage 2 token usage
            logger.info("=" * 80)
            logger.info("STAGE 2 (Structured Output) Token Usage:")
            logger.info(
                f"  Input tokens:  {usage.get('input_tokens', 0):,} (official) | {usage.get('estimated_input_tokens', 0):,} (estimated)",
            )
            logger.info(
                f"  Output tokens: {usage.get('output_tokens', 0):,} (official) | {usage.get('estimated_output_tokens', 0):,} (estimated)",
            )
            logger.info(
                f"  Total tokens:  {usage.get('input_tokens', 0) + usage.get('output_tokens', 0):,}",
            )
            logger.info("=" * 80)

        # Stage 3: Classify repository type and discipline
        logger.info("Stage 3: Classifying repository type and discipline...")
        classification_result = await classify_repository_type_and_discipline(
            compiled_context=compiled_context,
        )

        classification = classification_result.get("data")
        usage = classification_result.get("usage")

        if not classification:
            logger.error("Repository classification failed")
            return

        # Accumulate usage from classification
        if usage:
            self.total_input_tokens += usage.get("input_tokens", 0)
            self.total_output_tokens += usage.get("output_tokens", 0)
            if "estimated_input_tokens" in usage:
                self.estimated_input_tokens += usage.get("estimated_input_tokens", 0)
                self.estimated_output_tokens += usage.get("estimated_output_tokens", 0)

            # Log Stage 3 token usage
            logger.info("=" * 80)
            logger.info("STAGE 3 (Repository Classifier) Token Usage:")
            logger.info(
                f"  Input tokens:  {usage.get('input_tokens', 0):,} (official) | {usage.get('estimated_input_tokens', 0):,} (estimated)",
            )
            logger.info(
                f"  Output tokens: {usage.get('output_tokens', 0):,} (official) | {usage.get('estimated_output_tokens', 0):,} (estimated)",
            )
            logger.info(
                f"  Total tokens:  {usage.get('input_tokens', 0) + usage.get('output_tokens', 0):,}",
            )
            logger.info("=" * 80)

        # Convert simplified output to SoftwareSourceCode
        # First convert to dict
        if hasattr(structured_output, "model_dump"):
            simplified_dict = structured_output.model_dump()
        else:
            simplified_dict = structured_output

        # Override with classification results (Stage 3 takes precedence)
        if hasattr(classification, "model_dump"):
            classification_dict = classification.model_dump()
        else:
            classification_dict = classification

        # Merge classification into simplified_dict (overrides Stage 2 values)
        simplified_dict["repositoryType"] = classification_dict.get("repositoryType")
        simplified_dict["repositoryTypeJustification"] = classification_dict.get(
            "repositoryTypeJustification",
            [],
        )
        simplified_dict["discipline"] = classification_dict.get("discipline", [])
        simplified_dict["disciplineJustification"] = classification_dict.get(
            "disciplineJustification",
            [],
        )

        logger.info(
            f"Repository classified as: {classification_dict.get('repositoryType')} with disciplines: {classification_dict.get('discipline', [])}",
        )

        # Stage 4: Identify related organizations
        logger.info("Stage 4: Identifying related organizations...")
        organization_result = await identify_related_organizations(
            compiled_context=compiled_context,
        )

        organization_data = organization_result.get("data")
        usage = organization_result.get("usage")

        if not organization_data:
            logger.error("Organization identification failed")
            return

        # Accumulate usage from organization identification
        if usage:
            self.total_input_tokens += usage.get("input_tokens", 0)
            self.total_output_tokens += usage.get("output_tokens", 0)
            if "estimated_input_tokens" in usage:
                self.estimated_input_tokens += usage.get("estimated_input_tokens", 0)
                self.estimated_output_tokens += usage.get("estimated_output_tokens", 0)

            # Log Stage 4 token usage
            logger.info("=" * 80)
            logger.info("STAGE 4 (Organization Identifier) Token Usage:")
            logger.info(
                f"  Input tokens:  {usage.get('input_tokens', 0):,} (official) | {usage.get('estimated_input_tokens', 0):,} (estimated)",
            )
            logger.info(
                f"  Output tokens: {usage.get('output_tokens', 0):,} (official) | {usage.get('estimated_output_tokens', 0):,} (estimated)",
            )
            logger.info(
                f"  Total tokens:  {usage.get('input_tokens', 0) + usage.get('output_tokens', 0):,}",
            )
            logger.info("=" * 80)

        # Convert organization data to dict
        if hasattr(organization_data, "model_dump"):
            organization_dict = organization_data.model_dump()
        else:
            organization_dict = organization_data

        # Merge organization data into simplified_dict (Stage 4 provides organizations)
        simplified_dict["relatedToOrganizations"] = organization_dict.get(
            "relatedToOrganizations",
            [],
        )
        simplified_dict["relatedToOrganizationJustification"] = organization_dict.get(
            "relatedToOrganizationJustification",
            [],
        )

        logger.info(
            f"Identified {len(organization_dict.get('relatedToOrganizations', []))} related organizations",
        )

        # Get union_metadata for reconciliation
        union_metadata = structured_result.get("union_metadata", {})

        # Convert simplified dict to full SoftwareSourceCode format
        full_dict = self._convert_simplified_to_full(
            simplified_dict,
            union_metadata,
            git_authors=git_authors,
        )

        # Note: EPFL assessment now runs AFTER all enrichments complete
        # (see run_epfl_final_assessment method called at end of run_analysis)

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

            # Extract GitHub ID from GitHub URL if present in entity_id
            if entity_id and "github.com/" in entity_id:
                # Extract username from URL like "https://github.com/username"
                try:
                    github_username = entity_id.rstrip("/").split("/")[-1]
                    if github_username and github_username != "github.com":
                        person_data["githubId"] = github_username
                except Exception as e:
                    logger.debug(f"Could not extract GitHub ID from {entity_id}: {e}")

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
                logger.debug(
                    f"GIMIE author {person_data.get('name')} has {len(affiliations)} affiliations",
                )

            result["authors"].append(person_data)

        logger.info(
            f"Extracted {len(result['authors'])} authors and {len(result['organizations'])} organizations from GIMIE",
        )
        # Log total affiliations extracted
        total_affs = sum(len(a.get("affiliations", [])) for a in result["authors"])
        logger.info(f"Total affiliations extracted from GIMIE: {total_affs}")
        return result

    def _deduplicate_authors(self):
        """
        Deduplicate authors with the same name, merging their fields.

        This merges Person objects that have the same name but different IDs,
        combining their affiliations, emails, and other metadata without duplicates.
        """
        if not self.data or not hasattr(self.data, "author") or not self.data.author:
            return

        from collections import defaultdict

        # Group authors by name (case-insensitive, normalize hyphens/dashes)
        authors_by_name = defaultdict(list)
        for author in self.data.author:
            if hasattr(author, "name") and author.name:
                # Normalize name for grouping
                # Replace various dash/hyphen characters with standard hyphen
                normalized_name = author.name.strip().lower()
                # Normalize various unicode hyphens/dashes to regular hyphen
                normalized_name = normalized_name.replace(
                    "‑",
                    "-",
                )  # non-breaking hyphen
                normalized_name = normalized_name.replace("–", "-")  # en dash
                normalized_name = normalized_name.replace("—", "-")  # em dash
                authors_by_name[normalized_name].append(author)

        # Merge duplicates
        merged_authors = []
        for normalized_name, author_list in authors_by_name.items():
            if len(author_list) == 1:
                # No duplicates
                merged_authors.append(author_list[0])
            else:
                # Multiple authors with same name - merge them
                logger.info(
                    f"Merging {len(author_list)} duplicate authors: {author_list[0].name}",
                )
                merged = self._merge_person_objects(author_list)
                merged_authors.append(merged)

        self.data.author = merged_authors
        # Log affiliation counts after deduplication
        total_affs_after_dedup = sum(
            len(a.affiliations) if hasattr(a, "affiliations") else 0
            for a in self.data.author
        )
        logger.info(
            f"Author deduplication complete: {len(self.data.author)} unique authors with {total_affs_after_dedup} total affiliations",
        )

    def _merge_person_objects(self, persons: list):
        """
        Merge multiple Person objects with the same name into one.

        Args:
            persons: List of Person objects to merge

        Returns:
            Merged Person object
        """
        from ..data_models.models import Person

        if len(persons) == 1:
            return persons[0]

        # Prioritize the person with the most information (prefer orcid > github > gimie)
        # Sort by: has ORCID, has affiliations, source priority
        source_priority = {
            "orcid": 3,
            "agent_user_enrichment": 2,
            "github_profile": 1,
            "gimie": 0,
        }

        def person_score(p):
            score = 0
            if hasattr(p, "orcid") and p.orcid:
                score += 100
            if hasattr(p, "affiliations") and p.affiliations:
                score += 10 * len(p.affiliations)
            if hasattr(p, "linkedEntities") and p.linkedEntities:
                score += 5 * len(p.linkedEntities)
            if hasattr(p, "source") and p.source:
                # Check if source contains any priority keywords
                for source_key, priority in source_priority.items():
                    if source_key in str(p.source).lower():
                        score += priority
                        break
            return score

        # Use the person with the highest score as base
        base = max(persons, key=person_score)

        # Collect all unique values across all persons
        all_ids = []
        all_emails = []
        all_github_ids = []
        all_orcids = []
        all_affiliations = []
        all_affiliation_history = []
        all_linked_entities = []
        all_sources = []

        for person in persons:
            # IDs
            if person.id:
                all_ids.append(person.id)

            # Emails
            if person.emails:
                all_emails.extend(person.emails)

            # GitHub IDs
            if hasattr(person, "githubId") and person.githubId:
                all_github_ids.append(person.githubId)

            # ORCIDs - prefer full URLs
            if person.orcid:
                all_orcids.append(person.orcid)

            # Affiliations
            if person.affiliations:
                all_affiliations.extend(person.affiliations)

            # Affiliation history
            if person.affiliationHistory:
                all_affiliation_history.extend(person.affiliationHistory)

            # Linked entities
            if hasattr(person, "linkedEntities") and person.linkedEntities:
                all_linked_entities.extend(person.linkedEntities)

            # Sources
            if person.source:
                all_sources.append(person.source)

        # Deduplicate and merge
        # ID: Priority - GitHub URL > ORCID URL > internal ID (gitAuthor) > hash name
        # Note: If we have both GitHub and ORCID, use GitHub URL for id field
        merged_id = None
        github_urls = [id for id in all_ids if "github.com" in id]
        orcid_urls = [id for id in all_ids if "orcid.org" in id]
        git_author_hashes = [
            id
            for id in all_ids
            if len(id) == 64 and all(c in "0123456789abcdef" for c in id)
        ]  # SHA-256 hash
        other_ids = [
            id
            for id in all_ids
            if id not in github_urls + orcid_urls + git_author_hashes
        ]

        # Prefer GitHub URL as the primary ID
        if github_urls:
            merged_id = github_urls[0]
        elif orcid_urls:
            merged_id = orcid_urls[0]
        elif git_author_hashes:
            merged_id = git_author_hashes[0]
        elif other_ids:
            merged_id = other_ids[0]
        elif all_ids:
            merged_id = all_ids[0]

        # GitHub ID: Deduplicate and extract from URLs if needed
        merged_github_id = None
        if all_github_ids:
            # Prefer non-URL format
            non_url_github = [g for g in all_github_ids if not g.startswith("http")]
            if non_url_github:
                merged_github_id = non_url_github[0]
            else:
                merged_github_id = all_github_ids[0]

        # If we have GitHub URLs but no explicit githubId, extract from URL
        if not merged_github_id and github_urls:
            # Extract from GitHub URL (e.g., https://github.com/username)
            github_url = github_urls[0]
            if github_url.startswith("https://github.com/"):
                username = github_url.replace("https://github.com/", "").split("/")[0]
                if username:  # Ensure we extracted a valid username
                    merged_github_id = username

        # Emails: Deduplicate (case-insensitive)
        merged_emails = list(
            {email.lower(): email for email in all_emails if email}.values(),
        )

        # ORCID: Always store as ID format (xxxx-xxxx-xxxx-xxxx), not URL
        merged_orcid = None
        for orcid in all_orcids:
            if not orcid:
                continue
            # Extract ID from URL if it's a URL
            if orcid.startswith("https://orcid.org/"):
                orcid_id = orcid.replace("https://orcid.org/", "")
                if orcid_id:
                    merged_orcid = orcid_id
                    break
            else:
                # Already in ID format
                merged_orcid = orcid
                break

        # Affiliations: Deduplicate by name (case-insensitive)
        # Keep the one with the most information (non-empty organizationId preferred)
        affiliation_dict = {}
        for aff in all_affiliations:
            aff_name_lower = aff.name.lower() if aff.name else ""
            if not aff_name_lower:
                continue

            # Check if this affiliation has a valid (non-empty) organizationId
            has_org_id = bool(aff.organizationId and aff.organizationId.strip())

            if aff_name_lower not in affiliation_dict:
                # First occurrence of this affiliation
                affiliation_dict[aff_name_lower] = aff
            else:
                # Duplicate - keep the one with more information
                existing = affiliation_dict[aff_name_lower]
                existing_has_org_id = bool(
                    existing.organizationId and existing.organizationId.strip(),
                )

                # Prefer the one with a non-empty organizationId
                if has_org_id and not existing_has_org_id:
                    affiliation_dict[aff_name_lower] = aff
                # If both have organizationId or neither has it, keep the first one

        merged_affiliations = list(affiliation_dict.values())

        # Affiliation history: Deduplicate strings
        merged_affiliation_history = list(set(all_affiliation_history))

        # Linked entities: Deduplicate based on UUID or entity content
        # Use a dictionary to track unique entities by UUID (or full entity if no UUID)
        unique_entities = {}
        for linked_entity in all_linked_entities:
            if not linked_entity:
                continue

            # Try to get a unique key for this linked entity
            entity_key = None

            # Check if entity has a UUID (most reliable)
            if hasattr(linked_entity, "entity") and linked_entity.entity:
                entity_obj = linked_entity.entity
                if hasattr(entity_obj, "uuid") and entity_obj.uuid:
                    entity_key = f"{linked_entity.catalogType}:{linked_entity.entityType}:{entity_obj.uuid}"
                elif hasattr(entity_obj, "id"):
                    entity_key = f"{linked_entity.catalogType}:{linked_entity.entityType}:{entity_obj.id}"

            # Fallback: Use catalog type + entity type + justification
            if not entity_key:
                entity_key = f"{linked_entity.catalogType}:{linked_entity.entityType}:{linked_entity.justification}"

            # Add to dict (overwrites duplicates)
            if entity_key not in unique_entities:
                unique_entities[entity_key] = linked_entity

        merged_linked_entities = list(unique_entities.values())

        # Sources: Combine unique sources
        merged_sources = ", ".join(sorted(set(all_sources)))

        # Create merged Person
        merged = Person(
            type="Person",
            id=merged_id or base.id,
            name=base.name,  # Use original name (not normalized)
            emails=merged_emails,
            githubId=merged_github_id,
            orcid=merged_orcid,
            affiliations=merged_affiliations,
            affiliationHistory=merged_affiliation_history,
            source=merged_sources,
        )

        # Add linked entities if the field exists
        if hasattr(merged, "linkedEntities"):
            merged.linkedEntities = merged_linked_entities

        # Log detailed affiliation info for debugging
        input_affs = sum(
            len(p.affiliations) if hasattr(p, "affiliations") else 0 for p in persons
        )
        logger.debug(
            f"Merged {len(persons)} instances of {base.name}: "
            f"input {input_affs} affiliations → output {len(merged_affiliations)} unique affiliations, "
            f"{len(merged_linked_entities)} linked entities",
        )

        return merged

    def _reconcile_entity_union(self, rel_dict: dict) -> dict:
        """
        Reconcile entity Union fields that were split during simplification.

        The entity field Union[InfosciencePublication, InfoscienceAuthor, InfoscienceLab]
        gets split into:
        - entityInfosciencePublication
        - entityInfoscienceAuthor
        - entityInfoscienceLab

        This method merges them back into the single 'entity' field based on entityType.

        Args:
            rel_dict: Relation dict with split entity fields

        Returns:
            Relation dict with unified 'entity' field
        """
        # Get entityType to determine which union variant to use
        entity_type = rel_dict.get("entityType", "").lower()

        # Extract all three possible entity fields
        entity_pub = rel_dict.pop("entityInfosciencePublication", None)
        entity_author = rel_dict.pop("entityInfoscienceAuthor", None)
        entity_lab = rel_dict.pop("entityInfoscienceLab", None)

        # Select the correct entity based on entityType
        selected_entity = None
        if entity_type == "publication" and entity_pub is not None:
            selected_entity = entity_pub
        elif entity_type == "person" and entity_author is not None:
            selected_entity = entity_author
        elif entity_type == "orgunit" and entity_lab is not None:
            selected_entity = entity_lab
        else:
            # Fallback: use whichever one is not None (if entityType doesn't match)
            if entity_pub is not None:
                selected_entity = entity_pub
            elif entity_author is not None:
                selected_entity = entity_author
            elif entity_lab is not None:
                selected_entity = entity_lab

        # Clean up None values in list fields (convert to empty list)
        if selected_entity and isinstance(selected_entity, dict):
            # Handle subjects field (should be list, not None)
            if "subjects" in selected_entity and selected_entity["subjects"] is None:
                selected_entity["subjects"] = []
            # Handle other list fields that might be None
            for key, value in selected_entity.items():
                if value is None and key in ["authors", "keywords", "subjects"]:
                    selected_entity[key] = []

        # Set the unified entity field
        if selected_entity is not None:
            rel_dict["entity"] = selected_entity
        # If all None, entity will remain None (which is allowed)

        return rel_dict

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

        # Helper function to clean None values for list fields in any model dict
        def clean_model_dict(model_dict: dict, model_type: type) -> dict:
            """Convert None to empty lists for fields with default_factory=list"""
            if not isinstance(model_dict, dict):
                return model_dict

            # Check if model_type has model_fields
            if hasattr(model_type, "model_fields"):
                for field_name, field_info in model_type.model_fields.items():
                    if field_name in model_dict and model_dict[field_name] is None:
                        # Check if field has default_factory and it's callable
                        if (
                            hasattr(field_info, "default_factory")
                            and field_info.default_factory is not ...
                            and field_info.default_factory is not None
                            and callable(field_info.default_factory)
                        ):
                            # Convert None to empty value from default_factory
                            model_dict[field_name] = field_info.default_factory()
            return model_dict

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
                                    # Clean None values before instantiation
                                    value = clean_model_dict(value, target_type)
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
            from ..data_models.models import Affiliation, Person

            gimie_authors = []
            for gimie_author in gimie_authors_orgs["authors"]:
                # Convert GIMIE author dict to Person object
                person_id = gimie_author.get("id", "")
                person_data = {
                    "type": "Person",
                    "id": person_id,
                    "name": gimie_author.get("name", ""),
                    "source": "gimie",
                }

                # Extract GitHub ID from GitHub URL if present
                if person_id and "github.com/" in person_id:
                    # Extract username from URL like "https://github.com/username"
                    try:
                        github_username = person_id.rstrip("/").split("/")[-1]
                        if github_username and github_username != "github.com":
                            person_data["githubId"] = github_username
                    except Exception as e:
                        logger.debug(
                            f"Could not extract GitHub ID from {person_id}: {e}",
                        )

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
                        # If it's already an Affiliation object, keep it
                        if isinstance(aff, Affiliation):
                            affiliations.append(aff)
                        elif isinstance(aff, dict):
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
                    new_person = Person(**person_data)
                    gimie_authors.append(new_person)
                    logger.debug(
                        f"Created GIMIE Person {new_person.name} with {len(new_person.affiliations)} affiliations",
                    )
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
                        # Clean None values before creating Person
                        author = clean_model_dict(author, Person)
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

                        # Update GitHub ID if missing and GIMIE author has one
                        if (
                            not updated_data.get("githubId")
                            and hasattr(gimie_author, "githubId")
                            and gimie_author.githubId
                        ):
                            updated_data["githubId"] = gimie_author.githubId
                            updated = True

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
                            added_count = 0
                            for aff in gimie_author.affiliations:
                                if (
                                    isinstance(aff, Affiliation)
                                    and aff.name.lower() not in existing_names
                                ):
                                    updated_data.setdefault("affiliations", []).append(
                                        aff,
                                    )
                                    updated = True
                                    added_count += 1
                            if added_count > 0:
                                logger.debug(
                                    f"Added {added_count} GIMIE affiliations to {existing_author.name}, total now: {len(updated_data.get('affiliations', []))}",
                                )

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
                        if (
                            not existing_author.get("githubId")
                            and hasattr(gimie_author, "githubId")
                            and gimie_author.githubId
                        ):
                            existing_author["githubId"] = gimie_author.githubId
                            updated = True
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
                        f"Added new GIMIE author: {gimie_author.name} with {len(gimie_author.affiliations)} affiliations (id: {gimie_author.id})",
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
                # Log affiliation counts
                total_affs_after_merge = sum(
                    len(a.affiliations)
                    if isinstance(a, Person) and hasattr(a, "affiliations")
                    else 0
                    for a in final_authors
                )
                logger.info(
                    f"Merged {len(gimie_authors)} GIMIE authors with {len(existing_person_objects)} model authors, total: {len(final_authors)} authors with {total_affs_after_merge} total affiliations",
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

        # Convert SimplifiedOrganization objects to full Organization objects
        if "relatedToOrganizations" in simplified_dict and simplified_dict.get(
            "relatedToOrganizations",
        ):
            organizations = []
            for org_data in simplified_dict["relatedToOrganizations"]:
                # Convert dict to Organization if needed
                if isinstance(org_data, dict):
                    # Map SimplifiedOrganization.name to Organization.legalName
                    if "name" in org_data and "legalName" not in org_data:
                        org_data["legalName"] = org_data.pop("name")

                    # Ensure type is set
                    if "type" not in org_data:
                        org_data["type"] = "Organization"
                    # Set source if not present
                    if "source" not in org_data:
                        org_data["source"] = "atomic_agent"
                    # Ensure id is set (use legalName as fallback if no id)
                    if "id" not in org_data or not org_data["id"]:
                        org_data["id"] = org_data.get("legalName", "")

                    try:
                        org = Organization(**org_data)
                        organizations.append(org)
                    except Exception as e:
                        logger.warning(f"Failed to create Organization from dict: {e}")
                        continue
                elif isinstance(org_data, Organization):
                    organizations.append(org_data)
                else:
                    logger.warning(
                        f"Unexpected organization data type: {type(org_data)}",
                    )

            if organizations:
                full_dict["relatedToOrganizations"] = organizations
                logger.info(
                    f"Converted {len(organizations)} organizations to full Organization objects",
                )

        # Pass through relatedToOrganizationJustification if present
        if "relatedToOrganizationJustification" in simplified_dict:
            full_dict["relatedToOrganizationJustification"] = simplified_dict[
                "relatedToOrganizationJustification"
            ]

        return full_dict

    def run_authors_enrichment(self):
        """
        Enrich authors with ORCID affiliations.

        This runs after LLM analysis and enriches Person objects that have ORCID IDs
        with affiliation data from ORCID API. Uses the Affiliation model with source="orcid".
        """
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
            logger.info(f"ORCID enrichment successful for {self.full_path}")
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
        """
        Enrich repository with linked entities relations using atomic pipeline.

        This uses a two-stage atomic pipeline:
        1. Search academic catalogs (Infoscience) with tools for repository and authors
        2. Structure the search results into organized relations
        """
        logger.info(f"linked entities enrichment for {self.full_path}")

        # Check if data exists before enrichment
        if self.data is None:
            logger.warning(
                f"Cannot enrich linked entities: no data available for {self.full_path}",
            )
            return

        try:
            # Extract repository name from existing data
            repository_name = self.data.name or self.full_path.split("/")[-1]

            logger.info(
                f"Searching Infoscience for repository: '{repository_name}'",
            )

            # Stage 1: Search academic catalogs with tools (max 5 results per search)
            logger.info(
                "Stage 1: Searching academic catalogs (repository-level only)...",
            )
            search_result = await search_academic_catalogs(
                repository_name=repository_name,
            )

            search_context = search_result.get("data")
            usage = search_result.get("usage")

            if not search_context:
                logger.error("Academic catalog search failed")
                return

            # Accumulate usage from stage 1
            if usage:
                self.total_input_tokens += usage.get("input_tokens", 0)
                self.total_output_tokens += usage.get("output_tokens", 0)
                if "estimated_input_tokens" in usage:
                    self.estimated_input_tokens += usage.get(
                        "estimated_input_tokens",
                        0,
                    )
                    self.estimated_output_tokens += usage.get(
                        "estimated_output_tokens",
                        0,
                    )

            # Stage 2: Structure the search results
            logger.info(
                "Stage 2: Structuring linked entities results (repository-level only)...",
            )

            # Generate a simplified schema for the structured output
            # Note: The simplified model is generated dynamically in linked_entities_searcher.py
            schema = {
                "repository_relations": {
                    "type": "array",
                    "description": "Publications/entities about the repository itself",
                },
            }

            structure_result = await structure_linked_entities(
                search_context=search_context,
                schema=schema,
            )

            enrichment_data = structure_result.get("data")
            usage = structure_result.get("usage")

            if not enrichment_data:
                logger.error("Linked entities structuring failed")
                return

            # Validate enrichment_data type
            if isinstance(enrichment_data, str):
                logger.error(
                    f"Enrichment data is a string (unexpected): {enrichment_data[:200]}",
                )
                return

            # Convert to dict if it's a Pydantic model
            if hasattr(enrichment_data, "model_dump"):
                enrichment_dict = enrichment_data.model_dump()
            elif isinstance(enrichment_data, dict):
                enrichment_dict = enrichment_data
            else:
                logger.error(
                    f"Unexpected enrichment_data type: {type(enrichment_data)}",
                )
                return

            logger.info(
                f"Structured linked entities result with keys: {list(enrichment_dict.keys())}",
            )

            # Accumulate usage from stage 2
            if usage:
                self.total_input_tokens += usage.get("input_tokens", 0)
                self.total_output_tokens += usage.get("output_tokens", 0)
                if "estimated_input_tokens" in usage:
                    self.estimated_input_tokens += usage.get(
                        "estimated_input_tokens",
                        0,
                    )
                    self.estimated_output_tokens += usage.get(
                        "estimated_output_tokens",
                        0,
                    )

            # Store the linked entities relations at repository level
            if enrichment_dict:
                # Debug: Log what we got
                logger.info(f"Enrichment dict keys: {list(enrichment_dict.keys())}")
                logger.info(
                    f"Repository relations count: {len(enrichment_dict.get('repository_relations', []))}",
                )
                logger.info(
                    f"Author relations count: {len(enrichment_dict.get('author_relations', {}))}",
                )

                # Repository-level relations (publications about the repository itself)
                if "repository_relations" in enrichment_dict:
                    # Convert simplified relations to full relations
                    from ..data_models import linkedEntitiesRelation

                    repo_relations = []
                    repo_rels_list = enrichment_dict.get("repository_relations", [])
                    logger.info(
                        f"Processing {len(repo_rels_list)} repository relations...",
                    )

                    for idx, simplified_rel in enumerate(repo_rels_list):
                        # Skip if it's a string (shouldn't happen, but handle gracefully)
                        if isinstance(simplified_rel, str):
                            logger.warning(
                                f"Skipping repository relation {idx}: got string instead of dict",
                            )
                            continue

                        # Convert to dict
                        if hasattr(simplified_rel, "model_dump"):
                            rel_dict = simplified_rel.model_dump()
                        elif isinstance(simplified_rel, dict):
                            rel_dict = simplified_rel
                        else:
                            logger.warning(
                                f"Skipping repository relation {idx}: unexpected type {type(simplified_rel)}",
                            )
                            continue

                        # Reconcile Union fields (entity split into entityInfosciencePublication, entityInfoscienceAuthor, entityInfoscienceLab)
                        rel_dict = self._reconcile_entity_union(rel_dict)

                        try:
                            repo_relations.append(linkedEntitiesRelation(**rel_dict))
                        except Exception as e:
                            logger.warning(
                                f"Failed to create linkedEntitiesRelation: {e}",
                            )
                            continue

                    self.data.linkedEntities = repo_relations
                    logger.info(
                        f"✓ Stored {len(repo_relations)} repository-level linked entities relations",
                    )
                else:
                    logger.warning(
                        "No 'repository_relations' key found in enrichment_dict",
                    )
                    self.data.linkedEntities = []

                # Note: Author-level linked entities are handled in optional enrichment
                # See run_author_linked_entities_enrichment() for per-author Infoscience searches

        except Exception as e:
            logger.error(f"linked entities enrichment failed: {e}", exc_info=True)
            # Don't fail the entire analysis, just skip linked entities enrichment
            return

    async def run_author_linked_entities_enrichment(self):
        """
        Optional enrichment: Search Infoscience for each author individually.

        This is separate from the main atomic pipeline and runs only when requested.
        Assigns linkedEntities to each Person in self.data.author.
        """
        logger.info(f"Author-level linked entities enrichment for {self.full_path}")

        # Check if data exists
        if self.data is None:
            logger.warning(
                f"Cannot enrich author linked entities: no data available for {self.full_path}",
            )
            return

        # Check if we have authors
        if not hasattr(self.data, "author") or not self.data.author:
            logger.info("No authors to enrich with linked entities")
            return

        try:
            # Import Infoscience tools
            from ..context.infoscience import (
                search_infoscience_authors_tool,
            )

            # Search for each author
            for author in self.data.author:
                if not hasattr(author, "name") or not author.name:
                    continue

                logger.info(f"Searching Infoscience for author: {author.name}")

                # Search for author profile and publications
                try:
                    # Search for author profile
                    author_results = await search_infoscience_authors_tool(
                        name=author.name,
                        max_results=5,
                    )

                    # Parse results and create linkedEntitiesRelation objects
                    # Note: This is a simplified direct search, not via atomic pipeline
                    # Results parsing would need to be implemented based on tool output format

                    # For now, log that we searched
                    logger.info(
                        f"Searched Infoscience for {author.name}: {len(author_results) if author_results else 0} results",
                    )

                    # TODO: Parse author_results and create linkedEntitiesRelation objects
                    # author.linkedEntities = [...]

                except Exception as e:
                    logger.warning(
                        f"Failed to search Infoscience for author {author.name}: {e}",
                    )
                    continue

        except Exception as e:
            logger.error(
                f"Author linked entities enrichment failed: {e}",
                exc_info=True,
            )
            # Don't fail the entire analysis
            return

    async def run_epfl_final_assessment(self):
        """
        Run final EPFL relationship assessment using atomic pipeline after all enrichments complete.

        This uses a two-stage atomic pipeline:
        1. Compile enriched data into markdown context
        2. Assess EPFL relationship from compiled context
        """
        logger.info(f"Final EPFL assessment for {self.full_path}")

        # Check if data exists
        if self.data is None:
            logging.warning(
                f"Cannot run EPFL assessment: no data available for {self.full_path}",
            )
            return

        try:
            # Convert data to dict for compilation
            enriched_data_dict = self.data.model_dump()

            # Stage 1: Compile enriched data into markdown context
            logger.info("Stage 1: Compiling enriched data for EPFL assessment...")
            compilation_result = await compile_enriched_data_for_epfl(
                enriched_data=enriched_data_dict,
                repository_url=self.full_path,
            )

            enriched_context = compilation_result.get("data")
            usage = compilation_result.get("usage")

            if not enriched_context:
                logger.error("Enriched data compilation failed")
                return

            # Accumulate usage from stage 1
            if usage:
                self.total_input_tokens += usage.get("input_tokens", 0)
                self.total_output_tokens += usage.get("output_tokens", 0)
                if "estimated_input_tokens" in usage:
                    self.estimated_input_tokens += usage.get(
                        "estimated_input_tokens",
                        0,
                    )
                    self.estimated_output_tokens += usage.get(
                        "estimated_output_tokens",
                        0,
                    )

            # Stage 2: Assess EPFL relationship from compiled context
            logger.info("Stage 2: Assessing EPFL relationship...")
            assessment_result = await assess_final_epfl_relationship(
                enriched_context=enriched_context,
            )

            assessment = assessment_result.get("data")
            usage = assessment_result.get("usage")

            if not assessment:
                logger.error("EPFL assessment failed")
                return

            # Accumulate usage from stage 2
            if usage:
                self.total_input_tokens += usage.get("input_tokens", 0)
                self.total_output_tokens += usage.get("output_tokens", 0)
                if "estimated_input_tokens" in usage:
                    self.estimated_input_tokens += usage.get(
                        "estimated_input_tokens",
                        0,
                    )
                    self.estimated_output_tokens += usage.get(
                        "estimated_output_tokens",
                        0,
                    )

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
            return self.data.model_dump_json(indent=2, exclude_none=True)
        elif output_type == "dict":
            return self.data.model_dump(exclude_none=True)
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
        run_author_linked_entities: bool = False,
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
            logging.info(f"LLM analysis completed for {self.full_path}")

            # Run ORCID enrichment after LLM analysis (enriches authors with ORCID IDs)
            if self.data is not None:
                logging.info(f"ORCID enrichment for {self.full_path}")
                self.run_authors_enrichment()
                logging.info(f"ORCID enrichment completed for {self.full_path}")
            else:
                logging.warning(
                    f"Skipping ORCID enrichment: LLM analysis failed for {self.full_path}",
                )

            # Run user enrichment
            if run_user_enrichment and self.data is not None:
                logging.info(f"User enrichment for {self.full_path}")
                await self.run_user_enrichment()
                logging.info(f"User enrichment completed for {self.full_path}")
                # Deduplicate authors after user enrichment
                self._deduplicate_authors()

            # Run organization enrichment
            if run_organization_enrichment and self.data is not None:
                logging.info(f"Organization enrichment for {self.full_path}")
                await self.run_organization_enrichment()
                logging.info(f"Organization enrichment completed for {self.full_path}")

            # Run academic catalog linked entities enrichment (atomic pipeline with tools)
            if self.data is not None:
                logging.info(f"Academic catalog enrichment for {self.full_path}")
                await self.run_linked_entities_enrichment()
                logging.info(
                    f"Academic catalog enrichment completed for {self.full_path}",
                )

                # Run optional per-author linked entities enrichment
                if run_author_linked_entities:
                    logging.info(
                        f"Author-level linked entities enrichment for {self.full_path}",
                    )
                    await self.run_author_linked_entities_enrichment()
                    logging.info(
                        f"Author-level linked entities enrichment completed for {self.full_path}",
                    )

                # Deduplicate authors after linked entities enrichment
                self._deduplicate_authors()

            # Run final EPFL assessment after all enrichments complete (atomic pipeline)
            if self.data is not None:
                logging.info(f"Final EPFL assessment for {self.full_path}")
                await self.run_epfl_final_assessment()
                logging.info(f"Final EPFL assessment completed for {self.full_path}")

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

        # Log duration and final token usage summary
        if self.start_time and self.end_time:
            duration = (self.end_time - self.start_time).total_seconds()

            # Final token usage summary
            logger.info("")
            logger.info("=" * 80)
            logger.info("FINAL TOKEN USAGE SUMMARY (All Stages)")
            logger.info("=" * 80)
            logger.info("  Official API Counts:")
            logger.info(f"    Input tokens:  {self.total_input_tokens:,}")
            logger.info(f"    Output tokens: {self.total_output_tokens:,}")
            logger.info(
                f"    Total tokens:  {self.total_input_tokens + self.total_output_tokens:,}",
            )
            logger.info("")
            logger.info("  Estimated Counts (tiktoken):")
            logger.info(f"    Input tokens:  {self.estimated_input_tokens:,}")
            logger.info(f"    Output tokens: {self.estimated_output_tokens:,}")
            logger.info(
                f"    Total tokens:  {self.estimated_input_tokens + self.estimated_output_tokens:,}",
            )
            logger.info("")
            logger.info(f"  Analysis Duration: {duration:.2f} seconds")
            logger.info(
                f"  Status: {'SUCCESS' if self.analysis_successful else 'FAILED'}",
            )
            logger.info("=" * 80)
