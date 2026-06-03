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
# Ship the RAG index configs (config/index/*.yaml) — without them every
# index provider fails `load_config()` and silently disables itself.
# Deploy-specific values (Qdrant URL, tokens, scope) stay env-overridable.
COPY config ./config
# Ship operational scripts (e.g. scripts/v2/migrate_monolith_to_split.py) so
# operators can run migrations/re-ingest inside the container without
# bind-mounting them from a checkout.
COPY scripts ./scripts
COPY pyproject.toml .
COPY tools/config/gunicorn_conf.py ./gunicorn_conf.py

RUN uv pip install --system .

# Create data directory for cache and ensure proper permissions
RUN mkdir -p /app/data && chown -R non-root-user:non-root-user /app

USER non-root-user

# Set PYTHONPATH to include /app so src module can be found
ENV PYTHONPATH=/app

# Set cache database path to writable directory
ENV CACHE_DB_PATH=/app/data/api_cache.db

# Memory optimization environment variables
ENV MAX_CACHE_ENTRIES=5000
ENV MAX_SELENIUM_SESSIONS=1
ENV WORKERS=2

# Use gunicorn config file with memory optimizations
ENTRYPOINT ["gunicorn", "src.api:app", "--config", "gunicorn_conf.py"]
