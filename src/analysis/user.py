import logging
from datetime import datetime

from ..agents import llm_request_user_infos
from ..agents.epfl_assessment import assess_epfl_relationship
from ..agents.linked_entities_enrichment import enrich_user_linked_entities
from ..agents.organization_enrichment import enrich_organizations_from_dict
from ..agents.user_enrichment import enrich_users_from_dict
from ..cache.cache_manager import CacheManager, get_cache_manager
from ..data_models import GitHubUser
from ..parsers import parse_github_user

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class User:
    def __init__(self, username: str, force_refresh: bool = False):
        self.username: str = username
        self.data: GitHubUser = None
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

    def run_github_parsing(self):
        """Parse GitHub user metadata and convert to GitHubUser model"""
        logger.info(f"Parsing GitHub user data for {self.username}")

        # Parse GitHub user metadata
        github_metadata = parse_github_user(self.username)

        # Convert GitHubUserMetadata to dict and merge into self.data
        user_data_dict = github_metadata.model_dump()

        # Map GitHubUserMetadata fields to GitHubUser model
        self.data = GitHubUser(
            # Basic fields
            name=user_data_dict.get("name"),
            fullname=user_data_dict.get("name"),  # Use name as fullname by now
            githubHandle=user_data_dict.get("login"),
            githubUserMetadata=github_metadata,
            # Enrichment fields (will be populated by analysis steps)
            # NOTE: Don't pre-populate relatedToOrganization with GitHub orgs here
            # Let LLM analysis extract them from all sources (bio, README, GitHub orgs, etc.)
            # This prevents duplication when enrichment adds Organization objects later
            relatedToOrganization=[],
            relatedToOrganizationJustification=[],
            discipline=[],
            disciplineJustification=[],
            position=[],
            positionJustification=[],
            relatedToEPFL=None,
            relatedToEPFLJustification=None,
            relatedToEPFLConfidence=None,
        )

    async def run_llm_analysis(self):
        """Run LLM analysis to populate discipline and position fields"""
        logger.info(f"LLM analysis for {self.username}")

        # Prepare data for LLM analysis
        github_metadata = (
            self.data.githubUserMetadata.model_dump()
            if self.data.githubUserMetadata
            else {}
        )
        llm_input_data = {
            "username": self.username,
            "name": github_metadata.get("name"),
            "bio": github_metadata.get("bio"),
            "company": github_metadata.get("company"),
            "location": github_metadata.get("location"),
            "organizations": github_metadata.get("organizations", []),
            "orcid": github_metadata.get("orcid"),
            "orcid_activities": github_metadata.get("orcid_activities"),
            "readme_content": github_metadata.get("readme_content"),
            "public_repos": github_metadata.get("public_repos"),
            "followers": github_metadata.get("followers"),
            "following": github_metadata.get("following"),
        }

        try:
            # Call LLM to analyze user profile
            result = await llm_request_user_infos(
                username=self.username,
                user_data=llm_input_data,
                max_tokens=10000,
            )

            # Extract data and usage
            llm_result = result.get("data") if isinstance(result, dict) else result
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

            # Update self.data with LLM results
            if llm_result and isinstance(llm_result, dict):
                logger.info(f"LLM result keys: {list(llm_result.keys())}")

                # Extract organization information
                if llm_result.get("relatedToOrganization"):
                    self.data.relatedToOrganization = llm_result.get(
                        "relatedToOrganization",
                        [],
                    )
                    logger.info(
                        f"Set relatedToOrganization: {self.data.relatedToOrganization}",
                    )
                if llm_result.get("relatedToOrganizationJustification"):
                    self.data.relatedToOrganizationJustification = llm_result.get(
                        "relatedToOrganizationJustification",
                        [],
                    )
                    logger.info(
                        f"Set relatedToOrganizationJustification: {self.data.relatedToOrganizationJustification}",
                    )

                # Extract discipline information
                if llm_result.get("discipline"):
                    self.data.discipline = llm_result.get("discipline", [])
                    logger.info(f"Set discipline: {self.data.discipline}")
                if llm_result.get("disciplineJustification"):
                    self.data.disciplineJustification = llm_result.get(
                        "disciplineJustification",
                        [],
                    )
                    logger.info(
                        f"Set disciplineJustification: {self.data.disciplineJustification}",
                    )

                # Extract position information
                if llm_result.get("position"):
                    self.data.position = llm_result.get("position", [])
                    logger.info(f"Set position: {self.data.position}")
                if llm_result.get("positionJustification"):
                    self.data.positionJustification = llm_result.get(
                        "positionJustification",
                        [],
                    )
                    logger.info(
                        f"Set positionJustification: {self.data.positionJustification}",
                    )

                logger.info(f"LLM analysis completed for {self.username}")
            else:
                logger.warning(
                    f"LLM analysis returned no results for {self.username}: {llm_result}",
                )

        except Exception as e:
            logger.error(f"LLM analysis failed for {self.username}: {e}")
            # Don't fail the entire process, just log the error

    async def run_organization_enrichment(self):
        """Enrich organization data using PydanticAI agent"""
        logger.info(f"Organization enrichment for {self.username}")

        # Get github metadata
        github_metadata = (
            self.data.githubUserMetadata.model_dump()
            if self.data.githubUserMetadata
            else {}
        )

        # Format data for organization enrichment agent
        enrichment_data = {
            "gitAuthors": [],  # No git authors for user profiles
            "author": [],  # Will be populated with user's ORCID data if available
            "relatedToOrganizations": github_metadata.get("organizations", []),
            "relatedToOrganizationJustification": [],
            "relatedToEPFL": self.data.relatedToEPFL,  # Preserve existing value
            "relatedToEPFLJustification": self.data.relatedToEPFLJustification,  # Preserve existing value
            # Include LLM analysis results to preserve them
            "discipline": self.data.discipline or [],
            "disciplineJustification": self.data.disciplineJustification or [],
            "position": self.data.position or [],
            "positionJustification": self.data.positionJustification or [],
        }

        # Add user as author if we have ORCID data
        if github_metadata.get("orcid"):
            author_data = {
                "name": github_metadata.get("name") or self.data.fullname,
                "orcid": github_metadata.get("orcid"),
                "affiliation": github_metadata.get("organizations", []),
            }
            enrichment_data["author"] = [author_data]

        result = await enrich_organizations_from_dict(
            enrichment_data,
            f"https://github.com/{self.username}",
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
        enriched_orgs = organization_enrichment.organizations  # Direct attribute access

        # Replace the relatedToOrganization list with enriched Organization objects
        # This prevents duplication - we don't add both strings and objects
        # The LLM analysis already populated org name strings, now we replace them with full objects
        self.data.relatedToOrganization = list(enriched_orgs)

        # For user profiles, preserve any existing EPFL assessment
        # Only update EPFL values if they weren't already set
        if (
            self.data.relatedToEPFL is None
            and organization_enrichment.relatedToEPFL is not None
        ):
            self.data.relatedToEPFL = organization_enrichment.relatedToEPFL
            logger.info(f"Set relatedToEPFL from enrichment: {self.data.relatedToEPFL}")
        else:
            logger.info(f"Preserving existing relatedToEPFL: {self.data.relatedToEPFL}")

        if (
            self.data.relatedToEPFLJustification is None
            and organization_enrichment.relatedToEPFLJustification is not None
        ):
            self.data.relatedToEPFLJustification = (
                organization_enrichment.relatedToEPFLJustification
            )
            logger.info("Set relatedToEPFLJustification from enrichment")
        else:
            logger.info("Preserving existing relatedToEPFLJustification")

        if (
            self.data.relatedToEPFLConfidence is None
            and organization_enrichment.relatedToEPFLConfidence is not None
        ):
            self.data.relatedToEPFLConfidence = (
                organization_enrichment.relatedToEPFLConfidence
            )
            logger.info(
                f"Set relatedToEPFLConfidence from enrichment: {self.data.relatedToEPFLConfidence}",
            )
        else:
            logger.info(
                f"Preserving existing relatedToEPFLConfidence: {self.data.relatedToEPFLConfidence}",
            )

        # Note: Organization enrichment does NOT return discipline/position fields
        # so we preserve the LLM analysis results
        logger.info(f"Organization enrichment completed for {self.username}")

    async def run_user_enrichment(self):
        """Enrich user data using PydanticAI agent"""
        logger.info(f"User enrichment for {self.username}")

        # Get github metadata
        github_metadata = (
            self.data.githubUserMetadata.model_dump()
            if self.data.githubUserMetadata
            else {}
        )

        # Extract git authors and existing authors from metadata
        git_authors_data = []  # No git authors for user profiles
        existing_authors_data = []

        # Build existing author data using the new model structure
        if self.data.fullname or github_metadata.get("name"):
            author_data = {
                "name": self.data.fullname or github_metadata.get("name"),
                "orcid": github_metadata.get("orcid"),
                "affiliation": github_metadata.get("organizations", []),
            }
            existing_authors_data = [author_data]

        result = await enrich_users_from_dict(
            git_authors_data=git_authors_data,
            existing_authors_data=existing_authors_data,
            repository_url=f"https://github.com/{self.username}",
        )

        # Extract data and usage
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

        # Add enriched user data to response
        # Note: Currently we don't have a place to store enriched authors in GitHubUser model
        # This could be added as a field if needed in the future
        if user_enrichment is not None:
            logger.info(
                f"User enrichment completed with {len(user_enrichment.get('enrichedAuthors', []))} enriched authors",
            )
        else:
            logging.warning("User enrichment returned None, skipping author enrichment")

        logger.info(f"User enrichment completed for {self.username}")

    async def run_linked_entities_enrichment(self):
        """Enrich user with academic catalog relations (Infoscience, etc.)"""
        logger.info(f"Academic catalog enrichment for {self.username}")

        # Check if data exists before enrichment
        if self.data is None:
            logger.warning(
                f"Cannot enrich academic catalogs: no data available for {self.username}",
            )
            return

        try:
            # Extract user information for the enrichment
            github_metadata = (
                self.data.githubUserMetadata.model_dump()
                if self.data.githubUserMetadata
                else {}
            )

            full_name = self.data.fullname or github_metadata.get("name", "")
            bio = github_metadata.get("bio", "")
            organizations = github_metadata.get("organizations", [])

            result = await enrich_user_linked_entities(
                username=self.username,
                full_name=full_name,
                bio=bio,
                organizations=organizations,
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

            # Store the academic catalog relations
            if enrichment_data and hasattr(enrichment_data, "relations"):
                self.data.linkedEntities = enrichment_data.relations
                logger.info(
                    f"Stored {len(enrichment_data.relations)} academic catalog relations",
                )

        except Exception as e:
            logger.error(f"Academic catalog enrichment failed: {e}", exc_info=True)
            # Don't fail the entire analysis, just skip academic catalog enrichment
            return

    async def run_epfl_final_assessment(self):
        """Run final EPFL relationship assessment after all enrichments complete"""
        logger.info(f"Final EPFL assessment for {self.username}")

        # Check if data exists
        if self.data is None:
            logging.warning(
                f"Cannot run EPFL assessment: no data available for {self.username}",
            )
            return

        try:
            # Convert data to dict for assessment
            data_dict = self.data.model_dump()

            # Call the EPFL assessment agent
            result = await assess_epfl_relationship(
                data=data_dict,
                item_type="user",
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
                f"EPFL final assessment failed for {self.username}: {e}",
                exc_info=True,
            )
            # Don't fail the entire analysis, just log the error

    def run_validation(self) -> bool:
        """Validate the user data"""
        if self.data is None:
            logging.warning("No data to validate")
            return False
        else:
            self.data = GitHubUser.model_validate(self.data)
            logging.info(f"Data validation passed for {self.username}")
            return True

    def check_in_cache(self, api_type: str, cache_params: dict) -> bool:
        """Check if data exists in cache"""
        result = self.cache_manager.load_from_cache(api_type, cache_params)

        if result is not None:
            logging.info(f"Found cached data for {self.username}")
            return True
        else:
            logging.info(f"No cached data for {self.username}")
            return False

    def save_in_cache(self):
        """Save user data to cache"""
        if self.data is not None:
            self.cache_manager.cache.set(
                api_type="user",
                params={"username": self.username},
                response_data=self.data.model_dump_json(),
                ttl_days=365,  # Cache for 365 days
            )
            logging.info(f"Cached results for {self.username}")
        else:
            logging.warning(f"No data to cache for {self.username}")

    def load_from_cache(self, api_type: str, cache_params: dict):
        """Load user data from cache"""
        result = self.cache_manager.load_from_cache(api_type, cache_params)

        # Validate
        if isinstance(result, dict):
            result = GitHubUser.model_validate(result)
        elif isinstance(result, str):
            result = GitHubUser.model_validate_json(result)

        self.data = result

        logging.info(f"Loaded data from cache for {self.username}")

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
        Dump results in specified format: json, dict, or pydantic
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
        else:
            logging.error(f"Unsupported output type: {output_type}")
            return None

    ################################################################
    # Analysis
    ################################################################

    async def run_analysis(
        self,
        run_llm: bool = True,
        run_organization_enrichment: bool = False,
        run_user_enrichment: bool = False,
    ):
        """
        Run the full analysis pipeline with optional steps.
        Checks cache before running each step unless force_refresh is True.
        """
        # Track start time
        self.start_time = datetime.now()

        # Check if complete user analysis exists in cache
        cache_params = {"username": self.username}
        if not self.force_refresh and self.check_in_cache("user", cache_params):
            self.load_from_cache("user", cache_params)
            logging.info(f"Loaded complete analysis from cache for {self.username}")
            self.analysis_successful = True
            self.end_time = datetime.now()
            return

        # Run GitHub parsing
        logging.info(f"GitHub parsing for {self.username}")
        self.run_github_parsing()
        logging.info(f"GitHub parsing completed for {self.username}")

        # Run LLM analysis
        if run_llm:
            logging.info(f"LLM analysis for {self.username}")
            await self.run_llm_analysis()
            logging.info(f"LLM analysis completed for {self.username}")

        # Run organization enrichment
        if run_organization_enrichment:
            logging.info(f"Organization enrichment for {self.username}")
            await self.run_organization_enrichment()
            logging.info(f"Organization enrichment completed for {self.username}")

        # Run user enrichment
        if run_user_enrichment:
            logging.info(f"User enrichment for {self.username}")
            await self.run_user_enrichment()
            logging.info(f"User enrichment completed for {self.username}")

        # Run academic catalog enrichment
        if self.data is not None:
            logging.info(f"Academic catalog enrichment for {self.username}")
            await self.run_linked_entities_enrichment()
            logging.info(f"Academic catalog enrichment completed for {self.username}")

        # Run final EPFL assessment after all enrichments complete
        if self.data is not None:
            logging.info(f"Final EPFL assessment for {self.username}")
            await self.run_epfl_final_assessment()
            logging.info(f"Final EPFL assessment completed for {self.username}")

        # Validate and cache if we have data
        if self.data is not None:
            self.run_validation()
            self.save_in_cache()
            self.analysis_successful = True
        else:
            logging.error(f"Analysis failed for {self.username}: no data generated")
            self.analysis_successful = False

        # Track end time
        self.end_time = datetime.now()

        # Log duration
        if self.start_time and self.end_time:
            duration = (self.end_time - self.start_time).total_seconds()
            logging.info(f"Analysis completed in {duration:.2f} seconds")
