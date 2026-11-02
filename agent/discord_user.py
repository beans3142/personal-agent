"""Posts to a Discord channel as the owner, through the owner's browser session on this server."""

from __future__ import annotations

import httpx

from .authgate import SITES, AuthBlocked
from .loop import Tool

API = "https://discord.com/api/v10"
MESSAGE_BOX = "[role='textbox']"


def normalize(name: str) -> str:
    return "".join(name.split()).lower()


def pick_named(items: list[dict], name: str) -> dict:
    wanted = normalize(name)
    matches = [item for item in items if normalize(item["name"]) == wanted]
    if len(matches) != 1:
        matches = [item for item in items if wanted in normalize(item["name"])]
    if len(matches) != 1:
        raise RuntimeError(f"cannot pick one match for {name}: {[item['name'] for item in matches]}")
    return matches[0]


def channel_url(token: str, server: str, channel: str, get=httpx.get) -> str:
    headers = {"Authorization": f"Bot {token}", "User-Agent": "personal-agent/1.0"}

    def fetch(path: str) -> list[dict]:
        response = get(f"{API}{path}", headers=headers, timeout=20)
        response.raise_for_status()
        return response.json()

    guild = pick_named(fetch("/users/@me/guilds"), server)
    text_channels = [item for item in fetch(f"/guilds/{guild['id']}/channels") if item["type"] == 0]
    return f"https://discord.com/channels/{guild['id']}/{pick_named(text_channels, channel)['id']}"


def build_discord_user_tool(gate, browser, bot_token: str) -> Tool:
    def run(args: dict) -> str:
        try:
            gate.ensure("discord")
        except AuthBlocked as error:
            return f"blocked: {error}"
        url = channel_url(bot_token, args["server"], args["channel"])
        page = browser.open(SITES["discord"])
        try:
            page.goto(url)
            page.send_message(MESSAGE_BOX, args["text"])
            browser.save(SITES["discord"])
        finally:
            browser.close()
        return "sent as owner"

    return Tool(
        "discord_post_as_me",
        "사용자 본인 계정으로 Discord 채널에 메시지를 보낸다. 사용자가 요청한 경우에만 쓴다.",
        {
            "type": "object",
            "properties": {"server": {"type": "string"}, "channel": {"type": "string"}, "text": {"type": "string"}},
            "required": ["server", "channel", "text"],
        },
        run,
    )
