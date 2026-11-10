"""Power-bot side of the PC voice assistant.

A PC sends recognised speech to the internal 'voice' node. Rule commands (PC,
phone, server power) run here at once; anything else goes to the main agent,
which is started first when it is off. The answer comes back on the PC's own
request receipt, so the PC can speak it.
"""
import asyncio
import logging
import zlib
from types import SimpleNamespace

import discord

from .pc_control import parse_pc_command, parse_phone_command
from .pc_wake import pc_power_command
from .power import power_command
from .speech import spoken

log = logging.getLogger(__name__)
WAIT_STEPS, WAIT_SECONDS = 280, 1  # Up to ~280 s: boot (1-3 min) plus the answer.


class Recorder:
    """Posts to the owner DM like a normal channel and remembers what was said."""
    def __init__(self, channel):
        self.channel, self.texts = channel, []

    async def send(self, content=None, *args, **kwargs):
        message = await self.channel.send(content, *args, **kwargs)
        if content:
            self.texts.append(content)
        return message

    def __getattr__(self, name):
        return getattr(self.channel, name)


async def _run_rule(bot, recorder, message, text):
    phone = parse_phone_command(text)
    if phone:
        await bot.handle_phone(message, *phone)
        return True
    pc = parse_pc_command(text)
    if pc:
        try:
            await bot.handle_pc(message, *pc)
        except ValueError:
            await recorder.send('PC 요청을 처리하지 못했어요. PC 이름이나 연결 상태를 확인해 주세요.')
        return True
    command = 'pc_on' if pc_power_command(text) else power_command(text)
    if command:
        await bot.handle(recorder, command)
        return True
    return False


async def handle_voice_job(bot, job, sleep=asyncio.sleep):
    registry, payload = bot.pc_registry, job['payload']
    text, via = payload['text'].strip(), payload.get('via', 'voice')
    dm = await (await bot.fetch_user(bot.owner_id)).create_dm()
    await dm.send(('⌨️ ' if via == 'typed' else '🎤 ') + text, allowed_mentions=discord.AllowedMentions.none())
    recorder = Recorder(dm)
    message = SimpleNamespace(id=zlib.crc32(job['id'].encode()), channel=recorder, guild=None)
    try:
        if await _run_rule(bot, recorder, message, text):
            reply = spoken(recorder.texts[-1]) if recorder.texts else '처리했어요.'
            result = {'reply': reply, 'text': '\n'.join(recorder.texts)[:1500]} if recorder.texts else {'reply': reply}
            await asyncio.to_thread(registry.voice_finish, job['id'], 'succeeded', result)
            return
        extra = {}
        # A busy agent stops polling while it answers; only a real boot is announced.
        if not await asyncio.to_thread(registry.agent_online) and await asyncio.to_thread(bot.control.start) != 'already_running':
            extra = {'say': '서버를 켜고 있어요. 1~3분 걸려요.'}
            await asyncio.to_thread(registry.voice_progress, job['id'], extra)
        forwarded = await asyncio.to_thread(registry.forward_to_agent, text, 'voice-' + job['id'], via)
        shown = None
        for _ in range(WAIT_STEPS):
            state = await asyncio.to_thread(registry.job_result, forwarded['id'])
            if state['status'] == 'succeeded':
                await asyncio.to_thread(registry.voice_finish, job['id'], 'succeeded', state.get('result') or {'reply': '처리했어요.'})
                return
            if state['status'] not in ('queued', 'dispatched'):
                break
            # The agent's current step and any payment question, copied to the PC's receipt.
            progress = state.get('result')
            if progress and progress != shown:
                shown = progress
                await asyncio.to_thread(registry.voice_progress, job['id'], {**extra, **progress})
            await sleep(WAIT_SECONDS)
        await asyncio.to_thread(registry.voice_finish, job['id'], 'failed', {'reply': '본체 서버에서 답을 받지 못했어요. Discord에서 다시 요청해 주세요.'})
    except Exception:
        log.exception('voice request failed')
        await asyncio.to_thread(registry.voice_finish, job['id'], 'failed', {'reply': '요청을 처리하지 못했어요.'})


async def relay_loop(bot, interval=1.0):
    while True:
        try:
            for job in await asyncio.to_thread(bot.pc_registry.voice_jobs):
                bot.loop.create_task(handle_voice_job(bot, job))
        except Exception:
            log.exception('voice relay poll failed')
        await asyncio.sleep(interval)
