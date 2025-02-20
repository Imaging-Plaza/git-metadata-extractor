from dotenv import load_dotenv
from core.github_request import GithubRequest
import os
from urllib.parse import urlparse
from pprint import pprint

from utils.utils import *
from utils.prompts import *
from core.genai_model import request
from core.gpt_model import Model

load_dotenv()

# loading the tokens
TOKEN = os.getenv("TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
GIMIE_ENDPOINT = "http://imagingplazadev.epfl.ch:7511/gimie/jsonld/"

# model = Model(api_key=OPENAI_API_KEY, model_name="gpt-4")
    
# github = GithubRequest(token=TOKEN)

repos = ["https://github.com/stardist/stardist"] # -> trying on less repos
repos = ["https://github.com/DeepLabCut/DeepLabCut"]

# checking if repo is an imaging software or not
# imaging_software = {}
# for repo in repos:
#     parsed_response = model.request_is_imaging_software(question_is_imaging_software, repo)
#     imaging_software[parsed_response.repo] = parsed_response.is_imaging_software

# print(f"Number of imaging softwares: {sum(imaging_software.values())}.\nRatio: {round(sum(imaging_software.values())/len(imaging_software.values())*100, 2)}%")

# retrieving repo infos
imaging_repo_infos = {}
# for repo in imaging_software.keys():
    # if imaging_software[repo]:
        ### gimie
        # jsonld_gimie_data = fetch_jsonld(GIMIE_ENDPOINT + repo)
        ###
        # repo_name = urlparse(repo).path.strip("/")
        # imaging_repo_infos[repo] = github.request_repo_infos(repo_name)

# retrieving more infos using gpt4
# for repo in imaging_repo_infos.keys():
#     repo_name = urlparse(repo).path.strip("/")
#     parsed_response = model.request_repo_infos(question_repo_infos, repo_name)
#     imaging_repo_infos[repo]["docker"] = parsed_response.docker
#     imaging_repo_infos[repo]["documentation"] = parsed_response.documentation
#     imaging_repo_infos[repo]["image"] = parsed_response.image
#     imaging_repo_infos[repo]["requirements"] = parsed_response.requirements
#     imaging_repo_infos[repo]["executable"] = parsed_response.executable

# completing the description if empty
# for repo, infos in imaging_repo_infos.items():
#     if infos["description"] == "" or infos["description"] == None:
#         repo_name = urlparse(repo).path.strip("/")
#         parsed_response = model.request_repo_description(question_repo_description, repo_name, infos["readme"])
#         infos["description"] = parsed_response.description
    
# print(dict_to_json(imaging_repo_infos))

# close the github connection
# github.close()

for url in repos:
    # jsonld_gimie_data = fetch_jsonld(GIMIE_ENDPOINT + url)
    # pprint(jsonld_gimie_data)

    request(url)