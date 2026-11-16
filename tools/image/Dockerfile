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
COPY git_metadata_extractor ./git_metadata_extractor
# Ship operational scripts (e.g. scripts/v2/migrate_monolith_to_split.py) so
# operators can run migrations/re-ingest inside the container without
# bind-mounting them from a checkout.
COPY scripts ./scripts
COPY pyproject.toml .
COPY tools/config/gunicorn_conf.py ./gunicorn_conf.py

# The extractor talks to sources over HTTP; its image contains no index library.
RUN uv pip install --system .

# Create data directory for cache and ensure proper permissions
RUN mkdir -p /app/data && chown -R non-root-user:non-root-user /app

USER non-root-user

# Set PYTHONPATH to include /app so the package resolves from the copied tree
ENV PYTHONPATH=/app

ENV WORKERS=2

# Use gunicorn config file with memory optimizations
ENTRYPOINT ["gunicorn", "git_metadata_extractor.app:app", "--config", "gunicorn_conf.py"]
