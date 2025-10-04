FROM ghcr.io/astral-sh/uv:python3.12-bookworm

# Set locale to avoid warnings
ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8

# Install just and other system dependencies
RUN apt-get update && apt-get install -y \
    sudo \
    curl \
    jq \
    && curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh | bash -s -- --to /usr/local/bin \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Crear usuario no-root con UID/GID que suele usar VS Code (1000:1000)
RUN useradd -ms /bin/bash -u 1000 non-root-user \
    && apt-get update && apt-get install -y sudo \
    && echo "non-root-user ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

WORKDIR /app
COPY src ./src
COPY pyproject.toml .

RUN uv pip install --system .

# Create data directory for cache and ensure proper permissions
RUN mkdir -p /app/data && chown -R non-root-user:non-root-user /app

USER non-root-user

# Set PYTHONPATH to include /app so src module can be found
ENV PYTHONPATH=/app

# Set cache database path to writable directory
ENV CACHE_DB_PATH=/app/data/api_cache.db

#ENTRYPOINT ["uvicorn", "src.api:app", "--host", "0.0.0.0", "--port", "1234", "--workers", "4"]
ENTRYPOINT ["gunicorn", "src.api:app", "--workers", "4", "--worker-class", "uvicorn.workers.UvicornWorker", "--bind", "0.0.0.0:1234"]
