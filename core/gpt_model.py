from langchain_openai import ChatOpenAI
from langchain.output_parsers import PydanticOutputParser

from utils.utils import *
from utils.output import *
from utils.prompts import *

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