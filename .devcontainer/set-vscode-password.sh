#!/usr/bin/env bash
# Apply VSCODE_PASSWORD to user vscode at container start (not baked into the image).
# Set VSCODE_PASSWORD in .env (this repo loads it via devcontainer runArgs --env-file).
set -euo pipefail
if [[ -z "${VSCODE_PASSWORD:-}" ]]; then
  exit 0
fi
printf 'vscode:%s\n' "$VSCODE_PASSWORD" | sudo chpasswd
