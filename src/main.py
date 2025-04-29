import argparse
from pathlib import Path
from utils.utils import fetch_jsonld, merge_jsonld
from core.genai_model import llm_request_repo_infos
import logging
from utils.logging_config import setup_logging

# Environment variables
GIMIE_ENDPOINT = "http://imagingplazadev.epfl.ch:7511/gimie/jsonld/"
DEFAULT_REPO = "https://github.com/qchapp/lungs-segmentation"
DEFAULT_OUTPUT_PATH = "output_file.json"

# Setup logging
setup_logging()
logger = logging.getLogger(__name__)


def main(url: str, output_path: Path) -> None:
    """Retrieving repo infos using gimie + gemini and outputting it in the specified path."""

    logger.info(f"Fetching JSON-LD data from GIMIE for {url}")
    jsonld_gimie_data = fetch_jsonld(GIMIE_ENDPOINT + url)

    logger.info("Fetching Gemini response for repository...")
    llm_result = llm_request_repo_infos(url)

    if not llm_result:
        logger.error("LLM returned no data. Aborting.")
        return

    logger.info("Merging and saving output to JSON-LD...")
    merge_jsonld(jsonld_gimie_data, llm_result, output_path)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Fetch and process repository information.")
    parser.add_argument("--url", default=DEFAULT_REPO, help="GitHub repository URL")
    parser.add_argument("--output_path", default=DEFAULT_OUTPUT_PATH, help="Path to save the output jsonLD file")
    
    args = parser.parse_args()
    output_path = Path(args.output_path)
    url = args.url

    main(url, output_path)