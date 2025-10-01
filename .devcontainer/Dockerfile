FROM ghcr.io/astral-sh/uv:python3.12-bookworm

# Install just and other system dependencies
RUN apt-get update && apt-get install -y \
    sudo \
    curl \
    && curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh | bash -s -- --to /usr/local/bin \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Crear usuario no-root con UID/GID que suele usar VS Code (1000:1000)
RUN useradd -ms /bin/bash -u 1000 vscode \
    && apt-get update && apt-get install -y sudo \
    && echo "vscode ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

USER vscode
WORKDIR /workspaces