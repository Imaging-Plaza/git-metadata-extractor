from langchain.prompts import PromptTemplate
from langchain_openai import OpenAI
from langchain.chains import LLMChain
import requests


def get_repo_name(github_url: str) -> str:
    llm = OpenAI(temperature=0)
    
    prompt = PromptTemplate(
        input_variables=["url"],
        template="Extract the repository name from this GitHub URL: {url}\nRepository name:"
    )
    
    chain = LLMChain(llm=llm, prompt=prompt)
    
    result = chain.run(url=github_url)
    
    return result.strip()


def get_repo_description(github_url: str) -> str:
    llm = OpenAI(temperature=0.7)
    
    # Extract owner and repo from URL
    parts = github_url.split('/')
    owner, repo = parts[-2], parts[-1]
    
    # Fetch README content
    readme_url = f"https://raw.githubusercontent.com/{owner}/{repo}/main/README.md"
    response = requests.get(readme_url)
    readme_content = response.text if response.status_code == 200 else "README not found"
    
    prompt = PromptTemplate(
        input_variables=["readme"],
        template="Summarize the following GitHub repository README in a single, short, and catchy sentence:\n\n{readme}\n\nSummary:"
    )
    chain = LLMChain(llm=llm, prompt=prompt)
    
    result = chain.run(readme=readme_content[:4000])  # Limit to first 4000 chars to avoid token limits
    
    return result.strip()


if __name__ == "__main__":
    url = "https://github.com/facebookresearch/segment-anything-2"
    
    repo_name = get_repo_name(url)
    print(f"Repository name: {repo_name}")
    
    repo_description = get_repo_description(url)
    print(f"Repository description: {repo_description}")
