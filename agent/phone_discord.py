"""Owner-only phone calls through the phone node: "엄마에게 전화 걸어줘".

The phone searches its own contacts. Only names and masked numbers come back;
an ambiguous name becomes owner-only buttons that carry a short-lived token.
"""
import asyncio
import logging

import discord

log = logging.getLogger(__name__)
WAIT_STEPS, WAIT_SECONDS = 15, 2


async def _outcome(bot, job, sleep):
    for _ in range(WAIT_STEPS):
        state = await asyncio.to_thread(bot.pc_registry.job_result, job['id'])
        if state['status'] not in ('queued', 'dispatched'):
            return state
        await sleep(WAIT_SECONDS)
    return {'status': 'waiting', 'result': None}


def describe(state, label):
    result = state.get('result') or {}
    if state['status'] == 'waiting':
        return label + '에 요청을 보냈지만 아직 응답이 없어요. 전화가 걸렸는지는 확인되지 않았어요.'
    if state['status'] != 'succeeded':
        return '전화를 걸지 못했어요. 폰의 Termux:API 권한이나 연결 상태를 확인해 주세요. 자동으로 다시 걸지 않습니다.'
    if result.get('status') == 'called':
        return f"📞 {result.get('name')}({result.get('number')})에게 전화를 걸었어요."
    if result.get('status') == 'not_found':
        return '폰 연락처에서 그 이름을 찾지 못했어요. 연락처에 저장된 이름으로 다시 말해 주세요.'
    if result.get('status') == 'blocked':
        return '긴급번호나 짧은 번호는 이 기능으로 걸지 않아요. 직접 걸어 주세요.'
    return '폰의 응답을 이해하지 못했어요. 전화가 걸렸는지 폰에서 확인해 주세요.'


class PhoneChoiceView(discord.ui.View):
    def __init__(self, bot, label, candidates, sleep=asyncio.sleep):
        super().__init__(timeout=240)  # The phone forgets the choices after 300 seconds.
        self.bot, self.label, self.sleep, self.done = bot, label, sleep, False
        for candidate in candidates[:5]:
            button = discord.ui.Button(label=f"{candidate['name']} {candidate['number']}"[:80], style=discord.ButtonStyle.primary)
            button.callback = self._caller(candidate['token'])
            self.add_item(button)

    async def interaction_check(self, interaction):
        if interaction.user.id == self.bot.owner_id:
            return True
        await interaction.response.send_message('본인만 전화를 걸 수 있어요.', ephemeral=True)
        return False

    def _caller(self, token):
        async def call(interaction):
            if self.done:
                await interaction.response.send_message('이미 선택했어요.', ephemeral=True)
                return
            self.done = True
            await interaction.response.edit_message(view=None)
            try:
                job = await asyncio.to_thread(self.bot.pc_registry.phone_call, None, {'token': token}, 'discord-' + str(interaction.id))
                text = describe(await _outcome(self.bot, job, self.sleep), self.label)
            except ValueError:
                text = '폰 연결 프로그램이 응답하지 않아 전화를 걸지 않았어요.'
            await interaction.followup.send(text, allowed_mentions=discord.AllowedMentions.none())
        return call


async def handle_phone_call(bot, message, name, sleep=asyncio.sleep):
    try:
        job = await asyncio.to_thread(bot.pc_registry.phone_call, None, {'name': name}, 'discord-' + str(message.id))
    except ValueError as error:
        text = ('폰이 아직 등록되지 않았어요. `폰 등록`이라고 보내면 Termux 설치 명령을 DM으로 보내 드려요.'
                if 'no phone' in str(error) else '폰 연결 프로그램이 응답하지 않아 전화를 걸지 않았어요. 폰의 Termux가 켜져 있는지 확인해 주세요.')
        await message.channel.send(text)
        return
    state = await _outcome(bot, job, sleep)
    result = state.get('result') or {}
    if state['status'] == 'succeeded' and result.get('status') == 'choose':
        view = PhoneChoiceView(bot, job['label'], result.get('candidates', []), sleep)
        await message.channel.send('같은 이름이 여러 명이에요. 누구에게 걸까요?', view=view, allowed_mentions=discord.AllowedMentions.none())
        return
    await message.channel.send(describe(state, job['label']), allowed_mentions=discord.AllowedMentions.none())
