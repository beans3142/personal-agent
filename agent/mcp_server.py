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
import secrets
import subprocess
import sys
import time

import httpx
import jsonschema
from mcp import types
from mcp.server.lowlevel import Server
from mcp.server.stdio import stdio_server

from .loop import Tool

log = logging.getLogger(__name__)
READ_TOOLS = frozenset({
    "memory_search", "schedule_list", "web_search", "web_fetch", "mail_list",
    "mail_read", "route_search", "map_search", "android_screen", "n8n_list", "device_nodes", "device_jobs", "recipe_list",
})
ROUTINE_ANDROID = frozenset({"android_open", "android_click", "android_input", "android_swipe", "android_key"})
PRIVATE_TOOLS = frozenset({"memory_search", "mail_list", "mail_read", "schedule_list", "android_credential", "device_jobs"})
DISPATCH_TOOLS = frozenset({"plan_execute", "recipe_run"})


def owner_request_active(env) -> bool:
    try:
        return bool(env.get("OWNER_ID")) and env.get("DAEVIS_MCP_OWNER_REQUEST") == env["OWNER_ID"] and float(env.get("DAEVIS_MCP_REQUEST_EXPIRES", "0")) > time.time()
    except (ValueError, TypeError):
        return False


def scoped_allowlist(allowed, env):
    scoped = set(allowed) - PRIVATE_TOOLS if env.get("DAEVIS_MCP_PUBLIC") == "1" else set(allowed)
    return scoped & READ_TOOLS if env.get("DAEVIS_MCP_READ_ONLY") == "1" else scoped


def result(text: str, error: bool = False) -> types.CallToolResult:
    return types.CallToolResult(content=[types.TextContent(type="text", text=text)], isError=error)


class ToolBridge:
    def __init__(self, tools: list[Tool], allowed: set[str], approve=lambda *_: False):
        self.tools = {tool.name: tool for tool in tools}
        if len(self.tools) != len(tools):
            raise ValueError("duplicate tool names")
        if allowed - self.tools.keys():
            raise ValueError("unknown allowlist tools: " + ", ".join(sorted(allowed - self.tools.keys())))
        self.allowed = frozenset(allowed)
        self.approve = approve

    def list_tools(self) -> list[types.Tool]:
        items = []
        for name in sorted(self.allowed):
            tool = self.tools[name]
            schema = deepcopy(tool.parameters)
            schema["additionalProperties"] = False
            items.append(types.Tool(name=name, description=tool.description, inputSchema=schema,
                                    annotations=types.ToolAnnotations(readOnlyHint=name in READ_TOOLS and not tool.needs_approval,
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
        try:
            if (tool.needs_approval or (name not in READ_TOOLS and name not in DISPATCH_TOOLS)) and not self.approve(name, deepcopy(args)):
                return result("denied: owner approval required", True)
            text = str(tool.run(args))
            if name != "android_screen" and len(text) > 8000:
                text = text[:8000] + "\n[truncated]"
            failed = text.startswith(("error:", "denied:", "blocked:"))
            if name in DISPATCH_TOOLS:
                payload = json.loads(text)
                failed = failed or payload.get("status") in {"denied", "error", "blocked", "uncertain", "failed"}
            return result(text, failed)
        except Exception as error:
            # Site/browser exceptions can contain passwords, URLs and chat text.
            log.warning("tool failed: %s (%s)", name, type(error).__name__)
            return result("error: tool execution failed; outcome may be uncertain, do not blindly retry", True)


class DiscordApproval:
    """Ask only the configured owner in private; no approval tool is exposed.

    Uses REST, so it does not consume the Discord gateway connection. The
    current legacy gateway must be stopped while Hermes uses shared devices.
    """
    def __init__(self, token: str, owner_id: str, client=None, timeout=120, sleep=time.sleep, clock=time.monotonic):
        if not token or not owner_id.isdecimal():
            raise ValueError("Discord bot token and numeric OWNER_ID are required")
        self.owner_id = owner_id
        self.client = client or httpx.Client(base_url="https://discord.com/api/v10", headers={"Authorization": f"Bot {token}"}, timeout=15)
        self.timeout, self.sleep, self.clock = timeout, sleep, clock

    def _request(self, method, path, **kwargs):
        response = self.client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    def __call__(self, name: str, args: dict) -> bool:
        nonce = secrets.token_hex(8)
        # Full action must fit: never ask approval of a truncated action.
        detail = json.dumps({"tool": name, "arguments": args}, ensure_ascii=False)
        if len(detail) > 1400:
            return False
        channel = self._request("POST", "/users/@me/channels", json={"recipient_id": self.owner_id})["id"]
        sent = self._request("POST", f"/channels/{channel}/messages", json={
            "content": f"대비스 MCP 실행 승인\n{detail}\n\n실행하려면 `승인 {nonce}`, 거절하려면 `거절 {nonce}`를 {self.timeout}초 안에 보내 주세요.",
            "allowed_mentions": {"parse": []},
        })
        after = sent["id"]
        deadline = self.clock() + self.timeout
        while self.clock() < deadline:
            messages = self._request("GET", f"/channels/{channel}/messages", params={"after": after, "limit": 100})
            for message in sorted(messages, key=lambda m: int(m["id"])):
                after = message["id"]
                if message.get("author", {}).get("id") != self.owner_id or message.get("author", {}).get("bot"):
                    continue
                text = message.get("content", "").strip()
                if text == f"승인 {nonce}":
                    return True
                if text == f"거절 {nonce}":
                    return False
            self.sleep(2)
        return False


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
    from .recipes import RecipeExecutor, build_recipe_tools

    mode = env.get("DAEVIS_MCP_APPROVAL", "deny")
    if mode not in {"deny", "discord"}:
        raise ValueError("DAEVIS_MCP_APPROVAL must be deny or discord")
    request_approval = DiscordApproval(env.get("DISCORD_BOT_TOKEN", ""), env.get("OWNER_ID", "")) if mode == "discord" else lambda *_: False
    def approve(name, args):
        # Only the owner-only gateway's local worker gets this short-lived lease.
        # AndroidControl's consequential callback includes `button`, and must
        # still ask the owner even though ordinary navigation is delegated.
        if env.get("DAEVIS_MCP_OWNER_REQUEST"):
            if not owner_request_active(env):
                return False
            if name in ROUTINE_ANDROID and "button" not in args:
                return True
        accepted = request_approval(name, args)
        return accepted and (not env.get("DAEVIS_MCP_OWNER_REQUEST") or owner_request_active(env))
    data.mkdir(parents=True, exist_ok=True)
    memory = Memory(data / "agent.db")
    browser = PlaywrightBrowser(data / "sessions")
    gate = AuthGate(EnvCredentials(env), browser)
    android = AndroidControl(UiaDevice(env.get("ANDROID_SERIAL")), env=env, outbox=data / "outbox", approve=approve)
    tools = build_tools(memory, data / "schedule.json", env) + build_android_tools(android)
    tools += build_n8n_tools(data / "n8n-workflows.json") + build_device_tools(data, env)
    tools += build_kakao_self_tools(android.device, data, request_id=env.get('DAEVIS_MCP_REQUEST_ID'))
    tools += [build_auth_tool(gate), build_mail_tool(gate, browser, env)]
    if env.get("DISCORD_BOT_TOKEN"):
        tools.append(build_discord_user_tool(gate, browser, env["DISCORD_BOT_TOKEN"]))
    raw = env.get("DAEVIS_MCP_ALLOW_TOOLS", "memory_search,route_search,map_search,android_screen")
    allowed = scoped_allowlist({name.strip() for name in raw.split(",") if name.strip()}, env)
    underlying = [tool for tool in tools if tool.name in allowed]
    tools += build_recipe_tools(RecipeExecutor(underlying, approve, data / "recipes.json"))
    return ToolBridge(tools, allowed, approve)


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
