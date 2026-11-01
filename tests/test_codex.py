import base64
import json
import time

import pytest

from agent.codex import CodexAuth, CodexError, account_id_from, parse_sse


def jwt(payload):
    body = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode().rstrip("=")
    return f"h.{body}.s"


def event(name, **data):
    return [f"event: {name}", f"data: {json.dumps({'type': name, **data})}", ""]


def test_parse_collects_finished_output_items():
    item = {"type": "function_call", "name": "x", "arguments": "{}", "call_id": "1"}
    lines = event("response.output_item.done", item=item) + event("response.completed", response={"output": []})
    assert parse_sse(lines) == [item]


def test_parse_ignores_reasoning_items():
    lines = event("response.output_item.done", item={"type": "reasoning", "id": "r"}) + event("response.completed", response={})
    assert parse_sse(lines) == []


def test_parse_raises_on_failed_response():
    lines = event("response.failed", response={"error": {"message": "usage limit"}})
    with pytest.raises(CodexError, match="usage limit"):
        parse_sse(lines)


def test_parse_raises_when_stream_ends_early():
    with pytest.raises(CodexError):
        parse_sse(event("response.output_item.done", item={"type": "message"}))


def test_account_id_comes_from_token_claim():
    token = jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "acc-1"}})
    assert account_id_from(token) == "acc-1"


def test_valid_token_is_not_refreshed(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"access": "a", "refresh": "r", "expires": time.time() + 3600}))
    calls = []
    auth = CodexAuth(path, refresher=lambda refresh: calls.append(refresh))
    assert auth.access_token() == "a"
    assert calls == []


def test_expired_token_is_refreshed_and_saved(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"access": "old", "refresh": "r1", "expires": time.time() - 1}))
    auth = CodexAuth(path, refresher=lambda refresh: {"access_token": "new", "refresh_token": "r2", "expires_in": 3600})
    assert auth.access_token() == "new"
    saved = json.loads(path.read_text())
    assert saved["access"] == "new"
    assert saved["refresh"] == "r2"
    assert saved["expires"] > time.time()


def test_refresh_keeps_old_refresh_token_when_none_returned(tmp_path):
    path = tmp_path / "auth.json"
    path.write_text(json.dumps({"access": "old", "refresh": "r1", "expires": 0}))
    CodexAuth(path, refresher=lambda refresh: {"access_token": "new", "expires_in": 3600}).access_token()
    assert json.loads(path.read_text())["refresh"] == "r1"


def test_missing_login_is_clear_error(tmp_path):
    with pytest.raises(CodexError, match="login"):
        CodexAuth(tmp_path / "none.json").access_token()
