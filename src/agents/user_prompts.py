from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .user_enrichment import UserAnalysisContext

user_enrichment_agent_system_prompt = """
You are an expert at identifying and enriching author/user information from software repository metadata.

Your task is to analyze:
1. Git author information (name, email, commit history)
2. Existing ORCID records and affiliations
3. Temporal patterns of contributions (commit dates)
4. Email domains to infer institutional affiliations

For each author you analyze:
- Use the search_orcid tool to find ORCID records if not already available
- Use the search_web tool (DuckDuckGo) to find additional information about the author's affiliations
- Analyze email domains to infer institutional affiliations
- Look at commit dates to understand temporal affiliation patterns
- Identify both current and historical affiliations
- **Provide a confidence score (0.0 to 1.0)** for the enriched information based on:
  * Quality and completeness of sources (ORCID = high, institutional email = high, web search = moderate)
  * Consistency across multiple sources
  * Temporal alignment between commit dates and known affiliation periods
  * Amount and recency of contribution to the repository

Pay special attention to:
- Different name variations (e.g., "John Smith", "J. Smith", "Smith, John")
- Institutional email domains (e.g., @epfl.ch, @ethz.ch, @university.edu)
- Affiliation changes over time
- ORCID affiliation start/end dates aligned with commit patterns
- Active vs. historical contributors

For affiliation history:
- Extract temporal information when available (start/end dates)
- Align affiliation periods with commit activity
- Identify transitions between institutions
- Note if an author's commits align with specific affiliation periods

Confidence Scoring Guidelines:
- 0.9-1.0: Strong evidence (ORCID + institutional email + recent activity)
- 0.7-0.89: Good evidence (ORCID or institutional email + significant commits)
- 0.5-0.69: Moderate evidence (partial information + some commits)
- 0.3-0.49: Weak evidence (limited information or old/few commits)
- 0.0-0.29: Very weak or speculative evidence

Provide a summary that:
- Highlights the diversity of affiliations
- Identifies main contributing institutions
- Notes temporal patterns (e.g., "primarily EPFL authors from 2020-2023")
- Mentions any interesting collaboration patterns

Be thorough and use the tools available to you to gather and verify author information."""


def get_user_enrichment_agent_prompt(repository_url: str, context: UserAnalysisContext):
    prompt = f"""Analyze the following repository authors and enrich their information, particularly their affiliations.

    Repository: {repository_url}

    Git Authors (with emails and commit history):
    {
        json.dumps(
            [
                {
                    "name": a.name,
                    "email": a.email,
                    "commits": {
                        "total": a.commits.total if a.commits else 0,
                        "firstCommitDate": str(a.commits.firstCommitDate)
                        if a.commits and a.commits.firstCommitDate
                        else None,
                        "lastCommitDate": str(a.commits.lastCommitDate)
                        if a.commits and a.commits.lastCommitDate
                        else None,
                    },
                }
                for a in context.git_authors
            ],
            indent=2,
        )
    }

    Existing Author Information (from ORCID):
    {
        json.dumps(
            [
                {
                    "name": a.name,
                    "orcidId": str(a.orcidId) if a.orcidId else None,
                    "affiliation": a.affiliation,
                }
                for a in context.existing_authors
            ],
            indent=2,
        )
    }

    Please:
    1. For each git author, analyze their email domain to infer affiliations
    2. Match git authors with existing ORCID records when possible
    3. Use search_orcid tool to find ORCID records for authors without them
    4. Use search_web tool to find additional information about authors and their affiliations
    5. Examine commit patterns (first/last commit dates) to understand temporal affiliations
    6. Identify both current and historical affiliations for each author
    7. Create a comprehensive affiliation history when temporal data is available
    8. Provide a confidence score (0.0 to 1.0) for each enriched author based on:
    - Quality of sources (ORCID, institutional email, web search)
    - Consistency across sources
    - Amount and recency of contributions
    - Temporal alignment between commits and affiliation periods
    9. Provide an overall summary of author affiliations and patterns

    Focus on understanding:
    - Who are the main contributors and where are they affiliated?
    - Are there patterns in affiliations over time?
    - Which institutions are most represented?
    - Are there active vs. historical contributors?
    """

    return prompt


def get_general_user_agent_prompt(username: str, user_data: str):
    general_user_agent_prompt = f"""Analyze the following GitHub user profile and extract comprehensive metadata.

    Username: {username}

    User Profile Data:
    {user_data}

    CRITICAL: The README content contains explicit position information. Look for this exact text:
    "Currently, I am working as a **Data Engineer** at the **Swiss Data Science Center** at **EPFL**."

    Please provide a detailed analysis in JSON format with the following fields:
    - "discipline": List of scientific disciplines (e.g., ["Biology", "Computer Science", "Data Science"])
    - "disciplineJustification": List of justifications for each discipline
    - "position": List of professional positions/roles (e.g., ["Data Engineer", "Research Scientist", "Software Developer"])
    - "positionJustification": List of justifications for each position

    IMPORTANT: Extract position information from ALL available sources:
    - Company field: "{user_data.get('company', 'N/A')}"
    - Bio content: "{user_data.get('bio', 'N/A')}"
    - README content: "{user_data.get('readme_content', 'N/A')[:500]}..." (truncated)
    - Organization affiliations: {user_data.get('organizations', [])}
    - ORCID activities: {user_data.get('orcid_activities', 'N/A')}

    Look for phrases like:
    - "I am working as a [POSITION]"
    - "Currently working as [POSITION]"
    - "Data Engineer", "Research Scientist", "Software Developer", etc.
    - Job titles in README content
    - Current employment status

    The README explicitly states: "Currently, I am working as a **Data Engineer**" - this should be extracted as position: ["Data Engineer"]

    Return valid JSON only with all four fields populated.
    """

    return general_user_agent_prompt
