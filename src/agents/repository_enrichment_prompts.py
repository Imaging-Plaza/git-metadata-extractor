def get_repo_general_prompt(repo_url: str, input_text: str):
    prompt = f"""Analyze the following software repository and extract comprehensive metadata.

    Repository URL: {repo_url}

    Repository Content:
    {input_text}

    Please provide a detailed analysis including:
    - Repository name, description, and purpose
    - Programming languages used
    - License information
    - Author information and affiliations
    - Related organizations
    - Keywords and topics
    - Any other relevant metadata

    Focus on accuracy and completeness in your analysis."""

    return prompt
