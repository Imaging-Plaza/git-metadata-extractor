from __future__ import annotations

from enum import Enum
from typing import Self


class DisciplineV2(str, Enum):
    wikidata_uri: str

    def __new__(cls, identifier: str, wikidata_uri: str) -> Self:
        instance = str.__new__(cls, identifier)
        instance._value_ = identifier
        instance.wikidata_uri = wikidata_uri
        return instance

    MECHANICAL_ENGINEERING = ("wd:Q101333", "http://www.wikidata.org/entity/Q101333")
    GEOGRAPHY = ("wd:Q1071", "http://www.wikidata.org/entity/Q1071")
    COMMUNICATION_STUDIES = ("wd:Q11680831", "http://www.wikidata.org/entity/Q11680831")
    ARCHITECTURE = ("wd:Q12271", "http://www.wikidata.org/entity/Q12271")
    STATISTICS = ("wd:Q12483", "http://www.wikidata.org/entity/Q12483")
    INDUSTRIAL_AND_PRODUCTION_ENGINEERING = (
        "wd:Q18351432",
        "http://www.wikidata.org/entity/Q18351432",
    )
    ENVIRONMENTAL_SCIENCE = ("wd:Q188847", "http://www.wikidata.org/entity/Q188847")
    MILITARY_SCIENCE = ("wd:Q192386", "http://www.wikidata.org/entity/Q192386")
    SOCIOLOGY = ("wd:Q21201", "http://www.wikidata.org/entity/Q21201")
    SYSTEMS_SCIENCE_AND_ENGINEERING = ("wd:Q2167061", "http://www.wikidata.org/entity/Q2167061")
    CHEMISTRY = ("wd:Q2329", "http://www.wikidata.org/entity/Q2329")
    ANTHROPOLOGY = ("wd:Q23404", "http://www.wikidata.org/entity/Q23404")
    THEORETICAL_COMPUTER_SCIENCE = ("wd:Q2878974", "http://www.wikidata.org/entity/Q2878974")
    HISTORY = ("wd:Q309", "http://www.wikidata.org/entity/Q309")
    ASTRONOMY = ("wd:Q333", "http://www.wikidata.org/entity/Q333")
    ENERGY_ENGINEERING = ("wd:Q3353193", "http://www.wikidata.org/entity/Q3353193")
    SOCIAL_SCIENCES = ("wd:Q34749", "http://www.wikidata.org/entity/Q34749")
    AGRICULTURAL_AND_FOOD_SCIENCES = ("wd:Q3606845", "http://www.wikidata.org/entity/Q3606845")
    MATHEMATICS = ("wd:Q395", "http://www.wikidata.org/entity/Q395")
    PHYSICS = ("wd:Q413", "http://www.wikidata.org/entity/Q413")
    BIOLOGY = ("wd:Q420", "http://www.wikidata.org/entity/Q420")
    RESEARCH = ("wd:Q42240", "http://www.wikidata.org/entity/Q42240")
    COMPUTER_ENGINEERING = ("wd:Q428691", "http://www.wikidata.org/entity/Q428691")
    COMPUTER_SCIENCE = COMPUTER_ENGINEERING
    ELECTRICAL_ENGINEERING = ("wd:Q43035", "http://www.wikidata.org/entity/Q43035")
    BUSINESS = ("wd:Q4830453", "http://www.wikidata.org/entity/Q4830453")
    BIOLOGICAL_ENGINEERING = ("wd:Q580689", "http://www.wikidata.org/entity/Q580689")
    PHILOSOPHY = ("wd:Q5891", "http://www.wikidata.org/entity/Q5891")
    APPLIED_SCIENCES = ("wd:Q7112556", "http://www.wikidata.org/entity/Q7112556")
    POLITICS = ("wd:Q7163", "http://www.wikidata.org/entity/Q7163")
    ART = ("wd:Q735", "http://www.wikidata.org/entity/Q735")
    LAW = ("wd:Q7748", "http://www.wikidata.org/entity/Q7748")
    CIVIL_ENGINEERING = ("wd:Q77590", "http://www.wikidata.org/entity/Q77590")
    NATURAL_SCIENCES = ("wd:Q7991", "http://www.wikidata.org/entity/Q7991")
    EARTH_SCIENCE = ("wd:Q8008", "http://www.wikidata.org/entity/Q8008")
    HUMANITIES = ("wd:Q80083", "http://www.wikidata.org/entity/Q80083")
    LOGIC = ("wd:Q8078", "http://www.wikidata.org/entity/Q8078")
    ECONOMICS = ("wd:Q8134", "http://www.wikidata.org/entity/Q8134")
    LINGUISTICS = ("wd:Q8162", "http://www.wikidata.org/entity/Q8162")
    FORMAL_SCIENCES = ("wd:Q816264", "http://www.wikidata.org/entity/Q816264")
    LITERATURE = ("wd:Q8242", "http://www.wikidata.org/entity/Q8242")
    CHEMICAL_ENGINEERING = ("wd:Q83588", "http://www.wikidata.org/entity/Q83588")
    EDUCATION = ("wd:Q8434", "http://www.wikidata.org/entity/Q8434")
    HEALTH_SCIENCES = ("wd:Q843601", "http://www.wikidata.org/entity/Q843601")
    RELIGION = ("wd:Q9174", "http://www.wikidata.org/entity/Q9174")
    PSYCHOLOGY = ("wd:Q9418", "http://www.wikidata.org/entity/Q9418")
    INFORMATION_ENGINEERING = (
        "http://www.wikipedia.org/wiki/Information_engineering",
        "http://www.wikipedia.org/wiki/Information_engineering",
    )


class RepositoryTypeV2(str, Enum):
    SOFTWARE = "pulse:Software"
    EDUCATIONAL_RESOURCE = "pulse:EducationalResource"
    DOCUMENTATION = "pulse:Documentation"
    DATA = "pulse:Data"
    OTHER = "pulse:Other"


class OrganizationTypeV2(str, Enum):
    UNIVERSITY = "pulse:University"
    RESEARCH_INSTITUTION = "pulse:ResearchInstitution"
    GOVERNMENT_AGENCY = "pulse:GovernmentAgency"
    SOFTWARE_PROJECT = "pulse:SoftwareProject"
    PRIVATE_COMPANY = "pulse:PrivateCompany"
    NON_PROFIT_ORGANIZATION = "pulse:NonProfitOrganization"
    COMMUNITY_SPACE = "pulse:CommunitySpace"
    OTHER_ORGANIZATION_TYPE = "pulse:OtherOrganizationType"


__all__ = [
    "DisciplineV2",
    "OrganizationTypeV2",
    "RepositoryTypeV2",
]
