import sys
import types as pytypes

from agent.loop import Tool
from agent.mcp_server import ToolBridge, load_private_plugin, scoped_allowlist


def plugin_tool(name, calls):
    return Tool(name, "plugin tool", {"type": "object", "properties": {"text": {"type": "string"}}, "required": ["text"]},
                lambda a: calls.append(a) or "ok")


def fake_plugin(monkeypatch, build):
    module = pytypes.ModuleType("agent.private")
    module.build_tools = build
    monkeypatch.setitem(sys.modules, "agent.private", module)


def test_missing_private_plugin_adds_nothing(monkeypatch):
    monkeypatch.setitem(sys.modules, "agent.private", None)
    assert load_private_plugin("data", {}) == ([], frozenset(), frozenset())


def test_plugin_tools_and_declared_scopes_are_loaded(monkeypatch, tmp_path):
    calls = []
    fake_plugin(monkeypatch, lambda data, env: ([plugin_tool("plugin_read", calls)], {"plugin_read"}, {"plugin_read"}))
    tools, read, private = load_private_plugin(tmp_path, {})
    assert [t.name for t in tools] == ["plugin_read"]
    assert read == {"plugin_read"} and private == {"plugin_read"}


def test_plugin_cannot_mark_unknown_tools_read_only(monkeypatch, tmp_path):
    fake_plugin(monkeypatch, lambda data, env: ([], {"mail_send"}, set()))
    tools, read, private = load_private_plugin(tmp_path, {})
    assert read == frozenset()


def test_declared_read_only_plugin_tool_runs_without_approval():
    calls = []
    bridge = ToolBridge([plugin_tool("plugin_read", calls)], {"plugin_read"}, read_tools={"plugin_read"})
    assert not bridge.call("plugin_read", {"text": "x"}).isError
    assert calls == [{"text": "x"}]


def test_undeclared_plugin_tool_still_needs_approval():
    calls = []
    bridge = ToolBridge([plugin_tool("plugin_write", calls)], {"plugin_write"})
    assert bridge.call("plugin_write", {"text": "x"}).isError
    assert calls == []


def test_public_scope_drops_plugin_private_tools():
    allowed = scoped_allowlist({"plugin_read", "route_search"}, {"DAEVIS_MCP_PUBLIC": "1"}, private_tools={"plugin_read"})
    assert allowed == {"route_search"}


def test_broken_plugin_does_not_stop_runtime(monkeypatch, tmp_path):
    def boom(data, env):
        raise RuntimeError("secret path /home/x")
    fake_plugin(monkeypatch, boom)
    assert load_private_plugin(tmp_path, {}) == ([], frozenset(), frozenset())
