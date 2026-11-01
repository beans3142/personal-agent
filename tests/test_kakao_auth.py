import json
import threading
import time
import urllib.error
import urllib.request
from urllib.parse import parse_qs, urlparse

import pytest

from agent.kakao import KakaoClient, KakaoNotConnected, start_auth_server


def client(tmp_path, **kwargs):
    defaults = dict(
        rest_key="key",
        redirect_uri="http://server:8765/kakao/callback",
        token_path=tmp_path / "kakao.json",
        post=lambda url, data, headers: {},
    )
    return KakaoClient(**{**defaults, **kwargs})


def test_authorize_url_has_key_scope_redirect_and_state(tmp_path):
    query = parse_qs(urlparse(client(tmp_path).authorize_url()).query)
    assert query["client_id"] == ["key"]
    assert query["scope"] == ["talk_message"]
    assert query["redirect_uri"] == ["http://server:8765/kakao/callback"]
    assert query["state"][0]


def test_finish_login_rejects_wrong_state_and_saves_nothing(tmp_path):
    kakao = client(tmp_path)
    kakao.authorize_url()
    with pytest.raises(ValueError):
        kakao.finish_login("code", "forged")
    assert not (tmp_path / "kakao.json").exists()


def test_state_can_be_used_only_once(tmp_path):
    kakao = client(tmp_path, post=lambda *a: {"access_token": "a", "refresh_token": "r", "expires_in": 3600})
    state = parse_qs(urlparse(kakao.authorize_url()).query)["state"][0]
    kakao.finish_login("code", state)
    with pytest.raises(ValueError):
        kakao.finish_login("code", state)


def test_finish_login_saves_tokens(tmp_path):
    calls = []

    def post(url, data, headers):
        calls.append(data)
        return {"access_token": "a", "refresh_token": "r", "expires_in": 3600}

    kakao = client(tmp_path, post=post)
    state = parse_qs(urlparse(kakao.authorize_url()).query)["state"][0]
    kakao.finish_login("the-code", state)
    assert calls[0]["code"] == "the-code"
    assert calls[0]["grant_type"] == "authorization_code"
    assert json.loads((tmp_path / "kakao.json").read_text())["refresh"] == "r"


def test_send_without_login_says_not_connected(tmp_path):
    with pytest.raises(KakaoNotConnected):
        client(tmp_path).send_to_me("안녕")


def save(tmp_path, **values):
    (tmp_path / "kakao.json").write_text(json.dumps(values))


def test_send_to_me_posts_text_template_with_token(tmp_path):
    save(tmp_path, access="tok", refresh="r", expires=time.time() + 3600)
    calls = []

    def post(url, data, headers):
        calls.append((url, data, headers))
        return {"result_code": 0}

    client(tmp_path, post=post).send_to_me("안녕")
    url, data, headers = calls[0]
    assert url.endswith("/v2/api/talk/memo/default/send")
    assert headers["Authorization"] == "Bearer tok"
    assert json.loads(data["template_object"])["text"] == "안녕"


def test_expired_token_is_refreshed_and_old_refresh_kept(tmp_path):
    save(tmp_path, access="old", refresh="r1", expires=0)
    calls = []

    def post(url, data, headers):
        calls.append(data)
        return {"access_token": "new", "expires_in": 3600, "result_code": 0}

    client(tmp_path, post=post).send_to_me("안녕")
    assert calls[0]["grant_type"] == "refresh_token"
    saved = json.loads((tmp_path / "kakao.json").read_text())
    assert saved["access"] == "new"
    assert saved["refresh"] == "r1"


def test_long_text_is_cut_to_kakao_limit(tmp_path):
    save(tmp_path, access="tok", refresh="r", expires=time.time() + 3600)
    calls = []
    client(tmp_path, post=lambda u, d, h: calls.append(d) or {"result_code": 0}).send_to_me("가" * 500)
    assert len(json.loads(calls[0]["template_object"])["text"]) <= 200


def test_failed_send_raises(tmp_path):
    save(tmp_path, access="tok", refresh="r", expires=time.time() + 3600)
    with pytest.raises(RuntimeError):
        client(tmp_path, post=lambda *a: {"result_code": -1}).send_to_me("안녕")


def test_callback_server_finishes_login_and_rejects_forgery(tmp_path):
    kakao = client(tmp_path, post=lambda *a: {"access_token": "a", "refresh_token": "r", "expires_in": 3600})
    state = parse_qs(urlparse(kakao.authorize_url()).query)["state"][0]
    server = start_auth_server(kakao, "127.0.0.1", 0)
    port = server.server_address[1]
    try:
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/kakao/callback?code=c&state=bad")
        assert not (tmp_path / "kakao.json").exists()
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/other")
        ok = urllib.request.urlopen(f"http://127.0.0.1:{port}/kakao/callback?code=c&state={state}")
        assert ok.status == 200
        assert (tmp_path / "kakao.json").exists()
    finally:
        server.shutdown()
