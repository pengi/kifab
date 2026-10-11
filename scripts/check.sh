#!/usr/bin/env sh
# The checks every change must pass (CLAUDE.md): format, types, tests.
set -e
cd "$(dirname "$0")/.."
uv run black --check src tests
uv run mypy
uv run pytest -q "$@"
