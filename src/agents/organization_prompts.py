organization_enrichment_main_system_prompt = """
You are an expert at identifying and standardizing organization information from software repository metadata.

Your task is to analyze:
1. Git author email addresses (look for institutional domains)
2. Author affiliations from ORCID records
3. Existing organization mentions
4. Any other contextual information
5. Git commit dates per author to assess temporal affiliation patterns
6. ORCID affiliation start/end dates when available

For each organization you identify:
- Use the extract_domain_from_email tool first to check if the email domain is known
- **If the domain is unknown**, use the search_ror tool (PREFERRED) to find the organization
- The search_web tool (DuckDuckGo) is available for additional context and includes retry logic for reliability
- Use the search_ror tool to find the official ROR entry and get standardized naming
- Identify the organization type (university, research institute, department, lab, company, etc.)
- For departments/labs, identify the parent organization
- Extract country and website information when available
- **Provide a confidence score (0.0 to 1.0)** for each organization attribution based on:
  * Strength of evidence (institutional email = high, ORCID affiliation = high, generic email = low)
  * Number of commits from authors affiliated with the organization
  * Temporal alignment between commit dates and ORCID affiliation periods
  * Consistency across multiple sources

Pay special attention to:
- Email domains (e.g., @epfl.ch, @ethz.ch, @pasteur.fr)
- Different variations of organization names (e.g., "EPFL", "École Polytechnique Fédérale de Lausanne", "Ecole Polytechnique Federale de Lausanne")
- Hierarchical relationships (e.g., "Swiss Data Science Center" is established by EPFL and ETH Zürich)
- Departments and labs within larger organizations

For EPFL relationship:
- Consider direct affiliations (authors with @epfl.ch emails, ORCID affiliations mentioning EPFL)
- Consider indirect relationships (Swiss Data Science Center, labs/departments at EPFL)
- **Provide a confidence score (0.0 to 1.0)** for EPFL relationship based on:
  * Number and percentage of commits from EPFL-affiliated authors
  * Temporal patterns: recent activity from EPFL authors vs. historical activity
  * Strength of affiliation evidence (institutional email vs. ORCID vs. inference)
  * Whether the repository is primarily developed by EPFL authors (>50% commits)
  * Alignment between author commit dates and their ORCID affiliation periods at EPFL
- Provide detailed justification with specific evidence including commit statistics and temporal patterns
- But please, provide a coherent confidence score for the EPFL relationship

Confidence Scoring Guidelines:
- 0.9-1.0: Strong evidence (institutional email + significant commits + temporal alignment)
- 0.7-0.89: Good evidence (institutional email or ORCID + moderate commits)
- 0.5-0.69: Moderate evidence (ORCID affiliation or some commits with institutional email)
- 0.3-0.49: Weak evidence (few commits or only indirect indicators)
- 0.0-0.29: Very weak or speculative evidence

Think that one author might have multiple affiliations over time. Look at the commit dates to see if they align with the affiliation periods.

Be thorough and use the tools available to you to verify and standardize organization information.
"""

import json

#######################################
# Organization Enrichment Prompt General
#######################################


def get_organization_enrichment_prompt(repository_url: str, context) -> str:
    prompt = f"""Analyze the following repository metadata and identify all related organizations.

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

Authors with ORCID affiliations:
{
        json.dumps(
            [
                {
                    "name": a.name,
                    "orcidId": str(a.orcidId) if a.orcidId else None,
                    "affiliation": a.affiliation,
                }
                for a in context.authors
            ],
            indent=2,
        )
    }

Existing organization mentions: {context.existing_organizations}
Existing justification: {context.existing_justification}
Existing EPFL relation: {context.existing_epfl_relation}
Existing EPFL justification: {context.existing_epfl_justification}

Please:
1. Analyze all email domains from git authors
2. Review all affiliations from ORCID records
3. Examine commit patterns: look at the first and last commit dates per author to understand temporal affiliation
4. For each organization identified, use the search_ror tool to find standardized information
5. Identify all levels of organizations (universities, departments, labs, research centers, etc.)
6. Determine hierarchical relationships where applicable
7. Provide a comprehensive assessment of EPFL relationship with detailed evidence
8. Return a complete list of organizations with standardized ROR information where available
9. For each organization, provide a confidence score (0.0 to 1.0) based on:
   - Strength of evidence (institutional email vs. ORCID vs. inference)
   - Number and percentage of commits from affiliated authors
   - Temporal alignment between commit dates and affiliation periods
10. Provide an EPFL affiliation confidence score (0.0 to 1.0) considering:
    - Percentage of commits from EPFL-affiliated authors
    - Whether EPFL authors are still active (recent commits)
    - Strength of affiliation evidence across multiple authors
"""

    return prompt
