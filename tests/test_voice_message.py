import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent import voice_message
from agent.gateway import Gateway


def attachment(name='voice-message.ogg', ctype='audio/ogg', size=40_000):
    return SimpleNamespace(filename=name, content_type=ctype, size=size, read=AsyncMock(return_value=b'OggS...'))


def dm_message(mid=101, attachments=None, content='', author=1, when=None):
    channel = SimpleNamespace(id=12, send=AsyncMock(return_value=SimpleNamespace(id=9, edit=AsyncMock(), delete=AsyncMock())))
    @__import__('contextlib').asynccontextmanager
    async def typing():
        yield
    channel.typing = typing
    return SimpleNamespace(id=mid, content=content, guild=None, channel=channel, author=SimpleNamespace(id=author, bot=False),
                           attachments=attachments if attachments is not None else [attachment()],
                           created_at=when or datetime.now(timezone.utc), add_reaction=AsyncMock(), remove_reaction=AsyncMock())


@pytest.mark.parametrize("att,ok", [
    (attachment(), True),
    (attachment('memo.m4a', 'audio/mp4'), True),
    (attachment('photo.png', 'image/png'), False),
    (attachment(size=9_000_000), False),
])
def test_voice_attachments_are_recognised(att, ok):
    assert (voice_message.voice_attachment(SimpleNamespace(attachments=[att])) is not None) is ok


def gateway(tmp_path, monkeypatch, heard):
    monkeypatch.setattr(voice_message, 'transcribe_bytes', lambda data: heard)
    monkeypatch.setattr(voice_message, 'STATE', tmp_path / 'voice-processed.json')
    calls, relayed = [], []
    bot = SimpleNamespace(owner_id=1, user=SimpleNamespace(id=9), turn_lock=asyncio.Lock(),
                          turn=lambda *a: calls.append(a) or '답이에요', approver=lambda c: None,
                          send_long=AsyncMock(), send_outbox=AsyncMock(), watch_approvals=AsyncMock(),
                          relay_voice=lambda text, key: relayed.append(text))
    return bot, calls, relayed


def test_voice_message_is_answered_like_typed_text(tmp_path, monkeypatch):
    bot, calls, relayed = gateway(tmp_path, monkeypatch, '내일 날씨 어때')
    msg = dm_message()
    asyncio.run(Gateway.on_message(bot, msg))
    assert calls and calls[0][1] == '내일 날씨 어때'
    assert '🎤 내일 날씨 어때' in msg.channel.send.await_args_list[0].args[0]
    assert relayed == []


def test_spoken_power_and_phone_commands_go_to_the_power_bot(tmp_path, monkeypatch):
    bot, calls, relayed = gateway(tmp_path, monkeypatch, '엄마한테 전화 걸어줘')
    asyncio.run(Gateway.on_message(bot, dm_message()))
    assert relayed == ['엄마한테 전화 걸어줘'] and calls == []


def test_unclear_audio_gets_a_short_reply_and_no_request(tmp_path, monkeypatch):
    bot, calls, relayed = gateway(tmp_path, monkeypatch, '')
    msg = dm_message()
    asyncio.run(Gateway.on_message(bot, msg))
    assert calls == [] and '알아듣지' in msg.channel.send.await_args.args[0]


def test_each_voice_message_is_handled_once(tmp_path, monkeypatch):
    bot, calls, _ = gateway(tmp_path, monkeypatch, '안녕')
    msg = dm_message(mid=777)
    asyncio.run(Gateway.on_message(bot, msg))
    asyncio.run(Gateway.on_message(bot, msg))
    assert len(calls) == 1


def test_messages_left_while_the_server_was_off_are_processed_after_boot(tmp_path, monkeypatch):
    monkeypatch.setattr(voice_message, 'STATE', tmp_path / 'voice-processed.json')
    now = datetime.now(timezone.utc)
    recent, old, text = dm_message(mid=1, when=now - timedelta(minutes=3)), dm_message(mid=2, when=now - timedelta(hours=2)), dm_message(mid=3, content='hi', attachments=[])
    voice_message.mark_processed(4)
    done = dm_message(mid=4, when=now - timedelta(minutes=1))
    async def history(limit, after):
        for m in (recent, old, text, done):
            if m.created_at > after:
                yield m
    handled = []
    async def on_message(m):
        handled.append(m.id)
    bot = SimpleNamespace(owner_id=1, owner_dm=AsyncMock(return_value=SimpleNamespace(history=history)), on_message=on_message)
    asyncio.run(voice_message.process_backlog(bot))
    assert handled == [1]


def test_power_bot_starts_the_server_for_a_voice_message_when_the_agent_is_offline():
    from agent.power_bot import PowerBot
    started = []
    registry = SimpleNamespace(agent_online=lambda: False)
    bot = SimpleNamespace(owner_id=1, user=SimpleNamespace(id=9), pc_registry=registry,
                          control=SimpleNamespace(start=lambda: started.append(1) or 'started'))
    msg = dm_message()
    asyncio.run(PowerBot.on_message(bot, msg))
    assert started and '켜' in msg.channel.send.await_args.args[0]


def test_power_bot_leaves_voice_messages_to_a_running_agent():
    from agent.power_bot import PowerBot
    bot = SimpleNamespace(owner_id=1, user=SimpleNamespace(id=9), pc_registry=SimpleNamespace(agent_online=lambda: True),
                          control=SimpleNamespace(start=lambda: pytest.fail('must not start')))
    msg = dm_message()
    asyncio.run(PowerBot.on_message(bot, msg))
    msg.channel.send.assert_not_awaited()
