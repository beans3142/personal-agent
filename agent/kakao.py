"""KakaoTalk through the official API, with a small callback server for the login."""

from __future__ import annotations

import json
import logging
import os
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx

AUTHORIZE_URL = "https://kauth.kakao.com/oauth/authorize"
TOKEN_URL = "https://kauth.kakao.com/oauth/token"
SEND_TO_ME_URL = "https://kapi.kakao.com/v2/api/talk/memo/default/send"
CALLBACK_PATH = "/kakao/callback"
TEXT_LIMIT = 200
REFRESH_MARGIN_SECONDS = 120
log = logging.getLogger(__name__)


class KakaoNotConnected(Exception):
    pass


def http_post(url: str, data: dict, headers: dict) -> dict:
    response = httpx.post(url, data=data, headers=headers, timeout=20)
    response.raise_for_status()
    return response.json()


class KakaoClient:
    def __init__(self, rest_key: str, redirect_uri: str, token_path: Path, post=http_post):
        self.rest_key = rest_key
        self.redirect_uri = redirect_uri
        self.token_path = Path(token_path)
        self.post = post
        self.states: set[str] = set()

    def authorize_url(self) -> str:
        state = secrets.token_urlsafe(24)
        self.states.add(state)
        return f"{AUTHORIZE_URL}?" + urlencode({
            "response_type": "code",
            "client_id": self.rest_key,
            "redirect_uri": self.redirect_uri,
            "scope": "talk_message",
            "state": state,
        })

    def finish_login(self, code: str, state: str) -> None:
        if state not in self.states:
            raise ValueError("unknown login state")
        self.states.discard(state)
        tokens = self.post(TOKEN_URL, {
            "grant_type": "authorization_code",
            "client_id": self.rest_key,
            "redirect_uri": self.redirect_uri,
            "code": code,
        }, {})
        self._save({
            "access": tokens["access_token"],
            "refresh": tokens["refresh_token"],
            "expires": time.time() + tokens["expires_in"],
        })

    def _save(self, tokens: dict) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.token_path.with_suffix(".tmp")
        temp.write_text(json.dumps(tokens), encoding="utf-8")
        os.chmod(temp, 0o600)
        temp.replace(self.token_path)

    def _access_token(self) -> str:
        try:
            tokens = json.loads(self.token_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise KakaoNotConnected("KakaoTalk is not connected yet") from None
        if tokens["expires"] > time.time() + REFRESH_MARGIN_SECONDS:
            return tokens["access"]
        fresh = self.post(TOKEN_URL, {
            "grant_type": "refresh_token",
            "client_id": self.rest_key,
            "refresh_token": tokens["refresh"],
        }, {})
        tokens.update(
            access=fresh["access_token"],
            refresh=fresh.get("refresh_token", tokens["refresh"]),
            expires=time.time() + fresh["expires_in"],
        )
        self._save(tokens)
        return tokens["access"]

    def send_to_me(self, text: str) -> str:
        template = {
            "object_type": "text",
            "text": text[:TEXT_LIMIT],
            "link": {"web_url": "https://www.kakao.com", "mobile_web_url": "https://www.kakao.com"},
        }
        result = self.post(
            SEND_TO_ME_URL,
            {"template_object": json.dumps(template, ensure_ascii=False)},
            {"Authorization": f"Bearer {self._access_token()}"},
        )
        if result.get("result_code") != 0:
            raise RuntimeError(f"KakaoTalk send failed: {result}")
        return "sent"


def start_auth_server(kakao: KakaoClient, host: str, port: int) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def _reply(self, status: int, text: str) -> None:
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            parsed = urlparse(self.path)
            if parsed.path != CALLBACK_PATH:
                return self._reply(404, "not found")
            query = parse_qs(parsed.query)
            try:
                kakao.finish_login(query["code"][0], query["state"][0])
            except (KeyError, ValueError):
                return self._reply(400, "로그인 요청이 올바르지 않아요.")
            except Exception:
                log.exception("kakao login failed")
                return self._reply(502, "카카오 토큰을 받지 못했어요.")
            self._reply(200, "카카오톡 연결이 끝났어요. 이 창을 닫아도 돼요.")

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer((host, port), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server
