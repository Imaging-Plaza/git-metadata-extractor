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
                    "orcid": str(a.orcid) if a.orcid else None,
                    "affiliations": a.affiliations,
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

    Please provide a detailed analysis in JSON format with the following fields:
    - "relatedToOrganization": List of organizations the user is affiliated with
    - "relatedToOrganizationJustification": List of justifications for each organization
    - "discipline": List of scientific disciplines
    - "disciplineJustification": List of justifications for each discipline
    - "position": List of professional positions/roles
    - "positionJustification": List of justifications for each position

    IMPORTANT: Extract organization and position information ONLY from the actual data provided:
    - Company field: "{user_data.get('company', 'N/A')}"
    - Bio content: "{user_data.get('bio', 'N/A')}"
    - README content: "{user_data.get('readme_content', 'N/A')[:500]}..." (truncated)
    - Organization affiliations: {user_data.get('organizations', [])}
    - ORCID activities: {user_data.get('orcid_activities', 'N/A')}

    EXTRACTION GUIDELINES:
    
    **For Positions:**
    - Look for explicit statements about current or past roles in the bio, company field, or README
    - Look for phrases like "I am working as", "Currently working as", "Software Engineer at", etc.
    - ONLY extract positions that are EXPLICITLY mentioned in the data
    - DO NOT infer or assume positions that are not stated
    
    **For Organizations:**
    - Look for company/employer information in the bio, company field, and README
    - Check GitHub organizations the user is a member of (institutions, universities, companies)
    - Include both primary organizations (e.g., "EPFL") and sub-units (e.g., "Swiss Data Science Center") ONLY if mentioned
    - Add EPFL to the list ONLY if the user explicitly mentions affiliation with an EPFL lab/center or has @epfl.ch email
    - DO NOT add organizations that are not explicitly mentioned or clearly indicated
    
    **For Disciplines:**
    - Infer from the user's bio, projects, repositories, and stated roles
    - Base on technical skills, research areas, or explicit statements
    
    **Critical Rules:**
    - For each field, provide a clear justification that quotes or references the actual source data
    - If a field cannot be determined from the available data, return an empty list []
    - DO NOT hallucinate or fabricate information
    - DO NOT use example data as if it were real
    - ONLY extract information that is present in the provided user data

    Return valid JSON only with all SIX fields populated (relatedToOrganization, relatedToOrganizationJustification, discipline, disciplineJustification, position, positionJustification).
    """

    return general_user_agent_prompt
