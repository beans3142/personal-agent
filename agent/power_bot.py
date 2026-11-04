"""Small always-on bot that answers only the power commands. It shares the main bot token.

Runs on a separate always-on server so the agent server can be turned on from Discord.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import datetime, timezone

import discord

from .power import PowerControl, power_command

log = logging.getLogger("power")
BOOT_NOTE = "본체 Agent가 Discord에 접속하기까지 1~3분 더 걸려요."


def describe(status: dict) -> str:
    state = status["state"]
    if state == "running" and status.get("launched"):
        minutes = int((datetime.now(timezone.utc) - status["launched"]).total_seconds() // 60)
        return f"켜져 있어요. 켜진 지 약 {minutes}분이에요."
    return {"stopped": "꺼져 있어요.", "pending": "켜지는 중이에요.", "stopping": "꺼지는 중이에요."}.get(state, f"상태: {state}")


class PowerBot(discord.Client):
    def __init__(self, owner_id: int, control: PowerControl):
        intents = discord.Intents.none()
        intents.dm_messages = True
        intents.guild_messages = True
        intents.message_content = True
        super().__init__(intents=intents)
        self.owner_id = owner_id
        self.control = control
        self.lock = asyncio.Lock()

    async def on_message(self, message: discord.Message):
        if message.author.id != self.owner_id or message.author.bot:
            return
        text = message.content.strip()
        if message.guild is not None:
            if self.user is None or not re.search(rf"<@!?{self.user.id}>", text):
                return
            text = re.sub(rf"<@!?{self.user.id}>", "", text).strip()
        command = power_command(text)
        if command is None:
            return
        async with self.lock:
            try:
                await self.handle(message.channel, command)
            except Exception:
                log.exception("power command failed")
                await message.channel.send("전원 명령을 처리하지 못했어요. 전원 봇 로그를 확인해야 해요.")

    async def handle(self, channel, command: str) -> None:
        if command == "status":
            await channel.send(describe(await asyncio.to_thread(self.control.status)))
            return
        if command == "off":
            result = await asyncio.to_thread(self.control.stop)
            await channel.send({"stopping": "서버를 끄고 있어요.", "already_stopped": "이미 꺼져 있거나 꺼지는 중이에요."}.get(result, f"지금은 끌 수 없어요({result})."))
            return
        result = await asyncio.to_thread(self.control.start)
        if result == "already_running":
            await channel.send("이미 켜져 있어요.")
            return
        if result != "started":
            await channel.send(f"지금은 켤 수 없어요({result}). 잠시 뒤 다시 말해 주세요.")
            return
        await channel.send("서버를 켜고 있어요.")
        if await asyncio.to_thread(self.control.wait_running):
            await channel.send(f"서버가 켜졌어요. {BOOT_NOTE}")
        else:
            await channel.send("서버가 6분 안에 켜지지 않았어요. 상태를 확인해야 해요.")


def main() -> None:
    import boto3

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    region = os.environ.get("AWS_REGION", "ap-northeast-2")
    ssm = boto3.client("ssm", region_name=region)

    def parameter(name: str) -> str:
        return ssm.get_parameter(Name=name, WithDecryption=True)["Parameter"]["Value"]

    # Secrets live in Parameter Store, not in files or command history on this shared server.
    owner = os.environ.get("OWNER_ID") or parameter("/personal-agent/owner-id")
    token = os.environ.get("DISCORD_BOT_TOKEN") or parameter("/personal-agent/discord-bot-token")
    ec2 = boto3.client("ec2", region_name=region)
    bot = PowerBot(int(owner), PowerControl(ec2, os.environ["AGENT_INSTANCE_ID"]))
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
