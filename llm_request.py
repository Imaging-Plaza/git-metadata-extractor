from langchain_openai import ChatOpenAI
from langchain.output_parsers import PydanticOutputParser
from dotenv import load_dotenv
from github_request import GithubRequest
import os
from urllib.parse import urlparse

from utils import *
from output import *
from prompts import *

load_dotenv()

# loading the tokens
TOKEN = os.getenv("TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")

# output parsers
output_parser_is_imaging_software = PydanticOutputParser(pydantic_object=OutputIsImagingSoftware)
output_parser_repo_infos = PydanticOutputParser(pydantic_object=OutputRepoInfos)
output_parser_repo_description = PydanticOutputParser(pydantic_object=OutputDescription)

# our model class
class Model:
    def __init__(self, api_key, model_name):
        self.model = ChatOpenAI(
            model=model_name,
            openai_api_key=api_key,
            temperature=0
        )
        self.prompt_is_imaging_software = prompt_is_imaging_software

        self.prompt_repo_infos = prompt_repo_infos

        self.prompt_repo_description = prompt_repo_description

    def request_is_imaging_software(self, question, repo_url):
        formatted_prompt = self.prompt_is_imaging_software.format_prompt(
            question=question.format(repo_url=repo_url)
        ).to_messages()
        response = self.model.invoke(formatted_prompt)
        parsed_response = output_parser_is_imaging_software.parse(response.content)
        return parsed_response
    
    def request_repo_infos(self, question, repo_url):
        formatted_prompt = self.prompt_repo_infos.format_prompt(
            question=question.format(repo_url=repo_url)
        ).to_messages()
        response = self.model.invoke(formatted_prompt)
        parsed_response = output_parser_repo_infos.parse(response.content)
        return parsed_response
    
    def request_repo_description(self, question, repo_url, readme_url):
        formatted_prompt = self.prompt_repo_description.format_prompt(
            question=question.format(repo_url=repo_url, readme=readme_url)
        ).to_messages()
        response = self.model.invoke(formatted_prompt)
        print(response)
        parsed_response = output_parser_repo_description.parse(response.content)
        return parsed_response

model = Model(api_key=OPENAI_API_KEY, model_name="gpt-4")
    
github = GithubRequest(token=TOKEN)

repos = ["https://github.com/stardist/stardist"] # -> trying on less repos

# checking if repo is an imaging software or not
imaging_software = {}
for repo in repos:
    parsed_response = model.request_is_imaging_software(question_is_imaging_software, repo)
    imaging_software[parsed_response.repo] = parsed_response.is_imaging_software

print(f"Number of imaging softwares: {sum(imaging_software.values())}.\nRatio: {round(sum(imaging_software.values())/len(imaging_software.values())*100, 2)}%")

# retrieving repo infos
imaging_repo_infos = {}
for repo in imaging_software.keys():
    if imaging_software[repo]:
        repo_name = urlparse(repo).path.strip("/")
        imaging_repo_infos[repo] = github.request_repo_infos(repo_name)

# retrieving doi, doc and license with good format using gpt
for repo in imaging_repo_infos.keys():
    repo_name = urlparse(repo).path.strip("/")
    parsed_response = model.request_repo_infos(question_repo_infos, repo_name)
    imaging_repo_infos[repo]["docker"] = parsed_response.docker
    imaging_repo_infos[repo]["documentation"] = parsed_response.documentation
    imaging_repo_infos[repo]["image"] = parsed_response.image
    imaging_repo_infos[repo]["requirements"] = parsed_response.requirements
    imaging_repo_infos[repo]["executable"] = parsed_response.executable

for repo, infos in imaging_repo_infos.items():
    if infos["description"] == "" or infos["description"] == None:
        repo_name = urlparse(repo).path.strip("/")
        parsed_response = model.request_repo_description(question_repo_description, repo_name, infos["readme"])
        infos["description"] = parsed_response.description
    
print(dict_to_json(imaging_repo_infos))

github.close()