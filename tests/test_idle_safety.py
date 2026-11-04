import asyncio
import configparser
import json
from pathlib import Path
import shutil
import subprocess
from datetime import datetime,timedelta,timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest

from agent.gateway import Gateway


def test_maintenance_lease_expires_instead_of_disabling_shutdown_forever(tmp_path):
    import json
    from agent.power import maintenance_active
    now=datetime.now(timezone.utc)
    path=tmp_path/'maintenance.json'
    path.write_text(json.dumps({'until':(now+timedelta(minutes=15)).isoformat()}))
    assert maintenance_active(path,now)
    assert not maintenance_active(path,now+timedelta(minutes=16))


def test_new_owner_request_during_shutdown_notice_cancels_shutdown(tmp_path,monkeypatch):
    monkeypatch.setattr('agent.gateway.DATA',tmp_path)
    monkeypatch.setenv('IDLE_SHUTDOWN_MINUTES','120')
    closed=[False]
    bot=SimpleNamespace(last_activity=datetime.now(timezone.utc)-timedelta(hours=3),
        turn_lock=asyncio.Lock(),store=tmp_path/'schedule.json',wait_until_ready=AsyncMock(),
        owner_dm=AsyncMock(return_value=object()),is_closed=lambda:closed[0])
    async def notice(*args):
        bot.last_activity=datetime.now(timezone.utc)
        closed[0]=True
    bot.send_long=notice
    monkeypatch.setattr('agent.gateway.asyncio.sleep',AsyncMock())
    shutdown=AsyncMock()
    monkeypatch.setattr('agent.gateway.asyncio.create_subprocess_exec',shutdown)
    asyncio.run(Gateway.idle_shutdown_loop(bot))
    shutdown.assert_not_awaited()


@pytest.mark.parametrize('launch_failure', [False, True])
def test_failed_shutdown_unblocks_requests_waits_for_result_and_backs_off(tmp_path, monkeypatch, launch_failure):
    monkeypatch.setattr('agent.gateway.DATA', tmp_path)
    monkeypatch.setenv('IDLE_SHUTDOWN_MINUTES', '120')
    closed = iter([False, False, True])
    bot = SimpleNamespace(last_activity=datetime.now(timezone.utc)-timedelta(hours=3),
        turn_lock=asyncio.Lock(), store=tmp_path/'schedule.json', wait_until_ready=AsyncMock(),
        owner_dm=AsyncMock(return_value=object()), is_closed=lambda: next(closed), send_long=AsyncMock())
    process = SimpleNamespace(wait=AsyncMock(return_value=1))
    shutdown = AsyncMock(return_value=process)
    if launch_failure:
        shutdown.side_effect = OSError('private filesystem detail')
    monkeypatch.setattr('agent.gateway.asyncio.sleep', AsyncMock())
    monkeypatch.setattr('agent.gateway.asyncio.create_subprocess_exec', shutdown)
    asyncio.run(Gateway.idle_shutdown_loop(bot))
    shutdown.assert_awaited_once_with('systemctl', 'start', 'daevis-idle-poweroff.service')
    assert bot.draining is False
    assert (datetime.now(timezone.utc)-bot.last_activity).total_seconds() < 10
    assert process.wait.await_count == (0 if launch_failure else 1)
    assert bot.send_long.await_count == 2
    failure = bot.send_long.await_args.args[1]
    assert '실패' in failure and 'private' not in failure


def test_successful_shutdown_waits_for_service_result_and_keeps_draining(tmp_path, monkeypatch):
    monkeypatch.setattr('agent.gateway.DATA', tmp_path)
    monkeypatch.setenv('IDLE_SHUTDOWN_MINUTES', '120')
    bot = SimpleNamespace(last_activity=datetime.now(timezone.utc)-timedelta(hours=3),
        turn_lock=asyncio.Lock(), store=tmp_path/'schedule.json', wait_until_ready=AsyncMock(),
        owner_dm=AsyncMock(return_value=object()), is_closed=lambda: False, send_long=AsyncMock())
    process = SimpleNamespace(wait=AsyncMock(return_value=0))
    shutdown = AsyncMock(return_value=process)
    monkeypatch.setattr('agent.gateway.asyncio.sleep', AsyncMock())
    monkeypatch.setattr('agent.gateway.asyncio.create_subprocess_exec', shutdown)
    asyncio.run(Gateway.idle_shutdown_loop(bot))
    shutdown.assert_awaited_once_with('systemctl', 'start', 'daevis-idle-poweroff.service')
    process.wait.assert_awaited_once()
    assert bot.draining is True
    assert bot.send_long.await_count == 1


def test_privileged_unit_is_fixed_root_oneshot_and_gateway_keeps_nnp():
    deploy = Path(__file__).parents[1]/'deploy'
    unit = configparser.ConfigParser(interpolation=None)
    unit.read(deploy/'daevis-idle-poweroff.service')
    assert dict(unit['Service']) == {'type': 'oneshot', 'user': 'root', 'execstart': '/usr/sbin/shutdown -h now'}
    gateway = configparser.ConfigParser()
    gateway.read(deploy/'personal-agent-hermes.service')
    assert gateway.getboolean('Service', 'NoNewPrivileges')


def test_polkit_grants_only_named_unit_start_for_ubuntu():
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node unavailable for executing the policy decision function')
    policy = Path(__file__).parents[1]/'deploy/daevis-idle-poweroff.rules'
    script = r'''
const fs = require('fs'), vm = require('vm');
let decide;
vm.runInNewContext(fs.readFileSync(process.argv[1], 'utf8'), {
  polkit: {addRule: fn => {decide = fn;}, Result: {YES: 'yes'}}
});
const cases = [
  ['ubuntu','org.freedesktop.systemd1.manage-units','daevis-idle-poweroff.service','start'],
  ['other','org.freedesktop.systemd1.manage-units','daevis-idle-poweroff.service','start'],
  ['ubuntu','org.freedesktop.login1.power-off','daevis-idle-poweroff.service','start'],
  ['ubuntu','org.freedesktop.systemd1.manage-units','other.service','start'],
  ['ubuntu','org.freedesktop.systemd1.manage-units','daevis-idle-poweroff.service','stop'],
  ['ubuntu','org.freedesktop.systemd1.manage-units','daevis-idle-poweroff.service','restart'],
  ['ubuntu','org.freedesktop.systemd1.manage-units',null,'start']
];
console.log(JSON.stringify(cases.map(([user,id,unit,verb]) => decide(
  {id, lookup: key => ({unit,verb})[key]}, {user}) || null)));
'''
    result = subprocess.run([node, '-e', script, str(policy)], capture_output=True, text=True, check=True, timeout=5)
    assert json.loads(result.stdout) == ['yes', None, None, None, None, None, None]
