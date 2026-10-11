#!/bin/bash
# Cloud sessions only: make `scripts/check.sh` and the real-KiCad tests work.
#   - Python deps (uv sync, incl. the dev group: black, mypy, pytest, PySide6)
#   - libraries Qt needs for the headless GUI tests
#   - a docker daemon and the kicad/kicad:10.0 image for tests/test_real_kicad.py
#     (pulled in the background; KIFAB_REAL_KICAD_CLI points at the wrapper)
set -euo pipefail

if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi
cd "$CLAUDE_PROJECT_DIR"

if ! ldconfig -p 2>/dev/null | grep -q libEGL.so.1; then
  (apt-get install -y -q libegl1 libxkbcommon0 >/dev/null 2>&1 \
    || { apt-get update -q >/dev/null 2>&1 && apt-get install -y -q libegl1 libxkbcommon0 >/dev/null 2>&1; }) \
    || echo "session-start: could not install libegl1 (GUI tests will be skipped)" >&2
fi

export UV_SYSTEM_CERTS=1
unset UV_NATIVE_TLS
uv sync -q

if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  {
    echo 'export UV_SYSTEM_CERTS=1'
    echo 'unset UV_NATIVE_TLS'
    echo "export KIFAB_REAL_KICAD_CLI=$CLAUDE_PROJECT_DIR/scripts/kicad-cli-docker"
  } >> "$CLAUDE_ENV_FILE"
fi

# Real KiCad via docker, best effort and in the background (the pull is large).
if command -v dockerd >/dev/null 2>&1; then
  if ! docker info >/dev/null 2>&1; then
    nohup dockerd >/tmp/dockerd.log 2>&1 &
  fi
  nohup sh -c 'for i in $(seq 1 30); do docker info >/dev/null 2>&1 && break; sleep 1; done;
    docker pull -q kicad/kicad:10.0' >/tmp/kicad-pull.log 2>&1 &
fi
