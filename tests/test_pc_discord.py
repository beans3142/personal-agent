import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import discord
from agent.pc_control import PCRegistry
from agent.pc_discord import PCView, PCHomeView


def test_clear_mention_shutdown_does_not_ask_again(tmp_path):
    async def check():
        from agent.power_bot import PowerBot
        r=PCRegistry(tmp_path/'pcs.db');r.redeem(r.invite('데스크톱'))
        bot=SimpleNamespace(owner_id=1,user=SimpleNamespace(id=42),pc_registry=r,handle_pc=AsyncMock())
        message=SimpleNamespace(author=SimpleNamespace(id=1,bot=False),guild=object(),content='<@42> 대비스 내 데스크탑 꺼줘',channel=SimpleNamespace(send=AsyncMock()))
        await PowerBot.on_message(bot,message)
        bot.handle_pc.assert_awaited_once_with(message,'shutdown','내 데스크탑')
        assert r.resolve(bot.handle_pc.await_args.args[2])['label']=='데스크톱'
        message.channel.send.assert_not_awaited()
    asyncio.run(check())


def test_shutdown_confirmation_is_one_shot_and_rechecks_connection(tmp_path):
    async def check():
        from agent.pc_discord import ShutdownConfirmView
        r=PCRegistry(tmp_path/'pcs.db');a=r.redeem(r.invite('데스크톱'));r.poll(a['node'],a['token'])
        bot=SimpleNamespace(owner_id=1,pc_registry=r,handle_pc=AsyncMock())
        view=ShutdownConfirmView(bot,r.resolve(None))
        interaction=SimpleNamespace(id=99,response=SimpleNamespace(edit_message=AsyncMock()),followup=SimpleNamespace(send=AsyncMock()))
        assert [b.label for b in view.children]==['예','아니오']
        await view.children[0].callback(interaction)
        await view.children[0].callback(interaction)
        assert bot.handle_pc.await_count==1
        view=ShutdownConfirmView(bot,r.resolve(None));r.revoke(a['node'])
        await view.children[0].callback(interaction)
        assert bot.handle_pc.await_count==1
        view=ShutdownConfirmView(bot,r.resolve(None))
        await view.children[1].callback(interaction)
        assert bot.handle_pc.await_count==1
    asyncio.run(check())


def test_panel_has_registered_pc_selector_and_specific_power_actions(tmp_path):
    async def check():
        r=PCRegistry(tmp_path/'pcs.db');a=r.redeem(r.invite('데스크톱'));r.poll(a['node'],a['token'])
        r.redeem(r.invite('노트북'))
        bot=SimpleNamespace(owner_id=1,pc_registry=r)
        view=PCView(bot)
        select=next(c for c in view.children if isinstance(c,discord.ui.Select))
        assert {o.label.split(' · ')[0] for o in select.options}=={'데스크톱','노트북'}
        assert view.selected=='데스크톱'
        labels={c.label for c in view.children if isinstance(c,discord.ui.Button)}
        assert {'60초 뒤 종료','종료 취소','PC 추가','기본 PC 설정'}<=labels
        wrong=SimpleNamespace(user=SimpleNamespace(id=2),response=SimpleNamespace(send_message=AsyncMock()))
        assert await view.interaction_check(wrong) is False
    asyncio.run(check())


def test_home_button_can_be_restored_after_bot_restart():
    async def check():
        view=PCHomeView(SimpleNamespace(owner_id=1))
        assert view.is_persistent()
        assert view.children[0].custom_id=='daevis_pc_open'
    asyncio.run(check())


def test_old_panel_cannot_act_on_a_different_pc_reusing_the_name(tmp_path):
    async def check():
        import pytest
        r=PCRegistry(tmp_path/'pcs.db');r.redeem(r.invite('노트북'));view=PCView(SimpleNamespace(owner_id=1,pc_registry=r))
        r.remove('노트북');r.redeem(r.invite('노트북'))
        with pytest.raises(ValueError):view.assert_target()
    asyncio.run(check())
