import logging
from datetime import datetime
from typing import Optional

from ..agents.academic_catalog_enrichment import enrich_repository_academic_catalog
from ..agents.atomic_agents import (
    check_epfl_relationship,
    compile_repository_context,
    generate_structured_output,
)
from ..agents.epfl_assessment import assess_epfl_relationship
from ..agents.organization_enrichment import enrich_organizations_from_dict
from ..agents.user_enrichment import enrich_users_from_dict
from ..cache.cache_manager import CacheManager, get_cache_manager
from ..context import prepare_repository_context
from ..data_models import Organization, SoftwareSourceCode
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

        # Prepare GIMIE data as string if available
        gimie_data = None
        if self.gimie:
            import json as json_module

            gimie_data = (
                json_module.dumps(self.gimie)
                if isinstance(self.gimie, dict)
                else str(self.gimie)
            )
            logger.debug("=" * 80)
            logger.debug("GIMIE DATA PROVIDED TO CONTEXT COMPILER:")
            logger.debug("=" * 80)
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
        full_dict = self._convert_simplified_to_full(simplified_dict, union_metadata)

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

    def _convert_simplified_to_full(
        self,
        simplified_dict: dict,
        union_metadata: Optional[dict] = None,
    ) -> dict:
        """
        Convert simplified output dict to full SoftwareSourceCode format.

        Args:
            simplified_dict: Simplified output from structured output agent
            union_metadata: Metadata about Union fields that were split (for reconciliation)

        Returns:
            Dictionary in full SoftwareSourceCode format
        """
        from datetime import date

        from pydantic import BaseModel, HttpUrl

        from ..data_models.models import RepositoryType

        if union_metadata is None:
            union_metadata = {}

        full_dict = simplified_dict.copy()

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

        # name
        if "name" in simplified_dict:
            full_dict["name"] = simplified_dict["name"]

        # applicationCategory
        if "applicationCategory" in simplified_dict:
            full_dict["applicationCategory"] = simplified_dict["applicationCategory"]

        # codeRepository - convert strings to HttpUrl
        if "codeRepository" in simplified_dict and full_dict.get("codeRepository"):
            try:
                full_dict["codeRepository"] = [
                    HttpUrl(url) for url in full_dict["codeRepository"]
                ]
            except Exception as e:
                logger.warning(f"Failed to convert codeRepository URLs: {e}")
                full_dict["codeRepository"] = []

        # dateCreated - convert string to date
        if "dateCreated" in simplified_dict and simplified_dict["dateCreated"]:
            try:
                full_dict["dateCreated"] = date.fromisoformat(
                    simplified_dict["dateCreated"],
                )
            except (ValueError, TypeError):
                logger.warning(
                    f"Failed to parse dateCreated: {simplified_dict['dateCreated']}",
                )
                full_dict["dateCreated"] = None

        # license
        if "license" in simplified_dict:
            full_dict["license"] = simplified_dict["license"]

        # gitAuthors - convert to GitAuthor format
        if "gitAuthors" in simplified_dict and simplified_dict.get("gitAuthors"):
            git_authors = []
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
                    git_author_dict["commits"] = {
                        "count": commits_dict.get("count", 0),
                    }
                    if commits_dict.get("firstCommit"):
                        git_author_dict["commits"]["firstCommit"] = commits_dict[
                            "firstCommit"
                        ]
                    if commits_dict.get("lastCommit"):
                        git_author_dict["commits"]["lastCommit"] = commits_dict[
                            "lastCommit"
                        ]
                git_authors.append(git_author_dict)
            full_dict["gitAuthors"] = git_authors

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

    async def run_academic_catalog_enrichment(self):
        """Enrich repository with academic catalog relations (Infoscience, etc.)"""
        logger.info(f"Academic catalog enrichment for {self.full_path}")

        # Check if data exists before enrichment
        if self.data is None:
            logger.warning(
                f"Cannot enrich academic catalogs: no data available for {self.full_path}",
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

            result = await enrich_repository_academic_catalog(
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
                    f"Academic catalog enrichment usage: {usage.get('input_tokens', 0)} input, "
                    f"{usage.get('output_tokens', 0)} output tokens",
                )

            if usage and "estimated_input_tokens" in usage:
                self.estimated_input_tokens += usage.get("estimated_input_tokens", 0)
                self.estimated_output_tokens += usage.get("estimated_output_tokens", 0)

            # Store the academic catalog relations at repository level
            if enrichment_data:
                # Repository-level relations (publications about the repository itself)
                if hasattr(enrichment_data, "repository_relations"):
                    self.data.academicCatalogRelations = (
                        enrichment_data.repository_relations
                    )
                    logger.info(
                        f"Stored {len(enrichment_data.repository_relations)} repository-level academic catalog relations",
                    )
                # Fallback for backward compatibility
                elif hasattr(enrichment_data, "relations"):
                    self.data.academicCatalogRelations = enrichment_data.relations
                    logger.info(
                        f"Stored {len(enrichment_data.relations)} academic catalog relations at repository level",
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
                                    author.academicCatalogRelations = author_rels
                                    logger.info(
                                        f"✓ Directly assigned {len(author_rels)} relations to author: {author.name}",
                                    )
                                else:
                                    # Author had no results
                                    author.academicCatalogRelations = []
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
                                    author.academicCatalogRelations = org_rels
                                    logger.info(
                                        f"✓ Directly assigned {len(org_rels)} relations to organization: {author.legalName}",
                                    )
                                else:
                                    author.academicCatalogRelations = []
                                    logger.info(
                                        f"ℹ No relations found for organization: {author.legalName}",
                                    )

        except Exception as e:
            logger.error(f"Academic catalog enrichment failed: {e}", exc_info=True)
            # Don't fail the entire analysis, just skip academic catalog enrichment
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
        #     await self.run_academic_catalog_enrichment()
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
