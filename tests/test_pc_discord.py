import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import discord
from agent.pc_control import PCRegistry
from agent.pc_discord import PCView, PCHomeView


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
