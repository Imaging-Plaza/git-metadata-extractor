import logging
from datetime import datetime

from ..agents import llm_request_org_infos
from ..agents.epfl_assessment import assess_epfl_relationship
from ..agents.organization_enrichment import enrich_organizations_from_dict
from ..cache.cache_manager import CacheManager, get_cache_manager
from ..data_models import GitHubOrganization
from ..parsers import parse_github_organization

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Organization:
    def __init__(self, org_name: str, force_refresh: bool = False):
        self.org_name: str = org_name
        self.data: GitHubOrganization = None
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
        """Parse GitHub organization metadata and convert to GitHubOrganization model"""
        logger.info(f"Parsing GitHub organization data for {self.org_name}")

        # Parse GitHub organization metadata
        github_metadata = parse_github_organization(self.org_name)

        # Convert GitHubOrganizationMetadata to dict and merge into self.data
        org_data_dict = github_metadata.model_dump()

        # Map GitHubOrganizationMetadata fields to GitHubOrganization model
        self.data = GitHubOrganization(
            # Basic fields
            name=org_data_dict.get("name"),
            githubOrganizationMetadata=github_metadata,
            # Enrichment fields (will be populated by analysis steps)
            organizationType=None,
            organizationTypeJustification=None,
            description=org_data_dict.get("description"),
            relatedToOrganization=[],
            relatedToOrganizationsROR=[],
            relatedToOrganizationJustification=[],
            discipline=[],
            disciplineJustification=[],
            relatedToEPFL=None,
            relatedToEPFLJustification=None,
            relatedToEPFLConfidence=None,
            infoscienceEntities=None,
        )

    async def run_llm_analysis(self):
        """Run LLM analysis to populate organizationType and discipline fields"""
        logger.info(f"LLM analysis for {self.org_name}")

        # Prepare data for LLM analysis
        github_metadata = self.data.githubOrganizationMetadata.model_dump() if self.data.githubOrganizationMetadata else {}
        llm_input_data = {
            "login": github_metadata.get("login"),
            "name": github_metadata.get("name"),
            "description": github_metadata.get("description"),
            "location": github_metadata.get("location"),
            "company": github_metadata.get("company"),
            "blog": github_metadata.get("blog"),
            "email": github_metadata.get("email"),
            "twitter_username": github_metadata.get("twitter_username"),
            "public_repos": github_metadata.get("public_repos"),
            "followers": github_metadata.get("followers"),
            "public_members": github_metadata.get("public_members", []),
            "repositories": github_metadata.get("repositories", []),
            "readme_content": github_metadata.get("readme_content"),
            "social_accounts": github_metadata.get("social_accounts", []),
            "pinned_repositories": github_metadata.get("pinned_repositories", []),
        }

        try:
            # Call LLM to analyze organization profile
            result = await llm_request_org_infos(
                org_name=self.org_name,
                org_data=llm_input_data,
                max_tokens=20000,
            )

            # Extract data and usage
            llm_result = result.get("data") if isinstance(result, dict) else result
            usage = result.get("usage") if isinstance(result, dict) else None
            
            # Accumulate official API-reported usage data
            if usage:
                self.total_input_tokens += usage.get("input_tokens", 0)
                self.total_output_tokens += usage.get("output_tokens", 0)
                logger.info(f"LLM analysis usage: {usage.get('input_tokens', 0)} input, {usage.get('output_tokens', 0)} output tokens")
            
            # Accumulate estimated tokens
            if usage and "estimated_input_tokens" in usage:
                self.estimated_input_tokens += usage.get("estimated_input_tokens", 0)
                self.estimated_output_tokens += usage.get("estimated_output_tokens", 0)
                logger.info(f"LLM analysis estimated: {usage.get('estimated_input_tokens', 0)} input, {usage.get('estimated_output_tokens', 0)} output tokens")

            # Update self.data with LLM results
            if llm_result and isinstance(llm_result, dict):
                logger.info(f"LLM result keys: {list(llm_result.keys())}")
                
                if llm_result.get("organizationType"):
                    self.data.organizationType = llm_result.get("organizationType")
                    logger.info(f"Set organizationType: {self.data.organizationType}")
                
                if llm_result.get("organizationTypeJustification"):
                    self.data.organizationTypeJustification = llm_result.get(
                        "organizationTypeJustification",
                    )
                    logger.info(
                        f"Set organizationTypeJustification: {self.data.organizationTypeJustification}",
                    )
                
                # Update description if LLM provided an enhanced one
                if llm_result.get("description") and not self.data.description:
                    self.data.description = llm_result.get("description")
                    logger.info(f"Set enhanced description: {self.data.description[:100]}...")
                
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
                
                # Set EPFL relationship from LLM analysis
                if "relatedToEPFL" in llm_result:
                    self.data.relatedToEPFL = llm_result.get("relatedToEPFL")
                    logger.info(f"Set relatedToEPFL: {self.data.relatedToEPFL}")
                
                if llm_result.get("relatedToEPFLJustification"):
                    self.data.relatedToEPFLJustification = llm_result.get(
                        "relatedToEPFLJustification",
                    )
                    logger.info(
                        f"Set relatedToEPFLJustification: {self.data.relatedToEPFLJustification}",
                    )
                
                if llm_result.get("relatedToEPFLConfidence") is not None:
                    self.data.relatedToEPFLConfidence = llm_result.get(
                        "relatedToEPFLConfidence",
                    )
                    logger.info(
                        f"Set relatedToEPFLConfidence: {self.data.relatedToEPFLConfidence}",
                    )
                
                # Set Infoscience entities if found
                if llm_result.get("infoscienceEntities"):
                    from ..data_models.repository import InfoscienceEntity
                    entities = llm_result.get("infoscienceEntities", [])
                    # Convert to InfoscienceEntity objects if they're dicts
                    entity_objects = []
                    for entity in entities:
                        if isinstance(entity, dict):
                            entity_objects.append(InfoscienceEntity(**entity))
                        else:
                            entity_objects.append(entity)
                    self.data.infoscienceEntities = entity_objects
                    logger.info(
                        f"Set infoscienceEntities: {len(self.data.infoscienceEntities)} entities found",
                    )

                logger.info(f"LLM analysis completed for {self.org_name}")
            else:
                logger.warning(
                    f"LLM analysis returned no results for {self.org_name}: {llm_result}",
                )

        except Exception as e:
            logger.error(f"LLM analysis failed for {self.org_name}: {e}")
            # Don't fail the entire process, just log the error

    async def run_organization_enrichment(self):
        """Enrich organization data using PydanticAI agent"""
        logger.info(f"Organization enrichment for {self.org_name}")

        # Get github metadata
        github_metadata = self.data.githubOrganizationMetadata.model_dump() if self.data.githubOrganizationMetadata else {}

        # For organization profiles, we need to provide the org's OWN information as context
        # Create a pseudo-author entry with the organization's information so the enrichment
        # agent has context about what organization to search for in ROR
        org_as_author = {
            "name": github_metadata.get("name") or self.org_name,
            "affiliation": [github_metadata.get("name") or self.org_name],
        }
        
        # Add ALL available context for accurate ROR matching
        if github_metadata.get("location"):
            org_as_author["affiliation"].append(f"Location: {github_metadata.get('location')}")
        if github_metadata.get("description"):
            org_as_author["affiliation"].append(f"Description: {github_metadata.get('description')}")
        if github_metadata.get("blog"):
            org_as_author["affiliation"].append(f"Website: {github_metadata.get('blog')}")
        if github_metadata.get("email"):
            org_as_author["affiliation"].append(f"Email: {github_metadata.get('email')}")
        if github_metadata.get("twitter_username"):
            org_as_author["affiliation"].append(f"Twitter: @{github_metadata.get('twitter_username')}")
        if github_metadata.get("readme_content"):
            # Include FULL README content for maximum context
            org_as_author["affiliation"].append(f"README: {github_metadata.get('readme_content')}")
        if github_metadata.get("public_members"):
            # Include member information
            members = github_metadata.get("public_members", [])
            if members:
                org_as_author["affiliation"].append(f"Public Members: {', '.join(members)}")
        if github_metadata.get("repositories"):
            # Include repository list (important for understanding org's work)
            repos = github_metadata.get("repositories", [])
            if repos:
                # Include all repositories as they indicate the org's focus
                org_as_author["affiliation"].append(f"Repositories: {', '.join(repos)}")
        if github_metadata.get("pinned_repositories"):
            # Include pinned repos as they're the most important
            pinned = github_metadata.get("pinned_repositories", [])
            if pinned:
                pinned_info = [f"{r.get('name')}: {r.get('description', 'No description')}" for r in pinned]
                org_as_author["affiliation"].append(f"Pinned Repositories: {'; '.join(pinned_info)}")

        # Format data for organization enrichment agent
        enrichment_data = {
            "gitAuthors": [],  # No git authors for organization profiles
            "author": [org_as_author],  # Pass org info as "author" for context
            "relatedToOrganizations": [github_metadata.get("name") or self.org_name],  # The org itself
            "relatedToOrganizationJustification": [],
            "relatedToEPFL": self.data.relatedToEPFL,
            "relatedToEPFLJustification": self.data.relatedToEPFLJustification,
            # Include LLM analysis results to preserve them
            "discipline": self.data.discipline or [],
            "disciplineJustification": self.data.disciplineJustification or [],
        }

        result = await enrich_organizations_from_dict(
            enrichment_data,
            f"https://github.com/{self.org_name}",
        )

        # Extract data and usage
        organization_enrichment = result.get("data") if isinstance(result, dict) else result
        usage = result.get("usage") if isinstance(result, dict) else None
        
        # Accumulate official API-reported usage data
        if usage:
            self.total_input_tokens += usage.get("input_tokens", 0)
            self.total_output_tokens += usage.get("output_tokens", 0)
            logger.info(f"Organization enrichment usage: {usage.get('input_tokens', 0)} input, {usage.get('output_tokens', 0)} output tokens")
        
        # Accumulate estimated tokens
        if usage and "estimated_input_tokens" in usage:
            self.estimated_input_tokens += usage.get("estimated_input_tokens", 0)
            self.estimated_output_tokens += usage.get("estimated_output_tokens", 0)

        # organization_enrichment is an OrganizationEnrichmentResult, not a dict
        enriched_orgs = organization_enrichment.organizations  # Direct attribute access

        # Safely handle relatedToOrganization list
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

        # For organization profiles, preserve LLM's EPFL assessment (which has full context)
        # Only update EPFL values if they weren't set by LLM analysis
        # The enrichment agent is designed for repository analysis, not org profiles
        if self.data.relatedToEPFL is None and organization_enrichment.relatedToEPFL is not None:
            self.data.relatedToEPFL = organization_enrichment.relatedToEPFL
            logger.info(f"Set relatedToEPFL from enrichment: {self.data.relatedToEPFL}")
        else:
            logger.info(f"Preserving LLM's relatedToEPFL: {self.data.relatedToEPFL}")
            
        if self.data.relatedToEPFLJustification is None and organization_enrichment.relatedToEPFLJustification is not None:
            self.data.relatedToEPFLJustification = (
                organization_enrichment.relatedToEPFLJustification
            )
            logger.info(f"Set relatedToEPFLJustification from enrichment")
        else:
            logger.info(f"Preserving LLM's relatedToEPFLJustification")
            
        if self.data.relatedToEPFLConfidence is None and organization_enrichment.relatedToEPFLConfidence is not None:
            self.data.relatedToEPFLConfidence = (
                organization_enrichment.relatedToEPFLConfidence
            )
            logger.info(f"Set relatedToEPFLConfidence from enrichment: {self.data.relatedToEPFLConfidence}")
        else:
            logger.info(f"Preserving LLM's relatedToEPFLConfidence: {self.data.relatedToEPFLConfidence}")

        logger.info(f"Organization enrichment completed for {self.org_name}")

    async def run_epfl_final_assessment(self):
        """Run final EPFL relationship assessment after all enrichments complete"""
        logger.info(f"Final EPFL assessment for {self.org_name}")
        
        # Check if data exists
        if self.data is None:
            logging.warning(f"Cannot run EPFL assessment: no data available for {self.org_name}")
            return
        
        try:
            # Convert data to dict for assessment
            data_dict = self.data.model_dump()
            
            # Call the EPFL assessment agent
            result = await assess_epfl_relationship(
                data=data_dict,
                item_type="organization",
            )
            
            # Extract assessment and usage
            assessment = result.get("data") if isinstance(result, dict) else result
            usage = result.get("usage") if isinstance(result, dict) else None
            
            # Accumulate token usage
            if usage:
                self.total_input_tokens += usage.get("input_tokens", 0)
                self.total_output_tokens += usage.get("output_tokens", 0)
                logger.info(f"EPFL assessment usage: {usage.get('input_tokens', 0)} input, {usage.get('output_tokens', 0)} output tokens")
            
            # Update data with final assessment (overwrite previous values)
            self.data.relatedToEPFL = assessment.relatedToEPFL
            self.data.relatedToEPFLConfidence = assessment.relatedToEPFLConfidence
            self.data.relatedToEPFLJustification = assessment.relatedToEPFLJustification
            
            logger.info(f"Final EPFL assessment: relatedToEPFL={assessment.relatedToEPFL}, "
                       f"confidence={assessment.relatedToEPFLConfidence:.2f}")
            logger.info(f"Justification: {assessment.relatedToEPFLJustification[:200]}...")
            
        except Exception as e:
            logger.error(f"EPFL final assessment failed for {self.org_name}: {e}", exc_info=True)
            # Don't fail the entire analysis, just log the error

    def run_validation(self) -> bool:
        """Validate the organization data"""
        if self.data is None:
            logging.warning("No data to validate")
            return False
        else:
            self.data = GitHubOrganization.model_validate(self.data)
            logging.info(f"Data validation passed for {self.org_name}")
            return True

    def check_in_cache(self, api_type: str, cache_params: dict) -> bool:
        """Check if data exists in cache"""
        result = self.cache_manager.load_from_cache(api_type, cache_params)

        if result is not None:
            logging.info(f"Found cached data for {self.org_name}")
            return True
        else:
            logging.info(f"No cached data for {self.org_name}")
            return False

    def save_in_cache(self):
        """Save organization data to cache"""
        if self.data is not None:
            self.cache_manager.cache.set(
                api_type="organization",
                params={"org_name": self.org_name},
                response_data=self.data.model_dump_json(),
                ttl_days=365,  # Cache for 365 days
            )
            logging.info(f"Cached results for {self.org_name}")
        else:
            logging.warning(f"No data to cache for {self.org_name}")

    def load_from_cache(self, api_type: str, cache_params: dict):
        """Load organization data from cache"""
        result = self.cache_manager.load_from_cache(api_type, cache_params)

        # Validate
        if isinstance(result, dict):
            result = GitHubOrganization.model_validate(result)
        elif isinstance(result, str):
            result = GitHubOrganization.model_validate_json(result)

        self.data = result

        logging.info(f"Loaded data from cache for {self.org_name}")

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
            "estimated_total_tokens": self.estimated_input_tokens + self.estimated_output_tokens,
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
    ):
        """
        Run the full analysis pipeline with optional steps.
        Checks cache before running each step unless force_refresh is True.
        """
        # Track start time
        self.start_time = datetime.now()
        
        # Check if complete organization analysis exists in cache
        cache_params = {"org_name": self.org_name}
        if not self.force_refresh and self.check_in_cache("organization", cache_params):
            self.load_from_cache("organization", cache_params)
            logging.info(f"Loaded complete analysis from cache for {self.org_name}")
            self.analysis_successful = True
            self.end_time = datetime.now()
            return

        # Run GitHub parsing
        logging.info(f"GitHub parsing for {self.org_name}")
        self.run_github_parsing()
        logging.info(f"GitHub parsing completed for {self.org_name}")

        # Run LLM analysis
        if run_llm:
            logging.info(f"LLM analysis for {self.org_name}")
            await self.run_llm_analysis()
            logging.info(f"LLM analysis completed for {self.org_name}")

        # Run organization enrichment
        if run_organization_enrichment:
            logging.info(f"Organization enrichment for {self.org_name}")
            await self.run_organization_enrichment()
            logging.info(f"Organization enrichment completed for {self.org_name}")

        # Run final EPFL assessment after all enrichments complete
        if self.data is not None:
            logging.info(f"Final EPFL assessment for {self.org_name}")
            await self.run_epfl_final_assessment()
            logging.info(f"Final EPFL assessment completed for {self.org_name}")

        # Validate and cache if we have data
        if self.data is not None:
            self.run_validation()
            self.save_in_cache()
            self.analysis_successful = True
        else:
            logging.error(f"Analysis failed for {self.org_name}: no data generated")
            self.analysis_successful = False
        
        # Track end time
        self.end_time = datetime.now()
        
        # Log duration
        if self.start_time and self.end_time:
            duration = (self.end_time - self.start_time).total_seconds()
            logging.info(f"Analysis completed in {duration:.2f} seconds")

