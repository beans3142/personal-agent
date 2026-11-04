#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
# Owner-controlled configuration; do not source files supplied by a client.
set -a
source .env
set +a
exec .venv/bin/python -m agent.mcp_server
