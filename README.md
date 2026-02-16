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

## Versioned documentation (GitHub Pages)

The repository includes a versioned documentation site under `docs/` powered by MkDocs Material + Mike.

Install docs dependencies:

```bash
uv pip install -e ".[docs]"
```

Local docs preview:

```bash
just docs-serve
```

Strict docs build:

```bash
just docs-build
```

Manual publish commands:

```bash
# Publish dev/latest from current branch
just docs-deploy-dev

# Publish a release version and update stable alias
just docs-deploy-release 2.0.1

# Set default version in selector
just docs-set-default stable
```

Automation:

- `.github/workflows/docs_pages.yml` publishes docs on:
  - Pushes to `main` (`dev` + `latest`)
  - Pushes of tags matching `v*` (release version + `stable`)
- Configure GitHub Pages to serve from the `gh-pages` branch root.

## How to run the tool using Docker?

1. You need to build the image.

    ``` bash
    docker build -t git-metadata-extractor -f tools/image/Dockerfile .
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

    **Option A: Standalone mode (single concurrent session - may cause errors with concurrent requests):**
    ``` bash
    docker run --rm -d -p 4444:4444 -p 7900:7900 --shm-size="2g" --name selenium-standalone-firefox --network dev selenium/standalone-firefox
    ```

    **Option B: Standalone mode with multiple sessions (recommended for concurrent requests):**
    ``` bash
    docker run --rm -d -p 4444:4444 -p 7900:7900 --shm-size="2g" \
      -e SE_NODE_MAX_SESSIONS=5 \
      -e SE_NODE_SESSION_TIMEOUT=300 \
      --name selenium-standalone-firefox \
      --network dev \
      selenium/standalone-firefox
    ```




    **Option C: Grid mode with hub and multiple nodes (best for high concurrency):**
    ``` bash
    # Start the hub
    docker run --rm -d -p 4444:4444 --name selenium-hub --network dev selenium/hub:latest

    # Start 3 Firefox nodes
    docker run --rm -d --shm-size="2g" -e SE_EVENT_BUS_HOST=selenium-hub \
      -e SE_EVENT_BUS_PUBLISH_PORT=4442 -e SE_EVENT_BUS_SUBSCRIBE_PORT=4443 \
      --name selenium-node-firefox-1 --network dev selenium/node-firefox:latest

    docker run --rm -d --shm-size="2g" -e SE_EVENT_BUS_HOST=selenium-hub \
      -e SE_EVENT_BUS_PUBLISH_PORT=4442 -e SE_EVENT_BUS_SUBSCRIBE_PORT=4443 \
      --name selenium-node-firefox-2 --network dev selenium/node-firefox:latest

    docker run --rm -d --shm-size="2g" -e SE_EVENT_BUS_HOST=selenium-hub \
      -e SE_EVENT_BUS_PUBLISH_PORT=4442 -e SE_EVENT_BUS_SUBSCRIBE_PORT=4443 \
      --name selenium-node-firefox-3 --network dev selenium/node-firefox:latest

    # Update .env to use: SELENIUM_REMOTE_URL=http://selenium-hub:4444
    ```

## How to develop using Docker?

To facilitate the development we can mount the app folder in the docker. By doing this, all changes made in local will be accesible from the running container.

``` bash
docker run -it --env-file .env -p 1234:1234 -v .:/app git-metadata-extractor
```


## How to start the API?

Simply run:

``` bash
docker run -it --rm --env-file .env -p 1234:1234 -v ./data:/app/data --name git-metadata-extractor --network dev git-metadata-extractor
```

This mounts the local `data` folder to persist cache files. To configure the cache directory, set `CACHE_DIR=/app/data` in your `.env` file.

Then go to `localhost:1234`

Or if you are running the container with `bash` as the entrypoint, please execute.

``` bash
uvicorn src.api:app --host 0.0.0.0 --workers 4 --port 1234 --reload
```

`--reload` allows you to modify the files and reload automatically the api endpoint. Excellent for development.

## Credits

- Quentin Chappuis - EPFL Center for Imaging
- Robin Franken - SDSC
- Carlos Vivar Rios - SDSC / EPFL Center for Imaging
