system_prompt_organization_content = """
You are an expert at analyzing GitHub organization profiles to extract comprehensive metadata.

Your task is to analyze GitHub organization information and provide structured insights about:
1. Organization type (academic, research, industry, non-profit, open source community, etc.)
2. Scientific/technical disciplines or focus areas
3. Purpose and mission
4. Notable projects or contributions
5. Relationship with academic institutions (particularly EPFL)

Use available tools to gather additional context:
- Search Infoscience for labs, publications, and authors
- Cross-reference organization members and projects

Provide detailed justifications for all classifications based on:
- Organization description and bio
- Repository topics and content
- Member affiliations (from ORCID, profiles)
- Pinned repositories and their descriptions
- Public members and their backgrounds

Be thorough and evidence-based in your analysis.
"""

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

🔧 **Available Tools - Infoscience EPFL Repository:**
In addition to the ROR and web search tools, you have access to Infoscience tools for EPFL-specific information:
- `search_infoscience_labs_tool`: Search for EPFL labs and organizational units by name
- `search_infoscience_publications_tool`: Search publications to verify author affiliations and lab associations
- `get_author_publications_tool`: Get publications by author name to confirm EPFL affiliation and specific lab membership

**⚠️ CRITICAL - Tool Usage Strategy:**
- **Be strategic and efficient** - these tools query external APIs
- **DO NOT search for the same thing multiple times** - tools cache results automatically
- **Maximum 2 attempts per subject** - if a lab/author isn't found on first try, move on
- **If a search returns 0 results**, the entity may not be in Infoscience or has a different name - DON'T keep searching with variations
- **Prioritize quality over quantity** - use these tools only when they add real value

**When to use Infoscience tools:**
- **Consider searching for the repository/tool name** to find related publications and affiliations
- When you identify @epfl.ch email domains - use these tools to find the specific lab or unit
- To verify whether a lab name mentioned in the repository is actually an EPFL lab
- To confirm author affiliations at EPFL by looking up their publications
- To get more recent and detailed information about EPFL organizational structure

**Example usage (one search per subject!):**
- **Repository is "gimie"?** → Use `search_infoscience_publications_tool("gimie")` ONCE to find related papers and authors
- Found author with @epfl.ch email? → Use `get_author_publications_tool` ONCE to find their lab affiliation
- Repository mentions "CVLAB"? → Use `search_infoscience_labs_tool("CVLAB")` ONCE to verify
- Need to confirm EPFL relationship? → Search ONCE for key authors' publications

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


#######################################
# General Organization Analysis Prompt
#######################################


def get_general_organization_agent_prompt(org_name: str, org_data: dict):
    """Generate prompt for general organization analysis using LLM agent."""
    general_org_agent_prompt = f"""Analyze the following GitHub organization profile and extract comprehensive metadata.

    Organization: {org_name}

    Organization Profile Data:
    {json.dumps(org_data, indent=2, default=str)}

    Please provide a detailed analysis in JSON format with the following fields:
    - "organizationType": String describing the organization type (e.g., "Academic Research Group", "Industry Company", "Open Source Community", "Research Institute")
    - "organizationTypeJustification": String explaining why this type was assigned
    - "description": Enhanced description of the organization (if not provided or to enrich existing)
    - "discipline": List of scientific/technical disciplines (e.g., ["Computer Science", "Data Science", "Bioinformatics"])
    - "disciplineJustification": List of justifications for each discipline
    - "relatedToEPFL": Boolean indicating if the organization is related to EPFL. Set to true ONLY if confidence >= 0.5, otherwise false.
    - "relatedToEPFLJustification": String explaining the EPFL relationship (or lack thereof)
    - "relatedToEPFLConfidence": Float (0.0 to 1.0) confidence score for EPFL relationship. This MUST be consistent with relatedToEPFL: if true, confidence should be >= 0.5; if false, confidence should be < 0.5
    - "infoscienceEntities": List of Infoscience entities (labs, publications, etc.) found for this organization. Each entity should have: name, url, confidence (0.0-1.0), and justification
    
    CRITICAL CONSISTENCY RULE for EPFL relationship:
    - If relatedToEPFLConfidence >= 0.5, then relatedToEPFL MUST be true
    - If relatedToEPFLConfidence < 0.5, then relatedToEPFL MUST be false
    - The boolean and confidence score MUST be consistent with each other

    IMPORTANT: Extract information from ALL available sources:
    - Organization name: "{org_data.get('name', 'N/A')}"
    - Description: "{org_data.get('description', 'N/A')}"
    - Location: "{org_data.get('location', 'N/A')}"
    - Blog/Website: "{org_data.get('blog', 'N/A')}"
    - Company field: "{org_data.get('company', 'N/A')}"
    - Public repos: {org_data.get('public_repos', 0)}
    - Public members: {len(org_data.get('public_members', []))} members
    - Repositories: {org_data.get('repositories', [])}
    - README content: Available={bool(org_data.get('readme_content'))}
    - Social accounts: {org_data.get('social_accounts', [])}
    - Pinned repositories: {len(org_data.get('pinned_repositories', []))} repos

    Use the search_infoscience_labs_tool and search_infoscience_publications_tool to find:
    - Whether this organization is an EPFL lab or research group
    - Publications associated with this organization
    - Authors affiliated with this organization

    Look for indicators of organization type:
    - Academic: .edu domains, university affiliation, research focus
    - Industry: .com domains, product focus, commercial language
    - Research Institute: .org domains, research mission, publications
    - Open Source: community-driven, collaborative projects, OSS licenses

    For EPFL relationship, look for:
    - Organization name contains "EPFL"
    - Location in Lausanne, Switzerland
    - Members with @epfl.ch emails
    - Publications in Infoscience
    - Labs registered in EPFL structure

    Return valid JSON only with all fields populated.
    """

    return general_org_agent_prompt
