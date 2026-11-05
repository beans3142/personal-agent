import json
import pytest
from agent.pc_wake import pc_power_command, wake_pc


@pytest.mark.parametrize('text',['대비스 PC 켜줘','대비스 컴퓨터 켜줘','PC 켜줘','내 컴 켜줘'])
def test_explicit_pc_commands_are_distinct_from_agent_power(text):
    assert pc_power_command(text)
    assert not pc_power_command('대비스 온라인')
    assert not pc_power_command('PC 켜는 방법 알려줘')
    assert not pc_power_command('대비스 PC 꺼줘')


def test_magic_packet_goes_only_to_the_owner_configured_host(tmp_path):
    p=tmp_path/'pc.json';p.write_text(json.dumps({'address':'203.0.113.9','mac':'02:00:00:00:00:01'}))
    sent=[]
    class Socket:
        def __enter__(self):return self
        def __exit__(self,*_):pass
        def settimeout(self,_):pass
        def sendto(self,packet,target):sent.append((packet,target));return len(packet)
    result=wake_pc(p,socket_factory=lambda *a:Socket())
    assert sent[0][0]==b'\xff'*6+bytes.fromhex('020000000001')*16
    assert sent[0][1]==('203.0.113.9',9)
    assert result['packet_sent'] and result['boot_confirmed'] is False


def test_missing_configuration_never_sends(tmp_path):
    with pytest.raises(FileNotFoundError):wake_pc(tmp_path/'missing')


def test_power_bot_pc_command_does_not_start_the_arm_server(monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from agent.power_bot import PowerBot
    calls=[]
    monkeypatch.setattr('agent.power_bot.wake_pc',lambda p:calls.append(p))
    channel=SimpleNamespace(send=AsyncMock())
    bot=SimpleNamespace(control=SimpleNamespace(start=lambda:pytest.fail('ARM must stay off')))
    asyncio.run(PowerBot.handle(bot,channel,'pc_on'))
    assert len(calls)==1
    assert '부팅 성공은 아직 확인되지 않았어요' in channel.send.await_args.args[0]


def test_power_bot_keeps_guild_state_for_owner_mention_checks():
    from agent.power_bot import PowerBot
    from types import SimpleNamespace
    assert PowerBot(1,SimpleNamespace()).intents.guilds is True
