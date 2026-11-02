import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord

from agent.gateway import ApprovalView, Gateway, instructions_for


class Channel:
    def __init__(self, channel_id):
        self.id = channel_id
        self.send = AsyncMock()

    @asynccontextmanager
    async def typing(self):
        yield


def gateway():
    dm = Channel(12)
    calls = []
    fake = SimpleNamespace(
        owner_id=1, user=SimpleNamespace(id=9), turn_lock=asyncio.Lock(),
        owner_dm=AsyncMock(return_value=dm),
        turn=lambda *args: calls.append(args) or "private result",
        approver=lambda channel: channel,
        send_long=AsyncMock(), send_outbox=AsyncMock(),
    )
    return fake, dm, calls


def message(author=1, content="<@9> test", guild=True):
    return SimpleNamespace(author=SimpleNamespace(id=author, bot=False), content=content,
                           guild=object() if guild else None, channel=Channel(20),
                           add_reaction=AsyncMock(), remove_reaction=AsyncMock())


def test_only_owner_mentions_run_and_results_stay_in_original_channel():
    bot, dm, calls = gateway()
    msg = message()
    asyncio.run(Gateway.on_message(bot, msg))
    assert calls[0][:2] == ("guild:20", "test")
    assert calls[0][2] is msg.channel
    bot.send_long.assert_awaited_once_with(msg.channel, "private result")
    bot.send_outbox.assert_awaited_once_with(msg.channel)
    assert msg.channel.send.await_count == 0
    assert bot.owner_dm.await_count == 0
    assert [call.args[0] for call in msg.add_reaction.await_args_list] == ["⏳", "✅"]
    msg.remove_reaction.assert_awaited_once_with("⏳", bot.user)


def test_reaction_permission_failure_still_returns_result():
    bot, _, calls = gateway()
    msg = message()
    msg.add_reaction.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "denied")
    asyncio.run(Gateway.on_message(bot, msg))
    assert calls
    bot.send_long.assert_awaited_once_with(msg.channel, "private result")


def test_execution_error_gets_failure_reaction():
    bot, _, _ = gateway()
    def fail(*args):
        raise RuntimeError("failure")
    bot.turn = fail
    msg = message()
    asyncio.run(Gateway.on_message(bot, msg))
    assert msg.add_reaction.await_args_list[-1].args == ("❌",)


def test_other_users_and_unmentioned_guild_messages_do_not_invoke_model():
    for msg in (message(author=2), message(content="hello"), message(content="<@90> test")):
        bot, _, calls = gateway()
        asyncio.run(Gateway.on_message(bot, msg))
        assert not calls
        assert bot.owner_dm.await_count == 0
        assert msg.channel.send.await_count == 0


def test_dm_still_works_without_mention_and_empty_mention_is_ignored():
    bot, dm, calls = gateway()
    msg = message(content="hello", guild=False)
    asyncio.run(Gateway.on_message(bot, msg))
    assert calls[0][:2] == (str(msg.channel.id), "hello")
    assert bot.owner_dm.await_count == 0
    bot, _, calls = gateway()
    asyncio.run(Gateway.on_message(bot, message(content="<@!9>")))
    assert not calls


def test_approval_buttons_reject_nonowner():
    async def run():
        view = ApprovalView(owner_id=1)
        other = SimpleNamespace(user=SimpleNamespace(id=2), response=SimpleNamespace(send_message=AsyncMock()))
        assert not await view.interaction_check(other)
        assert not view.approved
        assert await view.interaction_check(SimpleNamespace(user=SimpleNamespace(id=1)))
    asyncio.run(run())


def test_public_prompt_does_not_automatically_load_private_facts():
    memory = SimpleNamespace(recent=lambda: [{"id": 1, "text": "private address"}])
    assert "private address" in instructions_for(memory)
    assert "private address" not in instructions_for(memory, public=True)
