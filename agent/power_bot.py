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
from .pc_wake import offline_seconds, pc_power_command, wake_pc, wake_reply
from pathlib import Path
from .pc_control import PCRegistry, parse_pc_command, parse_phone_command, pc_handler, BASE

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
        intents.guilds = True
        intents.dm_messages = True
        intents.guild_messages = True
        intents.message_content = True
        super().__init__(intents=intents)
        self.owner_id = owner_id
        self.control = control
        self.lock = asyncio.Lock()
        self.pc_display_lock=asyncio.Lock()
        self.pc_progress_tasks=set()

    async def setup_hook(self):
        from .pc_progress import restore_shutdowns
        self.loop.create_task(restore_shutdowns(self))
        if getattr(self,'pc_registry',None):
            from .voice_relay import relay_loop
            self.loop.create_task(relay_loop(self))
        from .pc_discord import PCHomeView
        self.add_view(PCHomeView(self))
        if getattr(self,'control_store',None):
            from .brief_controls import BriefHomeView
            self.add_view(BriefHomeView(self))

    async def on_message(self, message: discord.Message):
        if message.author.id != self.owner_id or message.author.bot:
            return
        text = message.content.strip()
        if not text and message.guild is None and getattr(self,'pc_registry',None):
            from .voice_message import voice_attachment
            if voice_attachment(message) and not await asyncio.to_thread(self.pc_registry.agent_online):
                # The main agent transcribes voice messages; wake it and let it pick this one up.
                result=await asyncio.to_thread(self.control.start)
                if result in ('started','already_running'):
                    await message.channel.send('서버를 켜고 있어요. 1~3분 뒤 이 음성 메시지를 처리할게요.')
                else:
                    await message.channel.send(f'서버를 켜지 못했어요({result}). 잠시 뒤 다시 보내 주세요.')
            return
        if message.guild is not None:
            if self.user is None or not re.search(rf"<@!?{self.user.id}>", text):
                return
            text = re.sub(rf"<@!?{self.user.id}>", "", text).strip()
        phone_command = parse_phone_command(text)
        if phone_command and getattr(self,'pc_registry',None):
            try:
                await self.handle_phone(message, *phone_command)
            except Exception:
                log.exception('phone request failed')
                await message.channel.send('폰 요청을 처리하지 못했어요. 전화가 걸렸는지 폰에서 확인해 주세요.')
            return
        pc_command = parse_pc_command(text)
        if text in ('브리핑 설정','보고서 설정','브리핑 웹 설정') and getattr(self,'control_store',None):
            from .brief_controls import BriefView
            if text=='브리핑 웹 설정':
                from .owner_controls import BASE
                dm=await (await self.fetch_user(self.owner_id)).create_dm()
                await dm.send('보고서 수신 설정 로그인 링크입니다. 15분간 한 번 사용할 수 있어요.\n'+BASE+'/briefing/#'+self.control_store.issue_login())
                if message.guild:await message.channel.send('본인 DM으로 웹 설정 링크를 보냈어요.')
            else:
                view=BriefView(self);await message.channel.send(view.content(),view=view)
            return
        if pc_command and getattr(self,'pc_registry',None):
            try:
                await self.handle_pc(message, *pc_command)
            except ValueError as error:
                reason=str(error)
                if pc_command[0]=='shutdown' and 'unknown PC' in reason:
                    pcs=self.pc_registry.inventory()
                    if len(pcs)==1 and pcs[0]['online']:
                        from .pc_discord import ShutdownConfirmView
                        await message.channel.send('요청한 이름의 PC를 찾지 못했어요. 연결된 PC는 '+pcs[0]['label']+'입니다. 이 PC를 끌까요?',view=ShutdownConfirmView(self,pcs[0]))
                        return
                await message.channel.send('PC 연결 프로그램이 응답하지 않아 요청을 보내지 않았어요. PC 전원이 꺼졌다는 뜻은 아니에요.' if 'offline' in reason else
                    '요청한 PC를 특정하지 못했어요. 등록된 PC는 '+', '.join(p['label'] for p in self.pc_registry.inventory())+'입니다. 어느 PC를 말씀하셨나요?')
            except Exception:
                log.exception('PC control request failed')
                await message.channel.send('PC 요청을 처리하지 못했어요. 실행 여부 확인 전에는 다시 시도하지 마세요.')
            return
        command = 'pc_on' if pc_power_command(text) else power_command(text)
        if command is None:
            return
        async with self.lock:
            try:
                await self.handle(message.channel, command)
            except Exception:
                log.exception("power command failed")
                await message.channel.send("전원 명령을 처리하지 못했어요. 전원 봇 로그를 확인해야 해요.")

    async def handle_phone(self,message,action,value):
        if action=='register':
            try:
                code=await asyncio.to_thread(self.pc_registry.invite,value,'phone')
            except ValueError:
                await message.channel.send('이미 등록된 폰 이름이에요. 다른 이름으로 `폰 등록 이름`을 보내 주세요.')
                return
            owner=await self.fetch_user(self.owner_id);dm=await owner.create_dm()
            command="curl -fsSL "+BASE+"/phone-install.sh | bash -s -- "+code
            await dm.send(value+' 등록: **폰의 Termux**에 아래 한 줄을 붙여넣으세요. 15분간 한 번만 쓸 수 있어요. 연락처 검색과 전화 걸기 권한만 연결합니다.\n```bash\n'+command+'\n```')
            if message.guild is not None:await message.channel.send('폰 등록 명령을 본인 DM으로 보냈어요.')
            return
        if action=='remove':
            removed=await asyncio.to_thread(self.pc_registry.remove_phones)
            await message.channel.send((', '.join(removed)+' 등록을 해제했어요. 폰의 연결 프로그램은 더 이상 요청을 받지 못해요.') if removed else '등록된 폰이 없어요.')
            return
        from .phone_discord import handle_phone_call
        await handle_phone_call(self,message,value)

    async def confirm_pc_shutdown(self,label):
        from .pc_discord import confirm_shutdown
        owner=await self.fetch_user(self.owner_id)
        return await confirm_shutdown(self,await owner.create_dm(),label)

    async def publish_shutdown(self,job,channel):
        from .pc_progress import publish_shutdown
        return await publish_shutdown(self,job,channel)

    async def handle_pc(self,message,action,label):
        registry=self.pc_registry
        if action=='list':
            from .pc_discord import PCView
            view=PCView(self)
            await message.channel.send(view.content(),view=view)
            return
        if action=='register':
            code=await asyncio.to_thread(registry.invite,label)
            owner=await self.fetch_user(self.owner_id);dm=await owner.create_dm()
            command="& ([scriptblock]::Create((Invoke-WebRequest -UseBasicParsing '"+BASE+"/install.ps1').Content)) -Code '"+code+"'"
            await dm.send(label+' 등록: **해당 PC**의 PowerShell에 아래 한 줄을 붙여넣으세요. 15분간 한 번만 사용할 수 있습니다. 이 PC에 종료·종료 취소 권한을 연결하며, Windows 로그인 시 자동 연결됩니다.\n```powershell\n'+command+'\n```')
            if message.guild is not None:await message.channel.send('PC 등록 명령을 본인 DM으로 보냈어요.')
            return
        if action in ('default','remove'):
            name=await asyncio.to_thread(registry.set_default if action=='default' else registry.remove,label)
            await message.channel.send(name+('을 기본 PC로 설정했어요.' if action=='default' else ' 등록을 해제했어요.'))
            return
        if action=='wake':
            pc=await asyncio.to_thread(registry.resolve,label)
            config_path=Path(os.environ.get('PC_WAKE_CONFIG','data/pc-wake.json'))
            import json
            config=json.loads(config_path.read_text())
            if pc['node']!=config.get('pc_node'):
                await message.channel.send(pc['label']+'의 깨우기 설정은 아직 등록되지 않았어요. 다른 PC를 대신 켜지 않습니다.')
                return
            await asyncio.to_thread(wake_pc,config_path)
            offline=await asyncio.to_thread(offline_seconds,registry,pc['node'])
            await message.channel.send(wake_reply(pc['label'],offline))
            return
        job=await asyncio.to_thread(registry.submit,label,action,'discord-'+str(message.id))
        if action=='shutdown':
            await self.publish_shutdown(job,message.channel)
            return
        status=job['status']
        for _ in range(6):
            if status!='queued' and status!='dispatched':break
            await asyncio.sleep(2)
            status=(await asyncio.to_thread(registry.job,job['id']))['status']
        if status=='succeeded':
            text=(job['label']+'이 종료 요청을 접수했어요. **60초 뒤** Windows에 종료를 요청합니다.\n취소: `'+job['label']+' 종료 취소`') if action=='shutdown' else job['label']+'이 종료 예약 취소 요청을 접수했어요.'
        elif status in ('queued','dispatched'):
            text=job['label']+'에 요청을 전달 중이에요. PC의 접수는 아직 확인되지 않았어요.'
        else:text=job['label']+' 요청이 실행되지 않았거나 확인되지 않았어요. 자동으로 다시 보내지 않습니다.'
        await message.channel.send(text)

    async def handle(self, channel, command: str) -> None:
        if command == 'pc_on':
            try:
                config_path = Path(os.environ.get('PC_WAKE_CONFIG','data/pc-wake.json'))
                await asyncio.to_thread(wake_pc, config_path)
                offline = None
                registry = getattr(self, 'pc_registry', None)
                if registry:
                    import json
                    node = json.loads(config_path.read_text()).get('pc_node')
                    offline = await asyncio.to_thread(offline_seconds, registry, node)
                await channel.send(wake_reply('PC', offline))
            except (OSError, ValueError, KeyError):
                await channel.send('PC 깨우기 신호를 보내지 못했어요. 저장된 PC 설정을 확인해야 해요.')
            return
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
    from http.server import ThreadingHTTPServer
    from threading import Thread
    bot.pc_registry=PCRegistry(Path('data/pc-control.sqlite3'))
    bot.pc_registry.ensure_voice()
    handler=pc_handler(bot.pc_registry,Path('public'))
    config=Path('data/owner-control.json')
    if config.exists():
        import json
        from .owner_controls import ControlStore,control_handler
        bot.control_store=ControlStore('data/owner-control.sqlite3')
        handler=control_handler(handler,bot.control_store,bot,json.loads(config.read_text())['digest'])
    from .phone_secrets import PhoneSecrets, parameter_provider, secret_handler
    handler=secret_handler(handler,PhoneSecrets(bot.pc_registry,parameter_provider(ssm)))
    from .phone_updates import update_handler
    publisher=Path('data/android-publisher.sha256')
    handler=update_handler(handler,bot.pc_registry,'data/android-updates',publisher.read_text().strip() if publisher.exists() else '')
    relay=ThreadingHTTPServer(('127.0.0.1',8792),handler)
    Thread(target=relay.serve_forever,daemon=True).start()
    bot.run(token, log_handler=None)


if __name__ == "__main__":
    main()
