"""Model connection through a ChatGPT subscription login (Codex OAuth), with no API key."""

from __future__ import annotations

import base64
import json
import os
import time
from pathlib import Path
from typing import Callable, Iterable

import httpx

ENDPOINT = "https://chatgpt.com/backend-api/codex/responses"
TOKEN_URL = "https://auth.openai.com/oauth/token"
CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"
REFRESH_MARGIN_SECONDS = 120
KEPT_ITEM_TYPES = ("message", "function_call")


class CodexError(Exception):
    pass


def parse_sse(lines: Iterable[str]) -> list[dict]:
    items: list[dict] = []
    for line in lines:
        if not line.startswith("data: "):
            continue
        event = json.loads(line[6:])
        kind = event.get("type")
        if kind == "response.output_item.done":
            if event["item"].get("type") in KEPT_ITEM_TYPES:
                items.append(event["item"])
        elif kind == "response.completed":
            return items
        elif kind in ("response.failed", "response.incomplete", "error"):
            detail = event.get("response", {}).get("error") or event.get("error") or event
            raise CodexError(detail.get("message", str(detail)) if isinstance(detail, dict) else str(detail))
    raise CodexError("stream ended before the response completed")


def account_id_from(access_token: str) -> str:
    body = access_token.split(".")[1]
    claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    return claims["https://api.openai.com/auth"]["chatgpt_account_id"]


def refresh_request(refresh_token: str) -> dict:
    response = httpx.post(TOKEN_URL, timeout=30, json={
        "client_id": CLIENT_ID,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    })
    response.raise_for_status()
    return response.json()


class CodexAuth:
    def __init__(self, path: Path, refresher: Callable[[str], dict] = refresh_request):
        self.path = Path(path)
        self.refresher = refresher

    def access_token(self) -> str:
        try:
            auth = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise CodexError(f"no login at {self.path}. Run: python -m agent.login") from None
        if auth["expires"] > time.time() + REFRESH_MARGIN_SECONDS:
            return auth["access"]
        fresh = self.refresher(auth["refresh"])
        auth.update(
            access=fresh["access_token"],
            refresh=fresh.get("refresh_token", auth["refresh"]),
            expires=time.time() + fresh.get("expires_in", 3600),
        )
        self.save(auth)
        return auth["access"]

    def save(self, auth: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix(".tmp")
        temp.write_text(json.dumps(auth), encoding="utf-8")
        os.chmod(temp, 0o600)
        temp.replace(self.path)


class CodexModel:
    def __init__(self, auth: CodexAuth, model: str):
        self.auth = auth
        self.model = model

    def respond(self, instructions: str, items: list[dict], tools: list[dict]) -> list[dict]:
        token = self.auth.access_token()
        headers = {
            "Authorization": f"Bearer {token}",
            "chatgpt-account-id": account_id_from(token),
            "originator": "codex_cli_rs",
            "accept": "text/event-stream",
        }
        payload = {
            "model": self.model,
            "instructions": instructions,
            "input": items,
            "tools": tools,
            "tool_choice": "auto",
            "parallel_tool_calls": False,
            "store": False,
            "stream": True,
        }
        with httpx.stream("POST", ENDPOINT, headers=headers, json=payload, timeout=180) as response:
            if response.status_code != 200:
                raise CodexError(f"HTTP {response.status_code}: {response.read().decode('utf-8', 'replace')[:300]}")
            return parse_sse(response.iter_lines())
