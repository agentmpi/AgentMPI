#!/usr/bin/env bash
set -euo pipefail

# Codex cloud setup script for AgentMPI. This file is also safe to run in a
# local Codex universal image. Authentication is deliberately not handled here:
# credentials belong in the Codex environment settings or `codex login`.
python3 -m venv .venv
.venv/bin/python -m pip install --upgrade pip
.venv/bin/pip install -e ".[dev,tokens]"

# Installing explicitly upgrades an older preinstalled CLI as well as making
# local environments and refreshed cloud images behave the same way.
npm install --global @openai/codex

.venv/bin/ampi --version
codex --version
