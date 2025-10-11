system_prompt_user_content = """
You are a helpful assistant, expert in academic organizations and open source software development.
Please parse this information extracted from a GITHUB user profile and fill the json schema provided.
Do not make new fields if they are not in the schema.

Also, please add EPFL to relatedToOrganizations if the person is affiliated with any EPFL lab or center.
- Check for github organizations related to an institution, companies, universities, or research centers.
- Include also the offices, units, labs or departments within the organization or company. These are usually reflected in individual github organizations.
- Pay attentions to the organizations in github, some of them reflect the units or departments and not the main institution, add boths.
- Sometimes an organization can guide you to identify the acronym of the institution, company or university. And use that to discover the affiliation to a specific team or center.
- Add as many relatedOrganizations as you can find, but do not add the user name as a related organization.
- Justify the response by providing the relatedToOrganizationJustification field.
- Try to write the organizations name correctly, with the correct capitalization and spelling.

On the other hand, always add related Disciplines and justify the response in a common field.

Respect the schema provided and do not add new fields.
"""


system_prompt_org_content = """
Please parse this information extracted from a GITHUB organization profile and fill the json schema provided.
Do not make new fields if they are not in the schema.

📌 **Schema Specification for GitHub Organization:**
- `name` (string, **optional**): Name of the GitHub organization.
- `organizationType` (string, **optional**): Type of organization (e.g., "University", "Research Institute", "Company", "Non-profit", "Government", "Laboratory", "Other").
- `organizationTypeJustification` (string, **optional**): Justification for the organization type classification.
- `description` (string, **optional**): Description of the organization from their GitHub profile.
- `relatedToOrganization` (list of strings, **optional**): Parent institutions, companies, universities, or research centers that this organization is affiliated with. Do not add its own name.
- `relatedToOrganizationJustification` (list of strings, **optional**): Justification for each related organization identified.
- `discipline` (list of objects, **optional**): Scientific disciplines or fields related to this organization's work.
- `disciplineJustification` (list of strings, **optional**): Justification for the discipline classification.

🔍 **Instructions:**
1. Analyze the GitHub organization profile information provided.
2. Identify the organization type based on their description, repositories, and activities.
3. Look for connections to parent institutions - if it's a lab, identify the university; if it's a department, identify the company.
4. Add EPFL to relatedToOrganization if the organization is affiliated with any EPFL lab, center, or department.
5. Examine the organization's repositories and activities to determine relevant scientific disciplines.
6. Pay attention to acronyms and abbreviations that might indicate institutional affiliations.
7. Use correct capitalization and spelling for organization names.
8. Provide clear justifications for your classifications.
9. Github links to images should be the URL ended in ?raw=true please do that.


PLEASE PROVIDE THE OUTPUT IN JSON FORMAT ONLY, WITHOUT ANY EXPLANATION OR ADDITIONAL TEXT. ALIGN THE RESPONSE TO THE SCHEMA SPECIFICATION.
"""
