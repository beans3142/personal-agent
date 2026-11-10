import asyncio
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.gateway import Gateway
from agent.hermes_gateway import HermesGateway


def test_hermes_setup_starts_scheduler_heartbeat_and_inherited_idle_task():
    captured = []
    async def schedule_loop():
        pass
    async def heartbeat_loop():
        pass
    async def idle_shutdown_loop():
        pass
    async def voice_backlog():
        pass
    fake = SimpleNamespace(loop=SimpleNamespace(create_task=captured.append),
                           schedule_loop=schedule_loop, heartbeat_loop=heartbeat_loop,
                           idle_shutdown_loop=idle_shutdown_loop, voice_backlog=voice_backlog)
    try:
        asyncio.run(HermesGateway.setup_hook(fake))
        assert [c.cr_code.co_name for c in captured] == [
            "schedule_loop", "heartbeat_loop", "idle_shutdown_loop", "voice_backlog"]
        assert HermesGateway.idle_shutdown_loop is Gateway.idle_shutdown_loop
    finally:
        for coroutine in captured:
            coroutine.close()


@pytest.mark.parametrize("guild", [False, True])
@pytest.mark.parametrize("command", ["대비스 온라인", "데비스 꺼줘", "ㄷㅂㅅ 상태"])
def test_hermes_leaves_exact_power_commands_to_power_bot(guild, command):
    def unexpected(*args, **kwargs):
        pytest.fail("power command reached the main agent")
    fake = SimpleNamespace(owner_id=1, user=SimpleNamespace(id=9),
                           turn=unexpected, engine=SimpleNamespace(run=unexpected),
                           send_long=AsyncMock(), owner_dm=AsyncMock())
    message = SimpleNamespace(author=SimpleNamespace(id=1, bot=False),
                              content=("<@9> " if guild else "") + command,
                              guild=object() if guild else None,
                              channel=SimpleNamespace(send=AsyncMock()),
                              add_reaction=AsyncMock())
    asyncio.run(HermesGateway.on_message(fake, message))
    fake.send_long.assert_not_awaited()
    fake.owner_dm.assert_not_awaited()
    message.channel.send.assert_not_awaited()
    message.add_reaction.assert_not_awaited()


@pytest.mark.parametrize("busy,jobs,disabled,watching", [(True, 0, False, False), (False, 1, False, False), (False, 0, True, False), (False, 0, False, True)])
def test_inherited_idle_task_keeps_busy_scheduled_watching_or_disabled_server_running(monkeypatch, tmp_path, busy, jobs, disabled, watching):
    closed = iter([False, True])
    fake = SimpleNamespace(wait_until_ready=AsyncMock(), is_closed=lambda: next(closed),
                           last_activity=datetime.now(timezone.utc) - timedelta(minutes=121),
                           turn_lock=SimpleNamespace(locked=lambda: busy),
                           kakao_watch=SimpleNamespace(load=lambda: {"enabled": True}) if watching else None,
                           store=tmp_path / "schedule.json", send_long=AsyncMock(), owner_dm=AsyncMock())
    if disabled:
        monkeypatch.setenv("IDLE_SHUTDOWN_MINUTES", "0")
    else:
        monkeypatch.delenv("IDLE_SHUTDOWN_MINUTES", raising=False)
    monkeypatch.setattr("agent.gateway.scheduler.load_jobs", lambda path: [None] * jobs)
    monkeypatch.setattr("agent.gateway.asyncio.sleep", AsyncMock())
    shutdown = AsyncMock()
    monkeypatch.setattr("agent.gateway.asyncio.create_subprocess_exec", shutdown)
    asyncio.run(HermesGateway.idle_shutdown_loop(fake))
    shutdown.assert_not_awaited()
    fake.send_long.assert_not_awaited()
