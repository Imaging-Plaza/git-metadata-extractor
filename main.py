import os
import argparse
from pathlib import Path
from utils.utils import fetch_jsonld, merge_jsonld
from utils.prompts import question_is_imaging_software
from core.genai_model import llm_request_repo_infos
from core.gpt_model import Model

# Environment variables
OPENAI_API_KEY = os.environ["OPENAI_API_KEY"]
GIMIE_ENDPOINT = "http://imagingplazadev.epfl.ch:7511/gimie/jsonld/"
DEFAULT_REPO = "https://github.com/DeepLabCut/DeepLabCut"
DEFAULT_OUTPUT_PATH = "output_file.json"

class Request:
    def __init__(self):
        # Loading GPT model
        self.model = Model(api_key=OPENAI_API_KEY, model_name="gpt-4")

    def is_imaging_software(self, url: str) -> bool:
        """Checking if repo is an imaging software or not using GPT"""
        parsed_response = self.model.request_is_imaging_software(question_is_imaging_software, url)
        return parsed_response.is_imaging_software
    
    def repo_info(self, url: str, output_path: Path) -> None:
        """Retrieving repo infos using gimie + gemini and outputting it in the specified path."""
        jsonld_gimie_data = fetch_jsonld(GIMIE_ENDPOINT + url)
        response = llm_request_repo_infos(url)
        merge_jsonld(jsonld_gimie_data, response, output_path)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch and process repository information.")
    parser.add_argument("--url", default=DEFAULT_REPO, help="GitHub repository URL")
    parser.add_argument("--output_path", default=DEFAULT_OUTPUT_PATH, help="Path to save the output jsonLD file")
    
    args = parser.parse_args()
    output_path = Path(args.output_path)
    url = args.url
    
    request = Request()
    request.repo_info(url, output_path)