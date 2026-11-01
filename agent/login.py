"""Signs in with a ChatGPT account (OAuth PKCE) and saves tokens for the agent.

Open the printed URL, sign in, then paste the address the browser lands on.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

from .codex import CLIENT_ID, TOKEN_URL, CodexAuth

AUTHORIZE_URL = "https://auth.openai.com/oauth/authorize"
REDIRECT_URI = "http://localhost:1455/auth/callback"
SCOPE = "openid profile email offline_access"


def pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    return verifier, base64.urlsafe_b64encode(digest).decode().rstrip("=")


def authorize_url(challenge: str, state: str) -> str:
    return f"{AUTHORIZE_URL}?" + urlencode({
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": REDIRECT_URI,
        "scope": SCOPE,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "state": state,
    })


def code_from_redirect(pasted: str, state: str) -> str:
    query = parse_qs(urlparse(pasted.strip()).query)
    if query.get("state", [""])[0] != state:
        raise ValueError("state does not match: start the login again")
    if "code" not in query:
        raise ValueError("the pasted address has no code")
    return query["code"][0]


def main() -> None:
    verifier, challenge = pkce_pair()
    state = secrets.token_urlsafe(16)
    print(authorize_url(challenge, state))
    code = code_from_redirect(input("Paste the address after sign-in: "), state)
    response = httpx.post(TOKEN_URL, timeout=30, data={
        "grant_type": "authorization_code",
        "client_id": CLIENT_ID,
        "code": code,
        "redirect_uri": REDIRECT_URI,
        "code_verifier": verifier,
    })
    response.raise_for_status()
    tokens = response.json()
    path = Path(os.environ.get("AGENT_DATA", "data")) / "codex-auth.json"
    CodexAuth(path).save({
        "access": tokens["access_token"],
        "refresh": tokens["refresh_token"],
        "expires": time.time() + tokens.get("expires_in", 3600),
    })
    print(json.dumps({"saved": str(path)}))


if __name__ == "__main__":
    main()
