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

# Installs the RAG index layer (open_pulse_sources) too: it is a declared
# dependency pinned to an immutable tag in pyproject.toml, which is the one
# place that version lives. The read-side providers and the index-store
# bootstrap import it unconditionally.
RUN uv pip install --system .

# Escape hatch only — leave EMPTY so the pyproject pin governs the build.
# Set it to test an unreleased child revision without editing pyproject:
#   docker build --build-arg OPEN_PULSE_SOURCES_REF=<tag|sha> ...
# A permanent bump belongs in pyproject.toml (+ the gme-sources image tag in
# tools/deploy/docker-compose.yml); tests/v2/test_open_pulse_sources_pin.py
# enforces that those two agree.
ARG OPEN_PULSE_SOURCES_REF=
RUN if [ -n "${OPEN_PULSE_SOURCES_REF}" ]; then \
        echo "overriding the pyproject pin with ${OPEN_PULSE_SOURCES_REF}"; \
        uv pip install --system \
            "open-pulse-sources @ git+https://github.com/sdsc-ordes/open-pulse-sources@${OPEN_PULSE_SOURCES_REF}"; \
    fi

# Create data directory for cache and ensure proper permissions
RUN mkdir -p /app/data && chown -R non-root-user:non-root-user /app

USER non-root-user

# Set PYTHONPATH to include /app so the package resolves from the copied tree
ENV PYTHONPATH=/app

ENV WORKERS=2

# Use gunicorn config file with memory optimizations
ENTRYPOINT ["gunicorn", "git_metadata_extractor.app:app", "--config", "gunicorn_conf.py"]
