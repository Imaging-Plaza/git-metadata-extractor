import logging
from datetime import datetime

from ..agents.academic_catalog_enrichment import enrich_repository_academic_catalog
from ..agents.epfl_assessment import assess_epfl_relationship
from ..agents.organization_enrichment import enrich_organizations_from_dict
from ..agents.repository import llm_request_repo_infos
from ..agents.user_enrichment import enrich_users_from_dict
from ..cache.cache_manager import CacheManager, get_cache_manager
from ..data_models import SoftwareSourceCode
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
        result = await llm_request_repo_infos(
            str(self.full_path),
            gimie_output=self.gimie,
            max_tokens=20000,
        )

        # Extract data and usage
        llm_data = result.get("data") if isinstance(result, dict) else result
        usage = result.get("usage") if isinstance(result, dict) else None

        # Accumulate official API-reported usage data
        if usage:
            self.total_input_tokens += usage.get("input_tokens", 0)
            self.total_output_tokens += usage.get("output_tokens", 0)
            logger.info(
                f"LLM analysis usage: {usage.get('input_tokens', 0)} input, {usage.get('output_tokens', 0)} output tokens",
            )

        # Accumulate estimated tokens
        if usage and "estimated_input_tokens" in usage:
            self.estimated_input_tokens += usage.get("estimated_input_tokens", 0)
            self.estimated_output_tokens += usage.get("estimated_output_tokens", 0)
            logger.info(
                f"LLM analysis estimated: {usage.get('estimated_input_tokens', 0)} input, {usage.get('estimated_output_tokens', 0)} output tokens",
            )

        # Output Validation
        if isinstance(llm_data, str):
            llm_data = SoftwareSourceCode.model_validate_json(llm_data)
        elif isinstance(llm_data, dict):
            llm_data = SoftwareSourceCode.model_validate(llm_data)

        if isinstance(llm_data, SoftwareSourceCode):
            self.data = llm_data

        # TODO: Handle errors and logging

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

            # Replace (not append) organization lists with enriched versions
            # Build list of organization names for relatedToOrganizations
            related_orgs = []
            for org in enriched_orgs:
                legal_name = org.legalName
                if legal_name:
                    related_orgs.append(legal_name)

            # Replace the lists with enriched data only
            self.data.relatedToOrganizations = related_orgs
            self.data.relatedToOrganizationsROR = enriched_orgs

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

        logger.info(f"User enrichment: {user_enrichment}")

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
            if hasattr(self.data, "readme") and self.data.readme:
                # The readme field is a URL, we'd need to fetch it
                # For now, use description or other text
                pass

            # Try to get some text from description or other fields
            if self.data.description:
                readme_excerpt = self.data.description[:1000]
            elif (
                hasattr(self.data, "hasExecutableInstructions")
                and self.data.hasExecutableInstructions
            ):
                readme_excerpt = self.data.hasExecutableInstructions[:1000]

            # Extract author names and organization names from existing data
            author_names = []
            organization_names = []

            if hasattr(self.data, "author") and self.data.author:
                for author in self.data.author:
                    if hasattr(author, "name") and author.name:
                        author_names.append(author.name)
                    elif hasattr(author, "legalName") and author.legalName:
                        organization_names.append(author.legalName)

            # Also check relatedToOrganizationsROR
            if (
                hasattr(self.data, "relatedToOrganizationsROR")
                and self.data.relatedToOrganizationsROR
            ):
                for org in self.data.relatedToOrganizationsROR:
                    if hasattr(org, "legalName") and org.legalName:
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
            if self.data is not None:
                self.run_authors_enrichment()
            else:
                logging.warning(
                    f"Skipping author enrichment: LLM analysis failed for {self.full_path}",
                )

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
        if self.data is not None:
            logging.info(f"Academic catalog enrichment for {self.full_path}")
            await self.run_academic_catalog_enrichment()
            logging.info(f"Academic catalog enrichment completed for {self.full_path}")

        # Run final EPFL assessment after all enrichments complete
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

        # Log duration
        if self.start_time and self.end_time:
            duration = (self.end_time - self.start_time).total_seconds()
            logging.info(f"Analysis completed in {duration:.2f} seconds")
