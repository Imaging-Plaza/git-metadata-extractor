import os
from utils.utils import *
from utils.prompts import *
from core.genai_model import llm_request_repo_infos
from core.gpt_model import Model

# Environment variables
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
GIMIE_ENDPOINT = "http://imagingplazadev.epfl.ch:7511/gimie/jsonld/"

# Loading GPT model
model = Model(api_key=OPENAI_API_KEY, model_name="gpt-4")

# Repos we are interesting at
repos = ["https://github.com/stardist/stardist"] # -> trying on less repos

# Main function
def main(repos) -> dict:
    # Checking if repo is an imaging software or not using GPT
    imaging_software = {}
    for repo in repos:
        parsed_response = model.request_is_imaging_software(question_is_imaging_software, repo)
        imaging_software[parsed_response.repo] = parsed_response.is_imaging_software

    print(f"Number of imaging softwares: {sum(imaging_software.values())}.\nRatio: {round(sum(imaging_software.values())/len(imaging_software.values())*100, 2)}%")

    # Retrieving repo infos using gimie + gemini
    imaging_repo_infos = {}
    for url in imaging_software.keys():
        if imaging_software[url]:
            jsonld_gimie_data = fetch_jsonld(GIMIE_ENDPOINT + url)

            response = llm_request_repo_infos(url)

            imaging_repo_infos[url] = merge_jsonld(jsonld_gimie_data, response)

    return imaging_repo_infos

if __name__ == "__main__":
    repos_infos = main(repos)
    from pprint import pprint
    pprint(repos_infos)