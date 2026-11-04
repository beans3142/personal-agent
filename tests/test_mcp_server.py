import asyncio
import json
import os
import sys

import pytest

from agent.loop import Tool
from agent.mcp_server import ToolBridge, create_server
from agent.mcp_server import DiscordApproval


def sample(name="memory_search", approval=False):
    return Tool(name, "test tool", {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]}, lambda a: a["text"], approval)


def test_allowlist_is_enforced_even_for_direct_calls():
    bridge = ToolBridge([sample(), sample("hidden")], {"memory_search"})
    assert [t.name for t in bridge.list_tools()] == ["memory_search"]
    assert bridge.call("hidden", {"text": "secret"}).isError
    assert bridge.call("memory_search", {"text": "ok"}).content[0].text == "ok"


def test_unknown_allowlist_name_fails_startup():
    with pytest.raises(ValueError):
        ToolBridge([sample()], {"typo"})


@pytest.mark.parametrize("args", [{}, {"text": 12}, {"text": "ok", "owner_id": "spoof"}])
def test_invalid_arguments_cannot_execute(args):
    assert ToolBridge([sample()], {"memory_search"}).call("memory_search", args).isError


@pytest.mark.parametrize("name", ["mail_send", "discord_post_as_me", "memory_save", "schedule_add", "android_input", "auth_ensure"])
def test_side_effects_require_approval_even_when_legacy_flag_is_false(name):
    result = ToolBridge([sample(name)], {name}).call(name, {"text": "do it"})
    assert result.isError
    assert "approval" in result.content[0].text


def test_approval_receives_exact_validated_arguments():
    seen = []
    bridge = ToolBridge([sample("mail_send")], {"mail_send"}, approve=lambda n, a: seen.append((n, a)) or True)
    assert not bridge.call("mail_send", {"text": "hello"}).isError
    assert seen == [("mail_send", {"text": "hello"})]


def test_legacy_approval_flag_remains_authoritative():
    assert ToolBridge([sample(approval=True)], {"memory_search"}).call("memory_search", {"text": "hello"}).isError


def test_write_tools_are_never_advertised_as_safe_to_retry():
    tool = ToolBridge([sample("mail_send")], {"mail_send"}).list_tools()[0]
    assert tool.annotations.readOnlyHint is False
    assert tool.annotations.idempotentHint is False


def test_exception_does_not_disclose_secret():
    def fail(args):
        raise RuntimeError("password-value")
    tool = sample()
    tool.run = fail
    result = ToolBridge([tool], {"memory_search"}).call("memory_search", {"text": "ok"})
    assert result.isError
    assert "password-value" not in result.content[0].text


def test_tool_error_is_reported_as_error():
    tool = sample()
    tool.run = lambda a: "error: stale snapshot"
    assert ToolBridge([tool], {"memory_search"}).call("memory_search", {"text": "ok"}).isError


@pytest.mark.parametrize("author,content,expected", [
    ("123", "승인 abc", True),
    ("999", "승인 abc", False),
    ("123", "승인 wrong", False),
    ("123", "거절 abc", False),
])
def test_discord_approval_requires_owner_and_fresh_nonce(monkeypatch, author, content, expected):
    import httpx
    monkeypatch.setattr("agent.mcp_server.secrets.token_hex", lambda n: "abc")
    now = [0]
    requests = []
    def handle(request):
        requests.append(request)
        if request.url.path.endswith("/users/@me/channels"):
            return httpx.Response(200, json={"id": "5"})
        if request.method == "POST":
            return httpx.Response(200, json={"id": "10"})
        return httpx.Response(200, json=[{"id": "11", "author": {"id": author}, "content": content}])
    client = httpx.Client(base_url="https://discord.com/api/v10", transport=httpx.MockTransport(handle))
    approval = DiscordApproval("test-token", "123", client=client, timeout=1, sleep=lambda n: now.__setitem__(0, now[0] + n), clock=lambda: now[0])
    assert approval("mail_send", {"body": "hello"}) is expected
    payload = json.loads(requests[1].content)
    assert '"body": "hello"' in payload["content"]
    assert payload["allowed_mentions"] == {"parse": []}


def test_approval_api_failure_does_not_execute_tool():
    seen = []
    tool = sample("mail_send")
    tool.run = lambda args: seen.append(args) or "sent"
    def fail(*args):
        raise RuntimeError("token secret")
    output = ToolBridge([tool], {"mail_send"}, approve=fail).call("mail_send", {"text": "hello"})
    assert output.isError and not seen
    assert "token secret" not in output.content[0].text


def test_actual_stdio_initialize_list_and_call(tmp_path):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def run():
        params = StdioServerParameters(command=sys.executable, args=["-m", "agent.mcp_server"], env={**os.environ, "AGENT_DATA": str(tmp_path), "DAEVIS_MCP_ALLOW_TOOLS": "memory_search", "DAEVIS_MCP_APPROVAL": "deny"})
        async with stdio_client(params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools = await session.list_tools()
                assert [t.name for t in tools.tools] == ["memory_search"]
                result = await session.call_tool("memory_search", {"query": "nothing"})
                assert not result.isError
                assert json.loads(result.content[0].text) == []
                assert (await session.call_tool("memory_save", {"text": "no"})).isError
    asyncio.run(run())
