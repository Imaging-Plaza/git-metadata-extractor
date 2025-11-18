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

# Crear usuario no-root con UID/GID que suele usar VS Code (1000:1000).
# TOOD: Take this user out of sudoers if you want to use this in fully agents mode.
RUN useradd -ms /bin/bash -u 1000 vscode \
    && apt-get update && apt-get install -y sudo \
    && echo "vscode ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers

# Gemini CLI
# Please login outside of the container and copy your credentials to ~/.gemini/...
RUN curl -fsSL https://deb.nodesource.com/setup_24.x | sudo -E bash - && sudo apt-get install -y nodejs
RUN npm install -g @google/gemini-cli

USER vscode
WORKDIR /workspaces
