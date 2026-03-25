FROM ghcr.io/astral-sh/uv:python3.12-bookworm

ENV LC_ALL=C.UTF-8
ENV LANG=C.UTF-8

RUN apt-get update && apt-get install -y \
    sudo \
    curl \
    jq \
    && curl --proto '=https' --tlsv1.2 -sSf https://just.systems/install.sh | bash -s -- --to /usr/local/bin \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

RUN useradd -ms /bin/bash -u 1000 vscode \
    && apt-get update \
    && apt-get install -y sudo \
    && echo "vscode ALL=(ALL) NOPASSWD:ALL" >> /etc/sudoers


RUN mkdir -p /app/data \
    && chown -R 1000:1000 /app/data \
    && chmod 775 /app/data

USER vscode
WORKDIR /workspaces
