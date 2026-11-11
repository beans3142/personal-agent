"""Owner-local stdio MCP adapter. Never expose this process to an untrusted client.

Only explicitly enabled tools are callable. Private data is available to the
local client: its Discord owner filter must be configured before connecting it.
No model, Discord gateway, scheduler or autoreply loop is started here.
"""

from __future__ import annotations

import asyncio
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import time

import jsonschema
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .approvals import ChannelApproval, owner_policy, owner_request_active
from .loop import Tool
from .progress import record as record_progress

log = logging.getLogger(__name__)
READ_TOOLS = frozenset({
    'conversation_search','conversation_history',
    "memory_search", "kb_search", "schedule_list", "web_search", "web_fetch", "mail_list", "kakao_find_recipient",
    "mail_read", "route_search", "map_search", "android_screen", "baemin_search", "n8n_list", "device_nodes", "device_jobs", "recipe_list",
})
PRIVATE_TOOLS = frozenset({"memory_search", "mail_list", "mail_read", "schedule_list", "android_credential", "device_jobs",
                           # The owner's PC and the code sandbox: never from a public channel.
                           "pc_list", "pc_screenshot", "pc_windows", "pc_ui", "pc_act", "pc_open", "pc_find_files",
                           "pc_send_file", "pc_save_file", "pc_system", "pc_shell", "run_code"})
DISPATCH_TOOLS = frozenset({"plan_execute", "recipe_run"})


def scoped_allowlist(allowed, env, private_tools=frozenset(), read_tools=frozenset()):
    scoped = set(allowed) - PRIVATE_TOOLS - set(private_tools) if env.get("DAEVIS_MCP_PUBLIC") == "1" else set(allowed)
    return scoped & (READ_TOOLS | set(read_tools)) if env.get("DAEVIS_MCP_READ_ONLY") == "1" else scoped


def load_private_plugin(data, env):
    """Owner-only tools from the unpublished `agent.private` package, if deployed.

    The package returns (tools, read-only names, private names). Read-only and
    private claims are limited to the plugin's own tools, and a failing plugin
    never stops the public runtime.
    """
    try:
        import importlib
        module = importlib.import_module("agent.private")
        tools, read, private = module.build_tools(data, env)
    except ImportError:
        return [], frozenset(), frozenset()
    except Exception as error:
        log.warning("private plugin disabled (%s)", type(error).__name__)
        return [], frozenset(), frozenset()
    names = {tool.name for tool in tools}
    return list(tools), frozenset(set(read) & names), frozenset(set(private) & names)


def result(text: str, error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], isError=error)


class ToolBridge:
    def __init__(self, tools: list[Tool], allowed: set[str], approve=lambda *_: False, read_tools=frozenset(), progress=None):
        self.tools = {tool.name: tool for tool in tools}
        self.read_tools = READ_TOOLS | frozenset(read_tools)
        if len(self.tools) != len(tools):
            raise ValueError("duplicate tool names")
        if allowed - self.tools.keys():
            raise ValueError("unknown allowlist tools: " + ", ".join(sorted(allowed - self.tools.keys())))
        self.allowed = frozenset(allowed)
        self.approve = approve
        self.progress = progress

    def list_tools(self) -> list[types.Tool]:
        items = []
        for name in sorted(self.allowed):
            tool = self.tools[name]
            schema = deepcopy(tool.parameters)
            schema["additionalProperties"] = False
            items.append(types.Tool(name=name, description=tool.description, inputSchema=schema,
                                    annotations=types.ToolAnnotations(readOnlyHint=name in self.read_tools and not tool.needs_approval,
                                                                      idempotentHint=False)))
        return items

    def call(self, name: str, args: dict) -> types.CallToolResult:
        if name not in self.allowed:
            return result("denied: tool is not allowed", True)
        tool = self.tools[name]
        schema = deepcopy(tool.parameters)
        schema["additionalProperties"] = False
        try:
            jsonschema.validate(args, schema)
        except jsonschema.ValidationError:
            return result("error: invalid tool arguments", True)
        if self.progress:
            try:
                self.progress(name, args)
            except Exception:
                pass  # Progress display must never block or fail the tool.
        try:
            if (tool.needs_approval or (name not in self.read_tools and name not in DISPATCH_TOOLS)) and not self.approve(name, deepcopy(args)):
                return result("denied: owner approval required", True)
            text = str(tool.run(args))
            if not name.startswith("android_") and len(text) > 8000:
                text = text[:8000] + "\n[truncated]"
            failed = text.startswith(("error:", "denied:", "blocked:"))
            if name == 'auth_ensure':
                failed = failed or json.loads(text).get('status') != 'ok'
            if name in DISPATCH_TOOLS:
                payload = json.loads(text)
                failed = failed or payload.get("status") in {"denied", "error", "blocked", "uncertain", "failed"}
            return result(text, failed)
        except Exception as error:
            # Site/browser exceptions can contain passwords, URLs and chat text.
            log.warning("tool failed: %s (%s)", name, type(error).__name__)
            return result("error: tool execution failed; outcome may be uncertain, do not blindly retry", True)


def build_runtime(data: Path, env) -> ToolBridge:
    # Lazy imports: listing/test-only clients need neither Android nor a browser.
    from .android_control import AndroidControl, UiaDevice, build_android_tools
    from .authgate import AuthGate, EnvCredentials, PlaywrightBrowser, build_auth_tool
    from .discord_user import build_discord_user_tool
    from .memory import Memory
    from .naver_mail import build_mail_tool
    from .tools import build_tools
    from .n8n_tools import build_n8n_tools
    from .device_nodes import build_device_tools
    from .kakao_self import build_kakao_self_tools
    from .kakao_watch import build_kakao_send_tools
    from .recipes import RecipeExecutor, build_recipe_tools
    from .login_automation import LoginImagePermission

    mode = env.get("DAEVIS_MCP_APPROVAL", "deny")
    if mode not in {"deny", "discord"}:
        raise ValueError("DAEVIS_MCP_APPROVAL must be deny or discord")
    # "discord": payment buttons appear in the channel the owner asked from.
    def lease_left():
        try:
            return float(env.get("DAEVIS_MCP_REQUEST_EXPIRES", "0")) - time.time()
        except ValueError:
            return 0
    ask = ChannelApproval(env.get("AGENT_OUTBOX"), budget=lease_left) if mode == "discord" else (lambda *_: False)
    approve = owner_policy(env, ask)
    login_permission = LoginImagePermission(data, env.get('DAEVIS_MCP_REQUEST_ID'), lambda: owner_request_active(env))
    data.mkdir(parents=True, exist_ok=True)
    memory = Memory(data / "agent.db")
    browser = PlaywrightBrowser(data / "sessions")
    challenge_handler = None
    if env.get('DAEVIS_LOGIN_IMAGE_ASSIST') == 'desktop':
        from .desktop_login import DesktopChat, DesktopChallenge
        image_approval = login_permission if (data / 'login-policy.json').exists() else approve
        challenge_handler = DesktopChallenge(DesktopChat().ask, data, approve=image_approval)
    notify = lambda *_: None
    if mode == 'discord':
        from .qr_login import discord_sender
        notify = discord_sender(env['DISCORD_BOT_TOKEN'], env['OWNER_ID'])
    policy_present = (data / 'login-policy.json').exists()
    gate = AuthGate(EnvCredentials(env), browser, challenge_handler=challenge_handler, notify=notify,
                   can_login=lambda site: site != 'naver' or not policy_present or login_permission.can_login(site),
                   record_outcome=login_permission.record_outcome if policy_present else lambda *_: None)
    android = AndroidControl(UiaDevice(env.get("ANDROID_SERIAL")), env=env, outbox=data / "outbox", approve=approve, observe_after=True)
    tools = build_tools(memory, data / "schedule.json", env) + build_android_tools(android)
    tools += build_n8n_tools(data / "n8n-workflows.json") + build_device_tools(data, env)
    from .baemin import build_baemin_tools
    tools += build_baemin_tools(android.device)
    tools += build_kakao_self_tools(android.device, data, request_id=env.get('DAEVIS_MCP_REQUEST_ID'))
    tools += build_kakao_send_tools(android.device, data, request_id=env.get('DAEVIS_MCP_REQUEST_ID'), notify_candidates=notify if mode == 'discord' else None)
    tools += [build_auth_tool(gate), build_mail_tool(gate, browser, env)]
    if env.get("DISCORD_BOT_TOKEN"):
        tools.append(build_discord_user_tool(gate, browser, env["DISCORD_BOT_TOKEN"]))
    outbox = lambda: Path(env.get("AGENT_OUTBOX") or data / "outbox")
    from .sandbox import RUNS, build_sandbox_tools
    tools += build_sandbox_tools(outbox, env)
    if env.get("DAEVIS_AGENT_NODE_TOKEN"):
        from .pc_control import BASE
        from .pc_tools import build_pc_tools, relay_link
        tools += build_pc_tools(relay_link(BASE, env["DAEVIS_AGENT_NODE_TOKEN"]), outbox, Path(env.get("DAEVIS_SANDBOX_RUNS", RUNS)))
    plugin_tools, plugin_read, plugin_private = load_private_plugin(data, env)
    tools += plugin_tools
    raw = env.get("DAEVIS_MCP_ALLOW_TOOLS", "memory_search,route_search,map_search,android_screen")
    allowed = scoped_allowlist({name.strip() for name in raw.split(",") if name.strip()}, env, plugin_private, plugin_read)
    underlying = [tool for tool in tools if tool.name in allowed and tool.name not in plugin_read]
    tools += build_recipe_tools(RecipeExecutor(underlying, approve, data / "recipes.json"))
    return ToolBridge(tools, allowed, approve, plugin_read, progress=lambda name, args: record_progress(env.get("AGENT_OUTBOX"), name, args))


def create_server(bridge: ToolBridge, executor: ThreadPoolExecutor) -> Server:
    server = Server("daevis-tools", version="0.1.0")
    # One dedicated thread preserves synchronous Playwright's thread affinity
    # and serializes all Android/browser operations within this process.
    @server.list_tools()
    async def list_tools():
        return bridge.list_tools()

    @server.call_tool()
    async def call_tool(name: str, arguments: dict):
        return await asyncio.get_running_loop().run_in_executor(executor, bridge.call, name, arguments)

    return server


async def serve():
    data = Path(os.environ.get("AGENT_DATA", Path(__file__).resolve().parent.parent / "data"))
    # Linux deployment is exclusive: the legacy gateway owns the same Android
    # and browser state. Refuse startup rather than racing a live gateway.
    runtime_lock = None
    if sys.platform == "linux":
        import fcntl
        check = subprocess.run(["systemctl", "is-active", "personal-agent"], capture_output=True, text=True, timeout=10)
        if check.stdout.strip() in {"active", "activating", "reloading"}:
            raise RuntimeError("Stop personal-agent.service before starting the MCP runtime")
        data.mkdir(parents=True, exist_ok=True)
        runtime_lock = (data / "mcp-runtime.lock").open("a")
        fcntl.flock(runtime_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="daevis-tools") as executor:
        bridge = await asyncio.get_running_loop().run_in_executor(executor, build_runtime, data, os.environ)
        server = create_server(bridge, executor)
        async with stdio_server() as (reader, writer):
            await server.run(reader, writer, server.create_initialization_options())
    if runtime_lock:
        runtime_lock.close()


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING)
    asyncio.run(serve())
