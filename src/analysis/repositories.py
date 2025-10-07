import logging

from ..agents.organization_enrichment import enrich_organizations_from_dict
from ..agents.user_enrichment import enrich_users_from_dict
from ..cache.cache_manager import CacheManager, get_cache_manager
from ..data_models import SoftwareSourceCode
from ..gimie_utils.gimie_methods import extract_gimie
from ..llm.genai_model import llm_request_repo_infos
from ..utils.utils import enrich_authors_with_orcid

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


class Repository:
    def __init__(self, full_path: str, force_refresh: bool = False):
        self.full_path: str = full_path
        self.data: SoftwareSourceCode = None
        self.gimie = None
        self.log: list[str] = []
        self.cache_manager: CacheManager = get_cache_manager()
        self.force_refresh: bool = force_refresh

        # TODO: Check if a compatible URL was provided

    def run_gimie_analysis(self):
        def fetch_gimie_data():
            return extract_gimie(self.full_path, format="json-ld")

        # Get GIMIE data (cached separately with 1-day TTL)
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
        # Add parsing timestamp
        # llm_result["parseTimestamp"] = datetime.now().strftime("%Y-%m-%dT%H:%M")

        llm_data = await llm_request_repo_infos(
            str(self.full_path),
            gimie_output=self.gimie,
            output_format="json",
            max_tokens=20000,
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
        llm_result = enrich_authors_with_orcid(self.data)

        if isinstance(llm_result, SoftwareSourceCode):
            self.data = llm_result
        else:
            logging.warning(f"Author enrichment failed for {self.full_path}")

    async def run_organization_enrichment(self):
        logger.info(f"Organization enrichment for {self.full_path}")

        organization_enrichment = await enrich_organizations_from_dict(
            self.data.model_dump(),
            self.full_path,
        )

        # organization_enrichment is an OrganizationEnrichmentResult, not a dict
        enriched_orgs = organization_enrichment.organizations  # Direct attribute access

        # Safely handle relatedToOrganizations list
        related_orgs = getattr(self.data, "relatedToOrganizations", None)
        if related_orgs is None:
            related_orgs = []
            self.data.relatedToOrganizations = related_orgs
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

        git_authors_data = getattr(self.data, "gitAuthors", [])
        existing_authors_data = getattr(self.data, "author", [])

        user_enrichment = await enrich_users_from_dict(
            git_authors_data=git_authors_data,
            existing_authors_data=existing_authors_data,
            repository_url=self.full_path,
        )
        # This method should validate and return a compatible object

        # Safely extend authors list
        authors_list = getattr(self.data, "author", [])
        authors_list.extend(user_enrichment.get("enrichedAuthors", []))
        self.data.author = authors_list

        # llm_result["authorEnrichmentSummary"] = user_enrichment.get(
        #     "summary",
        #     "",
        # )

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
                ttl_days=30,  # Cache for 30 days
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
        # Check if complete repository analysis exists in cache
        cache_params = {"full_path": self.full_path}
        if not self.force_refresh and self.check_in_cache("repository", cache_params):
            self.load_from_cache("repository", cache_params)
            logging.info(f"Loaded complete analysis from cache for {self.full_path}")
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
            self.run_authors_enrichment()
            logging.info(f"LLM analysis completed for {self.full_path}")

        # Run user enrichment
        if run_user_enrichment:
            logging.info(f"User enrichment for {self.full_path}")
            await self.run_user_enrichment()
            logging.info(f"User enrichment completed for {self.full_path}")

        # Run organization enrichment
        if run_organization_enrichment:
            logging.info(f"Organization enrichment for {self.full_path}")
            await self.run_organization_enrichment()
            logging.info(f"Organization enrichment completed for {self.full_path}")

        self.run_validation()
        self.save_in_cache()
