import logging

from ..agents.organization_enrichment import enrich_organizations_from_dict
from ..agents.user_enrichment import enrich_users_from_dict
from ..cache.cache_manager import CacheManager, get_cache_manager
from ..data_models import GitHubUser
from ..llm.genai_model import llm_request_user_infos
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
            relatedToOrganization=user_data_dict.get("organizations", []),
            # Enrichment fields (will be populated by analysis steps)
            relatedToOrganizationsROR=[],
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
        # llm_input_data = {
        #     "username": self.username,
        #     "name": self._github_metadata.get("name"),
        #     "bio": self._github_metadata.get("bio"),
        #     "company": self._github_metadata.get("company"),
        #     "location": self._github_metadata.get("location"),
        #     "organizations": self._github_metadata.get("organizations", []),
        #     "orcid": self._github_metadata.get("orcid"),
        #     "orcid_activities": self._github_metadata.get("orcid_activities"),
        #     "readme_content": self._github_metadata.get("readme_content"),
        #     "public_repos": self._github_metadata.get("public_repos"),
        #     "followers": self._github_metadata.get("followers"),
        #     "following": self._github_metadata.get("following"),
        # }
        # TODO: Why don't we provide all the data?

        try:
            # Call LLM to analyze user profile
            llm_result = await llm_request_user_infos(
                username=self.username,
                user_data=llm_input_data,  # Here
                output_format="json",
                max_tokens=20000,
            )

            # Update self.data with LLM results
            if llm_result and isinstance(llm_result, dict):
                logger.info(f"LLM result keys: {list(llm_result.keys())}")
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

        # Format data for organization enrichment agent
        enrichment_data = {
            "gitAuthors": [],  # No git authors for user profiles
            "author": [],  # Will be populated with user's ORCID data if available
            "relatedToOrganizations": self._github_metadata.get("organizations", []),
            "relatedToOrganizationJustification": [],
            "relatedToEPFL": None,
            "relatedToEPFLJustification": None,
            # Include LLM analysis results to preserve them
            "discipline": self.data.discipline or [],
            "disciplineJustification": self.data.disciplineJustification or [],
            "position": self.data.position or [],
            "positionJustification": self.data.positionJustification or [],
        }

        # Add user as author if we have ORCID data
        if self._github_metadata.get("orcid"):
            author_data = {
                "name": self._github_metadata.get("name")
                or self._github_metadata.get("fullname"),
                "orcidId": self._github_metadata.get("orcid"),
                "affiliation": self._github_metadata.get("organizations", []),
            }
            enrichment_data["author"] = [author_data]

        organization_enrichment = await enrich_organizations_from_dict(
            enrichment_data,
            f"https://github.com/{self.username}",
        )

        # organization_enrichment is an OrganizationEnrichmentResult, not a dict
        enriched_orgs = organization_enrichment.organizations  # Direct attribute access

        # Safely handle relatedToOrganizations list
        related_orgs = getattr(self.data, "relatedToOrganization", None)
        if related_orgs is None:
            related_orgs = []
            self.data.relatedToOrganization = related_orgs
        for org in enriched_orgs:
            legal_name = (
                org.legalName
            )  # Direct attribute access, org is already Organization
            if legal_name:
                related_orgs.append(legal_name)

        # Safely handle relatedToOrganizationsROR list
        related_orgs_ror = getattr(self.data, "relatedToOrganizationsROR", None)
        if related_orgs_ror is None:
            related_orgs_ror = []
            self.data.relatedToOrganizationsROR = related_orgs_ror
        related_orgs_ror.extend(
            enriched_orgs,
        )  # enriched_orgs already contains Organization instances

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

        # Note: Organization enrichment does NOT return discipline/position fields
        # so we preserve the LLM analysis results
        logger.info(f"Organization enrichment completed for {self.username}")

    async def run_user_enrichment(self):
        """Enrich user data using PydanticAI agent"""
        logger.info(f"User enrichment for {self.username}")

        # Extract git authors and existing authors from metadata
        git_authors_data = []  # No git authors for user profiles
        existing_authors_data = []

        # Build existing author data using the new model structure
        if self._github_metadata.get("fullname") or self._github_metadata.get("name"):
            author_data = {
                "name": self._github_metadata.get("fullname")
                or self._github_metadata.get("name"),
                "orcidId": self._github_metadata.get("orcid"),
                "affiliation": self._github_metadata.get("organizations", []),
            }
            existing_authors_data = [author_data]

        user_enrichment = await enrich_users_from_dict(
            git_authors_data=git_authors_data,
            existing_authors_data=existing_authors_data,
            repository_url=f"https://github.com/{self.username}",
        )

        # Add enriched user data to response
        if user_enrichment is not None:
            self._github_metadata["enrichedAuthors"] = user_enrichment.get(
                "enrichedAuthors",
                [],
            )
            self._github_metadata["authorEnrichmentSummary"] = user_enrichment.get(
                "summary",
                "",
            )
        else:
            logging.warning("User enrichment returned None, skipping author enrichment")

        logger.info(f"User enrichment completed for {self.username}")

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
                ttl_days=30,  # Cache for 30 days
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
        # Check if complete user analysis exists in cache
        cache_params = {"username": self.username}
        if not self.force_refresh and self.check_in_cache("user", cache_params):
            self.load_from_cache("user", cache_params)
            logging.info(f"Loaded complete analysis from cache for {self.username}")
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

        self.run_validation()
        self.save_in_cache()
