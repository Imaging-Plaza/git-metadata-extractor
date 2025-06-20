# Imaging Software Metadata Extractor

This project is designed to classify imaging software repositories and extract relevant information using AI models like GPT and Gemini. It integrates with external services to analyze repositories and store the extracted data in JSON-LD format.

## Features

- Extracts repository metadata using GIMIE and AI models.
- Merges extracted data into JSON-LD format.
- Supports CLI usage for flexible execution.

## Project Structure

```bash
├── src/
│   ├── core/
│   │   ├── prompts.py              # Predefined AI prompts
│   │   ├── pydantic.py             # Output schema for Gemini request
│   │   ├── verification.py         # Post-response verification for LLM generated json
│   │   └── genai_model.py          # Gemini model interaction
│   ├── files/
│   │   ├── json-ld-context.json    # Json-ld context for json to json-ld conversion
│   │   └── output_file.json        # Json-ld output example
│   ├── utils/
│   │   ├── utils.py                # Utility functions
│   │   └── logging_config.py       # Logging config file
│   └── main.py                     # Main entry point for the CLI
├── .env.dist                       # Env file template to fill
├── .gitignore                      # Git exclusions
├── Dockerfile                      # Container setup
├── README.md                       # Documentation
└── requirements.txt                # Dependencies
```


## Installation

Clone the repository and install dependencies:

``` sh
pip install -r requirements.txt
```

Create a `.env` (or modify `.env.dist`) file and fill it as follows:

``` bash
OPENAI_API_KEY="your_openai_api_key"
OPENROUTER_API_KEY="your_gemini_api_key"
```

## Usage

You can run the script with the default settings or specify parameters via CLI:

```sh
python src/main.py --url https://github.com/qchapp/lungs-segmentation --output_path output_file.json
```

If no arguments are provided, it will use the default repository and output path.

## How to run the tool using Docker?

1. You need to build the image.

    ``` bash
    docker build -t llm-software-finder . 
    ```

2. Run the image.

    ``` bash
    docker run -it --env-file .env -p 1234:1234 llm-software-finder
    ```

    If you are developping the application it's useful to mount the app volume. 

    ``` bash
    docker run -it --env-file .env -p 1234:1234 -v .:/app llm-software-finder
    ```

3. Then you can run the tool via

    ``` bash
    python src/main.py --url https://github.com/qchapp/lungs-segmentation --output_path output_file.json
    ```

## How to develop using Docker?

To facilitate the development we can mount the app folder in the docker. By doing this, all changes made in local will be accesible from the running container. 

```bash
docker run -it --env-file .env -p 1234:1234 -v .:/app llm-software-finder
```


## How to start the API?


After running the docker container with port 8000, please execute.

```bash
uvicorn src.api:app --host 0.0.0.0 --port 1234 --reload
```

```

```

## Roadmap

- [x] Improve Gemini prompt.
- [x] Add logging and error handling.
- [ ] Add a post-response verification step for the LLM to check the accuracy of the returned information. -> Already there but can still be improved
- [x] Develop the API endpoint
- [ ] Add HTTP Errors at output when using the API. 
    - Check for lack of credits
    - Check for issues in Gimie
    - Check for issues in the LLM 
    - Check for issues in validation