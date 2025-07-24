# Git Metadata Extractor

This project is designed to classify imaging software repositories and extract relevant information using AI models like GPT and Gemini. It integrates with external services to analyze repositories and store the extracted data in JSON-LD format.

The output of `/v1/extract` aligns with the softwareSourceCodeSchema of Imaging Plaza project. 

## Features

- Extracts repository metadata using GIMIE and AI models.
- Merges extracted data into JSON-LD format.
- Supports CLI usage for flexible execution.

## Project Structure

```bash
.
├── CHANGELOG.md
├── Dockerfile
├── LICENSE
├── pyproject.toml
├── README.md
├── requirements.txt
└── src
    ├── __init__.py
    ├── __pycache__
    │   └── __init__.cpython-311.pyc
    ├── api.py
    ├── core
    │   ├── __init__.py
    │   ├── __pycache__
    │   │   ├── __init__.cpython-311.pyc
    │   │   └── models.cpython-311.pyc
    │   ├── genai_model.py
    │   ├── gimie_methods.py
    │   ├── models.py
    │   ├── prompts.py
    │   └── verification.py
    ├── files
    │   ├── json-ld-context.json
    │   └── output_file.json
    ├── main.py
    ├── test
    │   ├── __pycache__
    │   │   └── test_conversion.cpython-311-pytest-8.4.1.pyc
    │   └── test_conversion.py
    └── utils
        ├── __init__.py
        ├── logging_config.py
        └── utils.py
```


## Installation

Clone the repository and install dependencies:

``` sh
pip install -r requirements.txt
```

Create a `.env` (or modify `.env.dist`) file and fill it as follows:

``` bash
OPENAI_API_KEY="your_openai_api_key"
OPENROUTER_API_KEY="your_openrouter_api_key"
GITHUB_TOKEN=
GITLAB_TOKEN=
MODEL="model to be used"
PROVIDER="openai" or "openrouter"
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
    docker build -t git-metadata-extractor . 
    ```

2. Run the image.

    ``` bash
    docker run -it --env-file .env -p 1234:1234 --entrypoint bash git-metadata-extractor
    ```

    If you are developping the application it's useful to mount the app volume. 

    ``` bash
    docker run -it --env-file .env -p 1234:1234 -v .:/app --entrypoint bash git-metadata-extractor
    ```

3. Then you can run the tool via

    ``` bash
    python src/main.py --url https://github.com/qchapp/lungs-segmentation --output_path output_file.json
    ```

4. Optional. If you are planning to use the ORCID functionality, you need to start a remote browser and configure the `.env` file. 

    ``` bash
    docker run --rm -d -p 4444:4444 -p 7900:7900 --shm-size="2g" selenium/standalone-firefox
    ```

## How to develop using Docker?

To facilitate the development we can mount the app folder in the docker. By doing this, all changes made in local will be accesible from the running container. 

```bash
docker run -it --env-file .env -p 1234:1234 -v .:/app git-metadata-extractor
```


## How to start the API?

Simply run:

```
docker run -it --env-file .env -p 1234:1234 git-metadata-extractor
```

and go to `localhost:1234`


Or if you are running the container with `bash` as the entrypoint, please execute.

```bash
uvicorn src.api:app --host 0.0.0.0 --port 1234 --reload
```

`--reload` allows you to modify the files and reload automatically the api endpoint. Excellent for development.

## Credits

Quentin Chappuis - EPFL Center for Imaging 
Robin Franken - SDSC
Carlos Vivar Rios - SDSC / EPFL Center for Imaging
