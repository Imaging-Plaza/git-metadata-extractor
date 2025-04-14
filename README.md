# Imaging Software Metadata Extractor

This project is designed to classify imaging software repositories and extract relevant information using AI models like GPT and Gemini. It integrates with external services to analyze repositories and store the extracted data in JSON-LD format.

## Features
- Extracts repository metadata using GIMIE and AI models.
- Merges extracted data into JSON-LD format.
- Supports CLI usage for flexible execution.

## Installation

Clone the repository and install dependencies:
```sh
pip install -r requirements.txt
```

Create a `.env` file and fill it as follows:
```.env
OPENAI_API_KEY="your_openai_api_key"
GEMINI_API_KEY="your_gemini_api_key"
```

## Usage
You can run the script with the default settings or specify parameters via CLI:

```sh
python main.py --url https://github.com/qchapp/lungs-segmentation --output_path output_file.json
```

If no arguments are provided, it will use the default repository and output path.

## Project Structure
```
├── core/
│   ├── genai_model.py          # Gemini model interaction
├── files/
│   ├── json-ld-context.json    # Json-ld context for json to json-ld conversion
│   ├── output_file.json        # Json-ld output example
├── utils/
│   ├── utils.py                # Utility functions
│   ├── prompts.py              # Predefined AI prompts
│   ├── pydantic.py             # Output schema for Gemini request
│   ├── verification.py         # Post-response verification for LLM generated json
├── main.py                     # Main entry point for the CLI
├── README.md                   # Documentation
├── .env                        # Env file to create and fill
└── requirements.txt            # Dependencies
```

## Roadmap
- [x] Improve Gemini prompt.
- [x] Add logging and error handling.
- [ ] Add a post-response verification step for the LLM to check the accuracy of the returned information. -> Already there but can still be improved