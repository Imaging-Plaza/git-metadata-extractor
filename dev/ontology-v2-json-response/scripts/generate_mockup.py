#!/usr/bin/env python3
"""
Generate mock data for Open Pulse Ontology v2 test cases.

Creates a complete, cross-reference-consistent dataset for all 6 entity
shapes.  Every generated reference points to a valid entity, composite IDs
are correctly formed, and bidirectional relationships (owns / ownedBy) are
consistent.

The output mirrors the structure of a-001/:
    <case_dir>/
        pulse_PersonShape.json
        pulse_RepositoryShape.json
        pulse_OrganizationShape.json
        pulse_MembershipShape.json
        pulse_ContributionShape.json
        pulse_ArticleShape.json

Usage:
    # Default: 6 persons, 4 repos, 5 orgs, 3 articles -> a-002/
    python scripts/generate_mockup.py

    # Custom counts and output directory
    python scripts/generate_mockup.py --persons 10 --repos 6 --orgs 8 \
        --articles 4 --output a-003

    # Reproducible with a fixed seed
    python scripts/generate_mockup.py --seed 42

Requirements:
    Python 3.10+ (standard library only, no external dependencies)
"""

import argparse
import json
import random
import string
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
SCRIPT_DIR = Path(__file__).parent
BASE_DIR = SCRIPT_DIR.parent

# ---------------------------------------------------------------------------
# Constants / pools for realistic-ish data
# ---------------------------------------------------------------------------
FIRST_NAMES = [
    "Alice",
    "Bob",
    "Carlos",
    "Diana",
    "Elena",
    "François",
    "Giulia",
    "Hans",
    "Ines",
    "Jean",
    "Kenji",
    "Lara",
    "Marco",
    "Nadia",
    "Oscar",
    "Priya",
    "Quentin",
    "Rosa",
    "Stefan",
    "Tara",
    "Ugo",
    "Vera",
    "Wei",
    "Xena",
    "Yuki",
    "Zara",
]

LAST_NAMES = [
    "Müller",
    "Dupont",
    "Rossi",
    "Garcia",
    "Smith",
    "Chen",
    "Morin",
    "Weber",
    "Fischer",
    "Tanaka",
    "Kim",
    "Patel",
    "Vivar",
    "Novak",
    "Okafor",
    "Larsson",
    "Costa",
    "Nguyen",
    "Brown",
    "Yamamoto",
]

ORG_NAMES = [
    ("EPFL", "https://ror.org/02s376052", "pulse:University"),
    ("ETH Zürich", "https://ror.org/05a28rw58", "pulse:University"),
    ("CERN", "https://ror.org/01ggx4157", "pulse:ResearchInstitution"),
    ("MIT", "https://ror.org/042nb2s44", "pulse:University"),
    ("Max Planck Society", "https://ror.org/01hhn8329", "pulse:ResearchInstitution"),
    ("University of Oxford", "https://ror.org/052gg0110", "pulse:University"),
    ("INRIA", "https://ror.org/02kvxyf05", "pulse:ResearchInstitution"),
    ("Stanford University", "https://ror.org/00f54p054", "pulse:University"),
    ("Caltech", "https://ror.org/05dxps526", "pulse:University"),
    ("University of Tokyo", "https://ror.org/057zh3y96", "pulse:University"),
]

GITHUB_ORG_NAMES = [
    ("NumPy", "numpy", "pulse:SoftwareProject"),
    ("Pandas", "pandas-dev", "pulse:SoftwareProject"),
    ("Scikit-learn", "scikit-learn", "pulse:SoftwareProject"),
    ("Jupyter", "jupyter", "pulse:SoftwareProject"),
    ("Apache", "apache", "pulse:SoftwareProject"),
    ("TensorFlow", "tensorflow", "pulse:SoftwareProject"),
]

REPO_PREFIXES = [
    "data-toolkit",
    "analysis-engine",
    "ml-pipeline",
    "viz-framework",
    "geo-tools",
    "sim-runner",
    "stat-lib",
    "api-gateway",
    "etl-service",
    "doc-generator",
    "test-harness",
    "config-manager",
    "log-aggregator",
    "model-trainer",
    "image-processor",
    "text-parser",
]

REPO_TYPES = [
    "pulse:Software",
    "pulse:Data",
    "pulse:Documentation",
    "pulse:EducationalResource",
    "pulse:Other",
]

DISCIPLINES = [
    "wd:Q413",
    "wd:Q420",
    "wd:Q2329",
    "wd:Q333",
    "wd:Q8008",
    "wd:Q43035",
    "wd:Q83588",
    "wd:Q428691",
    "wd:Q101333",
    "wd:Q395",
    "wd:Q12483",
    "wd:Q2878974",
    "wd:Q21201",
    "wd:Q8134",
    "wd:Q8434",
    "wd:Q580689",
    "wd:Q188847",
    "wd:Q12271",
    "wd:Q77590",
]

PROGRAMMING_LANGUAGES = [
    "Python",
    "R",
    "Julia",
    "C++",
    "Rust",
    "TypeScript",
    "JavaScript",
    "Go",
    "Java",
    "MATLAB",
    "Fortran",
    "Scala",
]

SPDX_LICENSES = [
    "https://spdx.org/licenses/MIT.html",
    "https://spdx.org/licenses/Apache-2.0.html",
    "https://spdx.org/licenses/GPL-3.0-only.html",
    "https://spdx.org/licenses/BSD-3-Clause.html",
    "https://spdx.org/licenses/LGPL-2.1-only.html",
    "https://spdx.org/licenses/AGPL-3.0-only.html",
    "https://spdx.org/licenses/CC-BY-4.0.html",
]

ROLES = [
    "Research Engineer",
    "Postdoctoral Researcher",
    "PhD Student",
    "Software Engineer",
    "Professor",
    "Research Scientist",
    "Data Scientist",
    "Lab Manager",
    "Contributor",
    "Maintainer",
]

EPFL_UNITS = [
    "School of Architecture, Civil and Environmental Engineering",
    "School of Computer and Communication Sciences",
    "School of Engineering",
    "School of Basic Sciences",
    "School of Life Sciences",
]

ARTICLE_TITLES = [
    "A Novel Framework for {topic} Analysis",
    "Towards Scalable {topic} in Research Computing",
    "Deep Learning Approaches for {topic}",
    "Open Source Tools for {topic}: A Survey",
    "{topic}: Methods, Benchmarks, and Applications",
    "Reproducible {topic} with Modern Software Engineering",
    "Accelerating {topic} through High-Performance Computing",
    "Collaborative {topic} in Large-Scale Scientific Projects",
]

ARTICLE_TOPICS = [
    "Geospatial Data",
    "Structural Simulation",
    "Biomedical Imaging",
    "Climate Modeling",
    "Particle Physics",
    "Genomic Sequencing",
    "Materials Science",
    "Fluid Dynamics",
    "Network Analysis",
    "Time Series Forecasting",
    "Natural Language Processing",
]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_uuid4() -> str:
    return str(uuid.uuid4())


def make_orcid(rng: random.Random) -> str:
    groups = [f"{rng.randint(0,9999):04d}" for _ in range(4)]
    # Last character can be 0-9 or X
    last_char = rng.choice(string.digits + "X")
    groups[3] = groups[3][:3] + last_char
    return "-".join(groups)


def make_github_username(first: str, last: str, rng: random.Random) -> str:
    styles = [
        f"{first[0].lower()}{last.lower()}",
        f"{first.lower()}.{last.lower()}",
        f"{first.lower()}{rng.randint(1,99)}",
        f"{last.lower()}-{first[0].lower()}",
    ]
    name = rng.choice(styles)
    # Sanitise: remove non-ASCII, keep alphanumeric and dash/dot
    return "".join(
        c for c in name if c in string.ascii_lowercase + string.digits + "-."
    )


def make_doi(rng: random.Random) -> str:
    prefix = rng.choice(["10.1038", "10.1016", "10.1109", "10.1145", "10.5281"])
    suffix_chars = string.ascii_lowercase + string.digits
    suffix = "".join(rng.choice(suffix_chars) for _ in range(8))
    return f"{prefix}/{suffix}"


def make_date(rng: random.Random, start_year: int = 2019, end_year: int = 2025) -> str:
    start = datetime(start_year, 1, 1, tzinfo=timezone.utc)
    end = datetime(end_year, 12, 31, tzinfo=timezone.utc)
    delta = (end - start).days
    d = start + timedelta(days=rng.randint(0, delta))
    return d.strftime("%Y-%m-%d")


def make_datetime(
    rng: random.Random,
    start_year: int = 2019,
    end_year: int = 2025,
) -> str:
    start = datetime(start_year, 1, 1, tzinfo=timezone.utc)
    end = datetime(end_year, 12, 31, tzinfo=timezone.utc)
    delta = int((end - start).total_seconds())
    d = start + timedelta(seconds=rng.randint(0, delta))
    return d.strftime("%Y-%m-%dT%H:%M:%SZ")


def resolve_id(identifiers: dict, hierarchy: list[str]) -> tuple[str, str]:
    """Apply hierarchical ID resolution. Returns (id, idSource)."""
    for key in hierarchy:
        val = identifiers.get(key)
        if val is not None:
            return val, key
    return identifiers["uuid"], "uuid"


# ---------------------------------------------------------------------------
# Generators
# ---------------------------------------------------------------------------


def generate_organizations(
    n_institutional: int,
    n_github: int,
    rng: random.Random,
) -> list[dict]:
    """Generate Organization instances."""
    orgs = []
    available_institutions = list(ORG_NAMES)
    rng.shuffle(available_institutions)
    available_github = list(GITHUB_ORG_NAMES)
    rng.shuffle(available_github)

    # Pick a parent org for unit relationships
    parent_idx = 0

    for i in range(min(n_institutional, len(available_institutions))):
        name, ror, org_type = available_institutions[i]
        infoscience_id = make_uuid4() if rng.random() < 0.5 else None
        github_handle = None

        identifiers = {
            "pulse:ror": ror,
            "pulse:infoscienceOrganizationIdentifier": infoscience_id,
            "pulse:githubOrganizationHandle": github_handle,
            "uuid": make_uuid4(),
        }
        primary_id, id_source = resolve_id(
            identifiers,
            [
                "pulse:ror",
                "pulse:infoscienceOrganizationIdentifier",
                "pulse:githubOrganizationHandle",
                "uuid",
            ],
        )

        org = {
            "id": primary_id,
            "type": "org:Organization",
            "shacl": "pulse:OrganizationShape",
            "identifiers": identifiers,
            "idSource": id_source,
            "schema:name": name,
            "schema:identifier": ror,
            "pulse:githubOrganizationHandle": github_handle,
            "pulse:infoscienceOrganizationIdentifier": infoscience_id,
            "pulse:OrganizationType": org_type,
            "pulse:githubOrgFollowers": None,
            "org:hasUnit": [],
            "org:unitOf": None,
            "pulse:owns": [],
        }
        orgs.append(org)

    # Add unit relationships: create units for the first institution
    if len(orgs) >= 2 and n_institutional >= 2:
        parent = orgs[0]
        unit_count = min(2, len(orgs) - 1)
        for j in range(1, 1 + unit_count):
            if j < len(orgs) and rng.random() < 0.4:
                # Make this org a unit of the parent
                child_infoscience = make_uuid4()
                unit_name = rng.choice(EPFL_UNITS)

                unit_identifiers = {
                    "pulse:ror": None,
                    "pulse:infoscienceOrganizationIdentifier": child_infoscience,
                    "pulse:githubOrganizationHandle": None,
                    "uuid": make_uuid4(),
                }
                unit_id, unit_id_source = resolve_id(
                    unit_identifiers,
                    [
                        "pulse:ror",
                        "pulse:infoscienceOrganizationIdentifier",
                        "pulse:githubOrganizationHandle",
                        "uuid",
                    ],
                )

                unit = {
                    "id": unit_id,
                    "type": "org:Organization",
                    "shacl": "pulse:OrganizationShape",
                    "identifiers": unit_identifiers,
                    "idSource": unit_id_source,
                    "schema:name": f"{parent['schema:name']} - {unit_name}",
                    "schema:identifier": None,
                    "pulse:githubOrganizationHandle": None,
                    "pulse:infoscienceOrganizationIdentifier": child_infoscience,
                    "pulse:OrganizationType": "pulse:University",
                    "pulse:githubOrgFollowers": rng.randint(10, 500),
                    "org:hasUnit": [],
                    "org:unitOf": parent["id"],
                    "pulse:owns": [],
                }
                parent["org:hasUnit"].append(unit["id"])
                orgs.append(unit)

    # GitHub-based orgs (no ROR)
    for i in range(min(n_github, len(available_github))):
        name, handle, org_type = available_github[i]
        identifiers = {
            "pulse:ror": None,
            "pulse:infoscienceOrganizationIdentifier": None,
            "pulse:githubOrganizationHandle": handle,
            "uuid": make_uuid4(),
        }
        primary_id, id_source = resolve_id(
            identifiers,
            [
                "pulse:ror",
                "pulse:infoscienceOrganizationIdentifier",
                "pulse:githubOrganizationHandle",
                "uuid",
            ],
        )

        org = {
            "id": primary_id,
            "type": "org:Organization",
            "shacl": "pulse:OrganizationShape",
            "identifiers": identifiers,
            "idSource": id_source,
            "schema:name": name,
            "schema:identifier": None,
            "pulse:githubOrganizationHandle": handle,
            "pulse:infoscienceOrganizationIdentifier": None,
            "pulse:OrganizationType": org_type,
            "pulse:githubOrgFollowers": rng.randint(100, 5000),
            "org:hasUnit": [],
            "org:unitOf": None,
            "pulse:owns": [],
        }
        orgs.append(org)

    return orgs


def generate_persons(n: int, rng: random.Random) -> list[dict]:
    """Generate Person instances."""
    persons = []
    used_names = set()

    for _ in range(n):
        # Pick unique first+last combination
        for _attempt in range(50):
            first = rng.choice(FIRST_NAMES)
            last = rng.choice(LAST_NAMES)
            if (first, last) not in used_names:
                used_names.add((first, last))
                break

        github = make_github_username(first, last, rng)

        # Some persons have ORCID, some don't
        has_orcid = rng.random() < 0.7
        orcid = make_orcid(rng) if has_orcid else None

        has_infoscience = rng.random() < 0.4
        infoscience_id = make_uuid4() if has_infoscience else None

        identifiers = {
            "pulse:orcid": orcid,
            "pulse:infosciencePersonIdentifier": infoscience_id,
            "pulse:githubUsername": github,
            "uuid": make_uuid4(),
        }
        primary_id, id_source = resolve_id(
            identifiers,
            [
                "pulse:orcid",
                "pulse:infosciencePersonIdentifier",
                "pulse:githubUsername",
                "uuid",
            ],
        )

        domain = rng.choice(["epfl.ch", "ethz.ch", "cern.ch", "mit.edu", "ox.ac.uk"])
        email = f"{first.lower()}.{last.lower()}@{domain}"
        # Sanitise email
        email = "".join(
            c for c in email if c in string.ascii_lowercase + string.digits + "-.@"
        )

        has_url = rng.random() < 0.6
        url = (
            f"https://people.{domain}/{first.lower()}.{last.lower()}"
            if has_url
            else None
        )

        person = {
            "id": primary_id,
            "type": "schema:Person",
            "shacl": "pulse:PersonShape",
            "identifiers": identifiers,
            "idSource": id_source,
            "schema:name": f"{first} {last}",
            "schema:email": email,
            "schema:url": url,
            "pulse:githubUsername": github,
            "pulse:orcidIdentifier": orcid,
            "pulse:infosciencePersonIdentifier": infoscience_id,
            "org:hasMembership": [],
            "pulse:hasContribution": [],
            "pulse:owns": [],
        }
        persons.append(person)

    return persons


def generate_repositories(
    n: int,
    persons: list[dict],
    orgs: list[dict],
    rng: random.Random,
) -> list[dict]:
    """Generate Repository instances with valid author/owner references."""
    repos = []

    # Some repos owned by orgs with github handles, some by persons
    orgs_with_github = [
        o for o in orgs if o["identifiers"]["pulse:githubOrganizationHandle"]
    ]

    for i in range(n):
        repo_suffix = rng.choice(REPO_PREFIXES)

        # Decide owner: org or person
        if orgs_with_github and rng.random() < 0.6:
            owner_org = rng.choice(orgs_with_github)
            owner_handle = owner_org["identifiers"]["pulse:githubOrganizationHandle"]
            github_handle = f"{owner_handle}/{repo_suffix}"
            owned_by = owner_org["id"]
            owner_org["pulse:owns"].append(github_handle)
        else:
            owner_person = rng.choice(persons)
            github_user = owner_person["identifiers"]["pulse:githubUsername"]
            github_handle = f"{github_user}/{repo_suffix}"
            owned_by = owner_person["id"]
            owner_person["pulse:owns"].append(github_handle)

        has_doi = rng.random() < 0.3
        doi = make_doi(rng) if has_doi else None

        identifiers = {
            "pulse:githubRepositoryHandle": github_handle,
            "schema:identifier": doi,
            "uuid": make_uuid4(),
        }
        primary_id, id_source = resolve_id(
            identifiers,
            [
                "pulse:githubRepositoryHandle",
                "schema:identifier",
                "uuid",
            ],
        )

        # Pick 1-3 authors (always at least the owner if they're a person)
        author_ids = []
        for p in persons:
            if p["id"] == owned_by:
                author_ids.append(p["id"])
                break
        if not author_ids:
            author_ids.append(rng.choice(persons)["id"])

        extra_authors = rng.randint(0, min(2, len(persons) - 1))
        candidates = [p["id"] for p in persons if p["id"] not in author_ids]
        author_ids.extend(rng.sample(candidates, min(extra_authors, len(candidates))))

        repo = {
            "id": primary_id,
            "type": "schema:SoftwareSourceCode",
            "shacl": "pulse:RepositoryShape",
            "identifiers": identifiers,
            "idSource": id_source,
            "schema:name": repo_suffix.replace("-", " ").title(),
            "pulse:githubRepositoryHandle": github_handle,
            "pulse:repositoryType": rng.choice(REPO_TYPES),
            "pulse:discipline": rng.sample(DISCIPLINES, rng.randint(1, 3)),
            "schema:author": author_ids,
            "pulse:githubRepoStars": rng.randint(0, 500),
            "pulse:githubRepoForks": rng.randint(0, 100),
            "schema:dateCreated": make_datetime(rng, 2019, 2024),
            "schema:license": rng.choice(SPDX_LICENSES),
            "schema:citation": f"https://doi.org/{doi}" if doi else None,
            "schema:programmingLanguage": rng.sample(
                PROGRAMMING_LANGUAGES,
                rng.randint(1, 3),
            ),
            "pulse:ownedBy": owned_by,
            "pulse:isForkOf": None,
        }
        repos.append(repo)

    return repos


def generate_memberships(
    persons: list[dict],
    orgs: list[dict],
    rng: random.Random,
) -> list[dict]:
    """Generate Membership instances. Each person gets 1-2 memberships."""
    memberships = []

    for person in persons:
        n_memberships = rng.randint(1, min(2, len(orgs)))
        member_orgs = rng.sample(orgs, n_memberships)

        for org in member_orgs:
            composite = f"{person['id']}_{org['id']}"
            identifiers = {
                "pulse:composite": composite,
                "uuid": make_uuid4(),
            }

            start_date = make_date(rng, 2018, 2023)
            has_end = rng.random() < 0.25
            end_date = None
            if has_end:
                # Ensure end > start
                start_dt = datetime.strptime(start_date, "%Y-%m-%d")
                end_dt = start_dt + timedelta(days=rng.randint(180, 1500))
                end_date = end_dt.strftime("%Y-%m-%d")

            membership = {
                "id": composite,
                "type": "org:Membership",
                "shacl": "pulse:MembershipShape",
                "identifiers": identifiers,
                "idSource": "pulse:composite",
                "org:organization": org["id"],
                "org:role": rng.choice(ROLES),
                "time:hasBeginning": start_date,
                "time:hasEnd": end_date,
            }
            memberships.append(membership)
            person["org:hasMembership"].append(composite)

    return memberships


def generate_contributions(
    persons: list[dict],
    repos: list[dict],
    rng: random.Random,
) -> list[dict]:
    """Generate Contribution instances. Each person contributes to 1-3 repos."""
    contributions = []

    for person in persons:
        n_contributions = rng.randint(1, min(3, len(repos)))
        contrib_repos = rng.sample(repos, n_contributions)

        for repo in contrib_repos:
            composite = f"{person['id']}_{repo['id']}"
            identifiers = {
                "pulse:composite": composite,
                "uuid": make_uuid4(),
            }

            first_date = make_datetime(rng, 2020, 2023)
            # last date is after first date
            first_dt = datetime.strptime(first_date, "%Y-%m-%dT%H:%M:%SZ")
            last_dt = first_dt + timedelta(days=rng.randint(30, 800))
            last_date = last_dt.strftime("%Y-%m-%dT%H:%M:%SZ")

            contribution = {
                "id": composite,
                "type": "pulse:Contribution",
                "shacl": "pulse:ContributionShape",
                "identifiers": identifiers,
                "idSource": "pulse:composite",
                "pulse:contributionTo": repo["id"],
                "pulse:contributionCount": rng.randint(1, 500),
                "pulse:firstContributionDate": first_date,
                "pulse:lastContributionDate": last_date,
                "schema:author": person["id"],
            }
            contributions.append(contribution)
            person["pulse:hasContribution"].append(composite)

    return contributions


def generate_articles(
    n: int,
    persons: list[dict],
    orgs: list[dict],
    rng: random.Random,
) -> list[dict]:
    """Generate Article instances with valid author/org references."""
    articles = []
    institutional_orgs = [o for o in orgs if o["identifiers"]["pulse:ror"]]

    for _ in range(n):
        doi = make_doi(rng)
        has_infoscience = rng.random() < 0.6
        infoscience_id = make_uuid4() if has_infoscience else None

        identifiers = {
            "schema:identifier": doi,
            "pulse:infoscienceArticleIdentifier": infoscience_id,
            "uuid": make_uuid4(),
        }
        primary_id, id_source = resolve_id(
            identifiers,
            [
                "schema:identifier",
                "pulse:infoscienceArticleIdentifier",
                "uuid",
            ],
        )

        # 1-3 authors
        n_authors = rng.randint(1, min(3, len(persons)))
        author_persons = rng.sample(persons, n_authors)
        author_ids = [p["id"] for p in author_persons]

        # Source org (optional)
        source_org = None
        if institutional_orgs and rng.random() < 0.7:
            source_org = rng.choice(institutional_orgs)["id"]

        topic = rng.choice(ARTICLE_TOPICS)
        title_template = rng.choice(ARTICLE_TITLES)
        title = title_template.format(topic=topic)

        article = {
            "id": primary_id,
            "type": "schema:ScholarlyArticle",
            "shacl": "pulse:ArticleShape",
            "identifiers": identifiers,
            "idSource": id_source,
            "schema:name": title,
            "schema:identifier": doi,
            "schema:datePublished": make_date(rng, 2022, 2025),
            "schema:author": author_ids,
            "pulse:infoscienceArticleIdentifier": infoscience_id,
            "schema:sourceOrganization": source_org,
        }
        articles.append(article)

    return articles


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def generate_edge_cases(
    persons: list[dict],
    repos: list[dict],
    orgs: list[dict],
) -> dict[str, list[dict]]:
    """Generate one boundary-value edge-case entity per shape.

    Returns a dict keyed by shape name with lists of extra entities to append.
    Also mutates the edge-case person's cross-reference arrays.
    """

    # --- PersonShape: UUID-only (no ORCID, no GitHub, no infoscience) ---
    person_uuid = make_uuid4()
    edge_person = {
        "id": person_uuid,
        "type": "schema:Person",
        "shacl": "pulse:PersonShape",
        "identifiers": {
            "pulse:orcid": None,
            "pulse:infosciencePersonIdentifier": None,
            "pulse:githubUsername": None,
            "uuid": person_uuid,
        },
        "idSource": "uuid",
        "schema:name": "Edge Case Person",
        "schema:email": "edge.case@example.org",
        "schema:url": None,
        "pulse:githubUsername": None,
        "pulse:orcidIdentifier": None,
        "pulse:infosciencePersonIdentifier": None,
        "org:hasMembership": [],
        "pulse:hasContribution": [],
        "pulse:owns": [],
    }

    # --- OrganizationShape: GitHub-only, zero followers ---
    edge_org_handle = "edge-case-org"
    edge_org = {
        "id": edge_org_handle,
        "type": "org:Organization",
        "shacl": "pulse:OrganizationShape",
        "identifiers": {
            "pulse:ror": None,
            "pulse:infoscienceOrganizationIdentifier": None,
            "pulse:githubOrganizationHandle": edge_org_handle,
            "uuid": make_uuid4(),
        },
        "idSource": "pulse:githubOrganizationHandle",
        "schema:name": "Edge Case Org",
        "schema:identifier": None,
        "pulse:githubOrganizationHandle": edge_org_handle,
        "pulse:infoscienceOrganizationIdentifier": None,
        "pulse:OrganizationType": "pulse:SoftwareProject",
        "pulse:githubOrgFollowers": 0,
        "org:hasUnit": [],
        "org:unitOf": None,
        "pulse:owns": [],
    }

    # --- RepositoryShape: fork, zero stars/forks, no DOI/license, empty arrays ---
    fork_parent = repos[0]["id"] if repos else "unknown/repo"
    edge_repo_handle = f"{edge_person['identifiers']['uuid'][:8]}/edge-fork"
    # Use person ownership (not org)
    edge_person["pulse:owns"].append(edge_repo_handle)

    edge_repo = {
        "id": edge_repo_handle,
        "type": "schema:SoftwareSourceCode",
        "shacl": "pulse:RepositoryShape",
        "identifiers": {
            "pulse:githubRepositoryHandle": edge_repo_handle,
            "schema:identifier": None,
            "uuid": make_uuid4(),
        },
        "idSource": "pulse:githubRepositoryHandle",
        "schema:name": "Edge Fork",
        "pulse:githubRepositoryHandle": edge_repo_handle,
        "pulse:repositoryType": "pulse:Software",
        "pulse:discipline": [],
        "schema:author": [edge_person["id"]],
        "pulse:githubRepoStars": 0,
        "pulse:githubRepoForks": 0,
        "schema:dateCreated": None,
        "schema:license": None,
        "schema:citation": None,
        "schema:programmingLanguage": [],
        "pulse:ownedBy": edge_person["id"],
        "pulse:isForkOf": fork_parent,
    }

    # --- MembershipShape: no role, no dates ---
    membership_composite = f"{edge_person['id']}_{edge_org['id']}"
    edge_membership = {
        "id": membership_composite,
        "type": "org:Membership",
        "shacl": "pulse:MembershipShape",
        "identifiers": {
            "pulse:composite": membership_composite,
            "uuid": make_uuid4(),
        },
        "idSource": "pulse:composite",
        "org:organization": edge_org["id"],
        "org:role": None,
        "time:hasBeginning": None,
        "time:hasEnd": None,
    }
    edge_person["org:hasMembership"].append(membership_composite)

    # --- ContributionShape: zero count, no dates ---
    contrib_composite = f"{edge_person['id']}_{edge_repo['id']}"
    edge_contribution = {
        "id": contrib_composite,
        "type": "pulse:Contribution",
        "shacl": "pulse:ContributionShape",
        "identifiers": {
            "pulse:composite": contrib_composite,
            "uuid": make_uuid4(),
        },
        "idSource": "pulse:composite",
        "pulse:contributionTo": edge_repo["id"],
        "pulse:contributionCount": 0,
        "pulse:firstContributionDate": None,
        "pulse:lastContributionDate": None,
        "schema:author": edge_person["id"],
    }
    edge_person["pulse:hasContribution"].append(contrib_composite)

    # --- ArticleShape: no infoscience, no source org, single author ---
    edge_doi = "10.5281/edge00000001"
    edge_article = {
        "id": edge_doi,
        "type": "schema:ScholarlyArticle",
        "shacl": "pulse:ArticleShape",
        "identifiers": {
            "schema:identifier": edge_doi,
            "pulse:infoscienceArticleIdentifier": None,
            "uuid": make_uuid4(),
        },
        "idSource": "schema:identifier",
        "schema:name": "Edge Case: Boundary Value Testing in Ontology Schemas",
        "schema:identifier": edge_doi,
        "schema:datePublished": "2025-01-01",
        "schema:author": [edge_person["id"]],
        "pulse:infoscienceArticleIdentifier": None,
        "schema:sourceOrganization": None,
    }

    return {
        "persons": [edge_person],
        "repos": [edge_repo],
        "orgs": [edge_org],
        "memberships": [edge_membership],
        "contributions": [edge_contribution],
        "articles": [edge_article],
    }


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main():
    parser = argparse.ArgumentParser(
        description="Generate mock data for Open Pulse Ontology v2",
    )
    parser.add_argument(
        "--persons",
        type=int,
        default=6,
        help="Number of Person entities (default: 6)",
    )
    parser.add_argument(
        "--repos",
        type=int,
        default=4,
        help="Number of Repository entities (default: 4)",
    )
    parser.add_argument(
        "--orgs",
        type=int,
        default=4,
        help="Number of institutional Organizations (default: 4)",
    )
    parser.add_argument(
        "--github-orgs",
        type=int,
        default=2,
        help="Number of GitHub-based Organizations (default: 2)",
    )
    parser.add_argument(
        "--articles",
        type=int,
        default=3,
        help="Number of Article entities (default: 3)",
    )
    parser.add_argument(
        "--output",
        type=str,
        default="a-002",
        help="Output directory name under ontology-v2-json-response/ (default: a-002)",
    )
    parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Random seed for reproducibility",
    )
    parser.add_argument(
        "--edge-cases",
        action="store_true",
        default=False,
        help="Append boundary-value edge-case entities (UUID-only person, "
        "zero-count contribution, fork repo, etc.)",
    )

    args = parser.parse_args()
    rng = random.Random(args.seed)

    output_dir = BASE_DIR / args.output
    output_dir.mkdir(parents=True, exist_ok=True)

    print(f"Generating mock data with seed={args.seed}")
    print(f"Output: {output_dir}")
    print(
        f"Config: {args.persons} persons, {args.repos} repos, "
        f"{args.orgs}+{args.github_orgs} orgs, {args.articles} articles",
    )

    # Generation order matters for cross-references
    print("\n1. Generating organizations...")
    orgs = generate_organizations(args.orgs, args.github_orgs, rng)
    print(f"   {len(orgs)} organizations")

    print("2. Generating persons...")
    persons = generate_persons(args.persons, rng)
    print(f"   {len(persons)} persons")

    print("3. Generating repositories...")
    repos = generate_repositories(args.repos, persons, orgs, rng)
    print(f"   {len(repos)} repositories")

    print("4. Generating memberships...")
    memberships = generate_memberships(persons, orgs, rng)
    print(f"   {len(memberships)} memberships")

    print("5. Generating contributions...")
    contributions = generate_contributions(persons, repos, rng)
    print(f"   {len(contributions)} contributions")

    print("6. Generating articles...")
    articles = generate_articles(args.articles, persons, orgs, rng)
    print(f"   {len(articles)} articles")

    if args.edge_cases:
        print("\n7. Generating edge-case entities...")
        edge = generate_edge_cases(persons, repos, orgs)
        persons.extend(edge["persons"])
        repos.extend(edge["repos"])
        orgs.extend(edge["orgs"])
        memberships.extend(edge["memberships"])
        contributions.extend(edge["contributions"])
        articles.extend(edge["articles"])
        edge_total = sum(len(v) for v in edge.values())
        print(f"   {edge_total} edge-case entities added")

    # Write output files
    shape_data = {
        "pulse_PersonShape.json": persons,
        "pulse_RepositoryShape.json": repos,
        "pulse_OrganizationShape.json": orgs,
        "pulse_MembershipShape.json": memberships,
        "pulse_ContributionShape.json": contributions,
        "pulse_ArticleShape.json": articles,
    }

    print(f"\nWriting to {output_dir}/")
    for filename, data in shape_data.items():
        filepath = output_dir / filename
        with open(filepath, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2, ensure_ascii=False)
        print(f"  {filename}: {len(data)} instances")

    total = sum(len(d) for d in shape_data.values())
    print(f"\nTotal: {total} entities generated")
    print("Done!")


if __name__ == "__main__":
    main()
