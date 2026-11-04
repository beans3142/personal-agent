#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "${BASH_SOURCE[0]}")/.."
declare -A caller_scope=()
for name in DAEVIS_MCP_PUBLIC DAEVIS_MCP_OWNER_REQUEST DAEVIS_MCP_REQUEST_ID DAEVIS_MCP_REQUEST_EXPIRES DAEVIS_MCP_READ_ONLY DAEVIS_MCP_ALLOW_TOOLS DAEVIS_MCP_APPROVAL AGENT_OUTBOX DAEVIS_BROWSER_CDP; do
  if [[ -v "$name" ]]; then caller_scope["$name"]="${!name}"; fi
done
# Owner-controlled configuration; do not source files supplied by a client.
set -a
source .env
set +a
for name in "${!caller_scope[@]}"; do export "$name=${caller_scope[$name]}"; done
exec .venv/bin/python -m agent.mcp_server
