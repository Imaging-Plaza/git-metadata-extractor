# GME-maintained gimie-api sidecar (see main.py header for why the upstream
# ghcr.io/sdsc-ordes/gimie-api image is unusable). gimie stays OUT of the
# GME Python package — this container is the only place it lives.
FROM python:3.11-slim

# gimie clones repositories (git) and detects licenses (libmagic).
RUN apt-get update && apt-get install -y --no-install-recommends \
    git \
    libmagic1 \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir gimie==0.7.2 "fastapi>=0.110" "uvicorn>=0.29"

RUN useradd -ms /bin/bash -u 1000 gimie
USER gimie
WORKDIR /app
COPY main.py .

EXPOSE 15400
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "15400"]
