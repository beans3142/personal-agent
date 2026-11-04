import json

import httpx
import pytest

from agent.n8n_tools import build_n8n_tools


def tools_for(tmp_path, mapping):
    path = tmp_path / "workflows.json"
    path.write_text(json.dumps(mapping), encoding="utf-8")
    return {tool.name: tool for tool in build_n8n_tools(path)}


def mock_http(monkeypatch, handler):
    client = httpx.Client
    options = []

    def factory(**kwargs):
        options.append(kwargs)
        return client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr("agent.n8n_tools.httpx.Client", factory)
    return options


def test_missing_config_disables_workflows(tmp_path, monkeypatch):
    monkeypatch.setenv("DAEVIS_N8N_WORKFLOWS_FILE", str(tmp_path / "missing.json"))
    tools = {t.name: t for t in build_n8n_tools()}
    assert json.loads(tools["n8n_list"].run({})) == {"workflows": []}
    assert tools["n8n_run"].run({"workflow": "unknown", "payload": {}}).startswith("error:")


def test_only_names_are_listed_and_execution_requires_approval(tmp_path):
    tools = tools_for(tmp_path, {"notify": "/webhook/private-token"})
    assert json.loads(tools["n8n_list"].run({})) == {"workflows": ["notify"]}
    assert not tools["n8n_list"].needs_approval
    assert tools["n8n_run"].needs_approval
    assert set(tools["n8n_run"].parameters["properties"]) == {"workflow", "payload"}


@pytest.mark.parametrize("url", [
    "http://example.com/webhook/x", "http://localhost:5678/webhook/x",
    "https://127.0.0.1:5678/webhook/x", "http://127.0.0.1:8080/webhook/x",
    "http://user:pass@127.0.0.1:5678/webhook/x", "/webhook-test/x",
    "/webhook/../rest", "/webhook/%2e%2e/rest", "/webhook/x?token=secret",
    "/webhook/x#fragment", "/webhook/a\\b", "/webhook/%252e%252e/x",
    "/webhook//x", "/webhook/", "/webhook/x\n", "/webhook/%2frest",
    "http://127.0.0.1:5678/webhook/x?", "http://127.0.0.1:5678/webhook/x#",
])
def test_rejects_unsafe_owner_urls(tmp_path, url):
    with pytest.raises(ValueError, match="n8n workflow configuration"):
        tools_for(tmp_path, {"unsafe": url})


def test_posts_json_once_with_bounded_timeout_and_proxy_disabled(tmp_path, monkeypatch):
    requests = []
    options = mock_http(monkeypatch, lambda r: requests.append(r) or httpx.Response(200, json={"ok": True}))
    tools = tools_for(tmp_path, {"notify": "http://127.0.0.1:5678/webhook/private-token"})
    result = tools["n8n_run"].run({"workflow": "notify", "payload": {"text": "한글"}})
    assert json.loads(result)["status"] == "accepted"
    assert len(requests) == 1
    assert requests[0].method == "POST"
    assert str(requests[0].url) == "http://127.0.0.1:5678/webhook/private-token"
    assert json.loads(requests[0].content) == {"text": "한글"}
    assert options[0]["trust_env"] is False
    assert options[0]["follow_redirects"] is False
    assert 0 < options[0]["timeout"] <= 30


@pytest.mark.parametrize("args", [
    {"workflow": "absent", "payload": {}},
    {"workflow": "notify", "payload": []},
    {"workflow": "notify", "payload": {"x": "a" * 65537}},
    {"workflow": "notify", "payload": {"x": float("nan")}},
    {"workflow": "notify", "payload": {}, "url": "http://evil"},
    {"workflow": "notify"},
])
def test_invalid_payload_never_reaches_network(tmp_path, monkeypatch, args):
    requests = []
    mock_http(monkeypatch, lambda r: requests.append(r) or httpx.Response(200))
    tools = tools_for(tmp_path, {"notify": "/webhook/x"})
    assert tools["n8n_run"].run(args).startswith("error:")
    assert not requests


@pytest.mark.parametrize("status", [302, 400, 500])
def test_unsuccessful_http_does_not_follow_redirect_or_leak_response(tmp_path, monkeypatch, status):
    requests = []
    mock_http(monkeypatch, lambda r: requests.append(r) or httpx.Response(status, text="private secret", headers={"Location": "http://outside.invalid"}))
    tools = tools_for(tmp_path, {"notify": "/webhook/private-token"})
    result = tools["n8n_run"].run({"workflow": "notify", "payload": {}})
    assert result.startswith("error:")
    assert "private" not in result
    assert "uncertain" in result
    assert len(requests) == 1


def test_timeout_is_sanitized_and_not_retried(tmp_path, monkeypatch):
    requests = []
    def handle(request):
        requests.append(request)
        raise httpx.ReadTimeout("secret URL", request=request)
    mock_http(monkeypatch, handle)
    tools = tools_for(tmp_path, {"notify": "/webhook/private-token"})
    result = tools["n8n_run"].run({"workflow": "notify", "payload": {}})
    assert result.startswith("error:") and "uncertain" in result
    assert "secret" not in result and "private-token" not in result
    assert len(requests) == 1


@pytest.mark.parametrize("mapping", [[], {"": "/webhook/x"}, {"name": 123}, {"bad name": "/webhook/x"}])
def test_invalid_config_fails_closed(tmp_path, mapping):
    with pytest.raises(ValueError, match="n8n workflow configuration"):
        tools_for(tmp_path, mapping)
