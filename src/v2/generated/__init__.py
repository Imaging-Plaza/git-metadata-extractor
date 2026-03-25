from src.v2._compat import warn_legacy_import
from src.v2.schema.models.strict import (
    ArticleModel,
    ContributionModel,
    MembershipModel,
    OrganizationModel,
    PersonModel,
    RepositoryModel,
)

warn_legacy_import("src.v2.generated", "src.v2.schema.models")

__all__ = [
    "ArticleModel",
    "ContributionModel",
    "MembershipModel",
    "OrganizationModel",
    "PersonModel",
    "RepositoryModel",
]
