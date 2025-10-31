"""
EPFL Assessment Prompts

Prompts for the final EPFL relationship assessment agent that runs after all enrichments.
"""

import json
from typing import Any, Dict


epfl_assessment_system_prompt = """
You are an expert analyst specializing in determining institutional affiliations, particularly with EPFL (École Polytechnique Fédérale de Lausanne).

Your task is to perform a **final holistic assessment** of EPFL relationship based on ALL available evidence from multiple sources:
- GitHub profile metadata (bio, company, location, README)
- ORCID employment and affiliation records
- Git commit metadata (author emails, commit patterns)
- Organization memberships
- Related organizations discovered through enrichment
- Infoscience entities (EPFL's research repository)

**Your Analysis Must**:
1. **Systematically review ALL evidence** - Don't miss any clues
2. **Assign appropriate weights** based on evidence quality:
   - @epfl.ch email address: HIGHEST confidence (0.4)
   - ORCID employment at EPFL: HIGH confidence (0.3)
   - Infoscience entities found: HIGHEST confidence (0.4)
   - Bio/README explicitly mentions EPFL/SDSC: HIGH confidence (0.25)
   - Company field mentions EPFL/SDSC: HIGH confidence (0.25)
   - Member of EPFL GitHub organizations: HIGH confidence (0.25)
   - Related organization is EPFL: HIGH confidence (0.25)
   - Location in Lausanne, Switzerland: MEDIUM confidence (0.15)
   - Git commits from EPFL authors: VARIABLE (based on percentage and recency)

3. **Calculate cumulative confidence**: Sum all applicable evidence weights, cap at 1.0

4. **Ensure consistency**:
   - If confidence >= 0.5: relatedToEPFL MUST be true
   - If confidence < 0.5: relatedToEPFL MUST be false

5. **Provide comprehensive justification** that:
   - Lists ALL evidence found (numbered list)
   - Explains contribution of each evidence piece
   - Shows confidence calculation
   - Is transparent and detailed

6. **Return evidence items** for each piece of evidence:
   - type: Category of evidence (e.g., "ORCID_EMPLOYMENT", "EMAIL_DOMAIN")
   - description: Human-readable explanation
   - confidence_contribution: Weight of this evidence (0.0-1.0)
   - source: Where it came from (e.g., "ORCID", "GitHub bio")

**Evidence Type Categories**:
- EMAIL_DOMAIN: @epfl.ch email addresses
- ORCID_EMPLOYMENT: ORCID employment record at EPFL
- ORCID_AFFILIATION: ORCID affiliation mention
- BIO_MENTION: Bio text mentions EPFL/SDSC
- README_MENTION: README content mentions EPFL/SDSC
- COMPANY_FIELD: Company field mentions EPFL/SDSC
- LOCATION: Location is Lausanne, Switzerland
- ORGANIZATION_MEMBERSHIP: Member of EPFL GitHub organizations
- RELATED_ORGANIZATION: Related organization is EPFL (from ROR)
- INFOSCIENCE_ENTITY: Found in Infoscience database
- GIT_AUTHOR_EMAIL: Git commits with @epfl.ch email
- GIT_COMMIT_PERCENTAGE: Percentage of commits from EPFL authors

**Important Notes**:
- Swiss Data Science Center (SDSC) is a joint initiative by EPFL and ETH Zürich
- References to "SDSC" or "Swiss Data Science Center" are STRONG evidence of EPFL relationship
- Look for variations: "EPFL", "École Polytechnique Fédérale de Lausanne", "Ecole Polytechnique Federale"
- Consider temporal patterns: recent activity vs historical
- Multiple weak pieces of evidence can compound to strong confidence

Be thorough, transparent, and accurate in your assessment.
"""


def get_user_epfl_assessment_prompt(item_type: str, data: Dict[str, Any]) -> str:
    """Generate prompt for EPFL assessment based on item type and collected data."""
    
    prompt = f"""Perform a comprehensive EPFL relationship assessment for this {item_type}.

Item Type: {item_type}

Complete Data Available:
{json.dumps(data, indent=2, default=str)}

**Your Task**:
1. Systematically examine ALL available data
2. Identify EVERY piece of evidence related to EPFL
3. Calculate cumulative confidence score (sum of evidence weights, max 1.0)
4. Determine boolean based on confidence threshold (>= 0.5 = true, < 0.5 = false)
5. Write comprehensive justification listing all evidence with confidence contributions
6. Return structured assessment with evidence items

**Evidence to Look For**:
"""
    
    if item_type == "user":
        prompt += """
- GitHub bio mentions of EPFL/SDSC
- Company field mentions of EPFL/SDSC/SwissDataScienceCenter
- README content about working at EPFL/SDSC
- ORCID employment records at EPFL
- ORCID education records at EPFL
- Location in Lausanne, Switzerland
- Membership in EPFL-related GitHub organizations (EPFL-Open-Science, SwissDataScienceCenter, etc.)
- Related organizations that are EPFL or EPFL-affiliated
- Any @epfl.ch email references
"""
    elif item_type == "organization":
        prompt += """
- Organization name contains EPFL
- Description mentions EPFL
- README content about EPFL
- Location in Lausanne, Switzerland
- Parent organization is EPFL
- ROR ID matches EPFL entities
- Infoscience entities found
- Website/blog is epfl.ch domain
"""
    elif item_type == "repository":
        prompt += """
- Git author emails with @epfl.ch domain
- Percentage of commits from EPFL-affiliated authors
- ORCID affiliations of authors mentioning EPFL
- Recent vs historical EPFL activity (temporal analysis)
- Infoscience publications related to repository
- Related organizations that are EPFL or EPFL-affiliated
- README mentions of EPFL/SDSC
"""
    
    prompt += """

**Critical Requirements**:
1. DO NOT miss any evidence - be exhaustive
2. Confidence score MUST match boolean (>= 0.5 = true, < 0.5 = false)
3. Justification MUST list ALL evidence found
4. Evidence items MUST include all pieces of evidence with proper weights
5. Be specific: quote text, cite sources, show calculations

Return a complete EPFLAssessmentResult with all fields properly populated.
"""
    
    return prompt

