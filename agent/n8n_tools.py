"""Owner-allowlisted n8n production webhooks; never accept a caller-supplied URL.

Configure a private JSON object mapping names to webhook paths or loopback URLs
in data/n8n-workflows.json, or set DAEVIS_N8N_WORKFLOWS_FILE to its path.
No workflows are enabled when that file is absent. Registration is owner-local;
the caller still needs approval to invoke any configured workflow.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import re
from urllib.parse import urlsplit

import httpx

from agent.loop import Tool

_BASE = "http://127.0.0.1:5678"
_MAX_PAYLOAD_BYTES = 65_536
_TIMEOUT_SECONDS = 20
_NAME = re.compile(r"[A-Za-z0-9_-]{1,64}\Z")
# A conservative path alphabet excludes encodings, dot segments and delimiters
# that proxies/frameworks might normalize into another endpoint.
_PATH = re.compile(r"/webhook/[A-Za-z0-9_-]+(?:/[A-Za-z0-9_-]+)*\Z")
_CONFIG_ERROR = "invalid n8n workflow configuration"


def _load_workflows(config_path: str | Path | None) -> dict[str, str]:
    path = Path(config_path or os.environ.get("DAEVIS_N8N_WORKFLOWS_FILE") or "data/n8n-workflows.json")
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        raise ValueError(_CONFIG_ERROR) from None
    if not isinstance(raw, dict):
        raise ValueError(_CONFIG_ERROR)
    workflows = {}
    for name, target in raw.items():
        if not isinstance(name, str) or not _NAME.fullmatch(name) or not isinstance(target, str):
            raise ValueError(_CONFIG_ERROR)
        # Validate the exact original string before urlsplit can strip controls.
        if not target.isascii() or "?" in target or "#" in target or any(ord(c) < 33 or ord(c) == 127 for c in target):
            raise ValueError(_CONFIG_ERROR)
        path_only = target
        if not target.startswith("/"):
            try:
                parsed = urlsplit(target)
            except ValueError:
                raise ValueError(_CONFIG_ERROR) from None
            if parsed.scheme != "http" or parsed.netloc != "127.0.0.1:5678" or parsed.query or parsed.fragment:
                raise ValueError(_CONFIG_ERROR)
            path_only = parsed.path
        if not _PATH.fullmatch(path_only):
            raise ValueError(_CONFIG_ERROR)
        workflows[name] = _BASE + path_only
    return workflows


def build_n8n_tools(config_path: str | Path | None = None) -> list[Tool]:
    """Load a fixed owner allowlist and build discovery/approved POST tools.

    HTTP acceptance does not establish completion of downstream workflow steps.
    Errors are deliberately uncertain: a timed-out/erroring webhook may already
    have executed. This helper never retries or exposes webhook tokens/bodies.
    """
    workflows = _load_workflows(config_path)

    def list_workflows(args: dict) -> str:
        if not isinstance(args, dict) or args:
            return "error: n8n_list takes no arguments"
        return json.dumps({"workflows": sorted(workflows)})

    def run_workflow(args: dict) -> str:
        if not isinstance(args, dict) or set(args) != {"workflow", "payload"}:
            return "error: expected workflow and payload only"
        name, payload = args["workflow"], args["payload"]
        if not isinstance(name, str) or name not in workflows:
            return "error: workflow is not configured"
        if not isinstance(payload, dict):
            return "error: payload must be a JSON object"
        try:
            body = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
        except (TypeError, ValueError, RecursionError, UnicodeError):
            return "error: payload must be a JSON object"
        if len(body) > _MAX_PAYLOAD_BYTES:
            return "error: payload exceeds 65536 bytes"
        try:
            with httpx.Client(timeout=_TIMEOUT_SECONDS, follow_redirects=False, trust_env=False) as client:
                # Do not buffer arbitrary response bodies or forward their private
                # data to the model. No redirects, environmental proxies or retries.
                with client.stream("POST", workflows[name], content=body, headers={"Content-Type": "application/json"}) as response:
                    status = response.status_code
        except httpx.HTTPError:
            return "error: n8n delivery outcome uncertain; do not retry without checking workflow execution"
        if not 200 <= status < 300:
            return f"error: n8n HTTP {status}; execution outcome uncertain; do not retry without checking workflow execution"
        return json.dumps({"status": "accepted", "http_status": status,
                           "note": "Webhook accepted; downstream completion is not verified."})

    return [
        Tool("n8n_list", "List configured n8n workflow names without exposing webhook URLs.",
             {"type": "object", "properties": {}, "additionalProperties": False}, list_workflows),
        Tool("n8n_run", "POST a JSON payload to an owner-configured n8n workflow. Requires approval. Never retry an uncertain result without checking execution.",
             {"type": "object", "properties": {"workflow": {"type": "string"}, "payload": {"type": "object"}},
              "required": ["workflow", "payload"], "additionalProperties": False},
             run_workflow, needs_approval=True),
    ]
