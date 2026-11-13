import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.device_nodes import DeviceStore
from agent.pc_control import PCRegistry, parse_phone_command


def node_module():
    spec = importlib.util.spec_from_file_location('device_node_phone', Path(__file__).parents[1] / 'deploy/device-node.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


CONTACTS = [{"name": "엄마", "number": "010-1234-5678"}, {"name": "김철수", "number": "+82 10-2222-3333"},
            {"name": "김철수(회사)", "number": "02-555-6666"}, {"name": "엄마", "number": "01012345678"},
            {"name": "긴급", "number": "112"}]


def runner(contacts=CONTACTS, fail_call=False):
    calls = []
    def run(argv, **kwargs):
        calls.append(argv)
        if argv[0].endswith("termux-contact-list"):
            return SimpleNamespace(returncode=0, stdout=json.dumps(contacts, ensure_ascii=False))
        if argv[0].endswith("termux-telephony-call"):
            if fail_call:
                raise OSError("no permission")
            return SimpleNamespace(returncode=0, stdout="")
        raise AssertionError(argv)
    return run, calls


CONFIG = {"allow_phone_call": True, "actions": ["phone_call"]}


def which(name):
    return "/data/data/com.termux/files/usr/bin/" + name


# --- phone node -----------------------------------------------------------

def test_single_exact_contact_is_called_and_number_is_masked_in_the_result():
    node = node_module(); run, calls = runner(); config = dict(CONFIG)
    result = node.phone_call_action({"name": "엄마"}, config, run=run, which=which)
    assert result == {"status": "called", "name": "엄마", "number": "010-****-5678"}
    assert calls[-1][1] == "01012345678"


def test_ambiguous_name_returns_candidates_without_calling():
    node = node_module(); run, calls = runner(); config = dict(CONFIG)
    result = node.phone_call_action({"name": "김철수"}, config, run=run, which=which)
    assert result["status"] == "called"  # Exact name wins over the partial "김철수(회사)".
    result = node.phone_call_action({"name": "철수"}, config, run=run, which=which)
    assert result["status"] == "choose"
    assert {c["name"] for c in result["candidates"]} == {"김철수", "김철수(회사)"}
    assert all("****" in c["number"] for c in result["candidates"])
    assert sum(1 for argv in calls if argv[0].endswith("termux-telephony-call")) == 1


def test_choice_token_calls_the_chosen_number_once_and_expires():
    node = node_module(); run, calls = runner(); config = dict(CONFIG); now = [1000.0]
    choice = node.phone_call_action({"name": "철수"}, config, run=run, which=which, clock=lambda: now[0])
    token = next(c["token"] for c in choice["candidates"] if c["name"] == "김철수(회사)")
    assert node.phone_call_action({"token": token}, config, run=run, which=which, clock=lambda: now[0])["status"] == "called"
    assert calls[-1][1] == "025556666"
    with pytest.raises(ValueError):
        node.phone_call_action({"token": token}, config, run=run, which=which, clock=lambda: now[0])
    other = node.phone_call_action({"name": "철수"}, config, run=run, which=which, clock=lambda: now[0])
    now[0] += 301
    with pytest.raises(ValueError):
        node.phone_call_action({"token": other["candidates"][0]["token"]}, config, run=run, which=which, clock=lambda: now[0])


def test_unknown_name_and_emergency_numbers_never_dial():
    node = node_module(); run, calls = runner(); config = dict(CONFIG)
    assert node.phone_call_action({"name": "없는사람"}, config, run=run, which=which)["status"] == "not_found"
    assert node.phone_call_action({"name": "긴급"}, config, run=run, which=which)["status"] == "blocked"
    assert not any(argv[0].endswith("termux-telephony-call") for argv in calls)


def test_phone_calls_require_explicit_local_permission_and_valid_payload():
    node = node_module(); run, _ = runner()
    with pytest.raises(ValueError):
        node.phone_call_action({"name": "엄마"}, {"actions": ["phone_call"]}, run=run, which=which)
    for payload in ({}, {"name": ""}, {"name": "엄마", "token": "x"}, {"name": "a" * 41}):
        with pytest.raises(ValueError):
            node.phone_call_action(payload, dict(CONFIG), run=run, which=which)


def test_missing_termux_api_is_reported_not_crashed():
    node = node_module(); run, _ = runner()
    with pytest.raises(OSError):
        node.phone_call_action({"name": "엄마"}, dict(CONFIG), run=run, which=lambda name: None)


def test_run_once_sends_the_action_result_with_the_acknowledgement(tmp_path, monkeypatch):
    node = node_module()
    posts = []
    def post(config, endpoint, body):
        posts.append((endpoint, body))
        if endpoint == '/poll':
            return {'job': {'id': 'j1', 'action': 'phone_call', 'payload': {'name': '엄마'}, 'expires': 9e9}}
        return {'ok': True}
    monkeypatch.setattr(node, 'post', post)
    monkeypatch.setattr(node, 'execute_action', lambda job, config: {'status': 'called', 'name': '엄마', 'number': '010-****-5678'})
    import sqlite3
    state = sqlite3.connect(':memory:'); state.execute('CREATE TABLE journal(id TEXT PRIMARY KEY,status TEXT,acknowledged INTEGER)')
    node.run_once({'node': 'phone-1'}, state)
    ack = posts[-1]
    assert ack[0] == '/ack' and ack[1]['result']['status'] == 'called'


# --- server ---------------------------------------------------------------

def test_store_keeps_small_job_results_and_rejects_large_ones(tmp_path):
    store = DeviceStore(tmp_path / 'n.sqlite3', clock=lambda: 1000)
    token = store.enroll('phone-1', ['phone_call'])
    job = store.enqueue('phone-1', 'phone_call', {'name': '엄마'}, 'k1', 30)
    store.poll('phone-1', token)
    with pytest.raises(ValueError):
        store.ack('phone-1', token, job['id'], 'succeeded', {'x': 'y' * 3000})
    store.ack('phone-1', token, job['id'], 'succeeded', {'status': 'called'})
    assert store.job_result(job['id']) == {'status': 'succeeded', 'result': {'status': 'called'}}


def test_phone_enrollment_is_separate_from_pcs_and_grants_calls_and_alarms(tmp_path):
    r = PCRegistry(tmp_path / 'pc.sqlite3', clock=lambda: 1000)
    reply = r.redeem(r.invite('내 폰', kind='phone'))
    assert reply['actions'] == ['phone_alarm_at', 'phone_call'] and reply['node'].startswith('phone-')
    assert r.inventory() == []
    assert [p['label'] for p in r.phone_inventory()] == ['내 폰']
    with pytest.raises(ValueError, match='offline'):
        r.phone_call(None, {'name': '엄마'}, 'k')
    r.poll(reply['node'], reply['token'])
    job = r.phone_call(None, {'name': '엄마'}, 'k')
    dispatched = r.poll(reply['node'], reply['token'])
    assert dispatched['action'] == 'phone_call' and dispatched['payload'] == {'name': '엄마'}
    r.ack(reply['node'], reply['token'], job['id'], 'succeeded', {'status': 'called'})
    assert r.job_result(job['id'])['result'] == {'status': 'called'}


def test_pc_shutdown_acknowledgement_still_works_without_a_result(tmp_path):
    r = PCRegistry(tmp_path / 'pc.sqlite3', clock=lambda: 1000)
    pc = r.redeem(r.invite('데스크톱'))
    r.poll(pc['node'], pc['token'])
    job = r.submit(None, 'shutdown', 'x')
    r.poll(pc['node'], pc['token'])
    r.ack(pc['node'], pc['token'], job['id'], 'succeeded')
    assert r.shutdown_progress(job['id'])['phase'] == 'waiting'


@pytest.mark.parametrize("text,expected", [
    ("엄마에게 전화 걸어줘", ("call", "엄마")),
    ("김철수한테 전화해줘", ("call", "김철수")),
    ("대비스 엄마한테 전화 좀 걸어줘", ("call", "엄마")),
    ("아빠께 통화 걸어줘", ("call", "아빠")),
    ("폰 등록", ("register", "내 폰")),
    ("휴대폰 등록 갤럭시", ("register", "갤럭시")),
    ("전화번호 알려줘", None),
    ("오늘 날씨 어때", None),
    ("엄마에게 카톡 보내줘", None),
])
def test_phone_command_parsing(text, expected):
    assert parse_phone_command(text) == expected


# --- Discord flow -----------------------------------------------------------

class Registry:
    def __init__(self, results):
        self.results, self.calls = list(results), []
    def phone_call(self, label, payload, key):
        self.calls.append(payload)
        return {'id': 'j%d' % len(self.calls), 'status': 'queued', 'label': '내 폰'}
    def job_result(self, job_id):
        return self.results.pop(0)


def flow(results):
    from agent.phone_discord import handle_phone_call
    channel = SimpleNamespace(send=AsyncMock())
    bot = SimpleNamespace(owner_id=1, pc_registry=Registry(results))
    message = SimpleNamespace(id=5, channel=channel)
    asyncio.run(handle_phone_call(bot, message, '엄마', sleep=AsyncMock()))
    return channel, bot


def test_discord_reports_the_call_with_masked_number():
    channel, _ = flow([{'status': 'dispatched', 'result': None},
                       {'status': 'succeeded', 'result': {'status': 'called', 'name': '엄마', 'number': '010-****-5678'}}])
    text = channel.send.await_args.args[0]
    assert '엄마' in text and '010-****-5678' in text and '걸었' in text


def test_discord_offers_owner_only_buttons_for_candidates():
    channel, _ = flow([{'status': 'succeeded', 'result': {'status': 'choose', 'candidates': [
        {'token': 'a1', 'name': '김철수', 'number': '010-****-3333'}, {'token': 'b2', 'name': '김철수(회사)', 'number': '02-****-6666'}]}}])
    view = channel.send.await_args.kwargs['view']
    assert [item.label for item in view.children] == ['김철수 010-****-3333', '김철수(회사) 02-****-6666']


@pytest.mark.parametrize("result,phrase", [
    ({'status': 'succeeded', 'result': {'status': 'not_found'}}, '찾지 못'),
    ({'status': 'succeeded', 'result': {'status': 'blocked'}}, '긴급'),
    ({'status': 'failed', 'result': None}, '걸지 못'),
])
def test_discord_explains_other_outcomes(result, phrase):
    channel, _ = flow([result])
    assert phrase in channel.send.await_args.args[0]


def test_discord_does_not_claim_a_call_while_still_waiting():
    channel, _ = flow([{'status': 'queued', 'result': None}] * 20)
    assert '아직' in channel.send.await_args.args[0]


def test_power_bot_routes_phone_commands_before_other_commands():
    from agent.power_bot import PowerBot
    calls = []
    async def handle_phone(message, action, value):
        calls.append((action, value))
    bot = SimpleNamespace(owner_id=1, user=SimpleNamespace(id=9), pc_registry=object(), handle_phone=handle_phone)
    message = SimpleNamespace(author=SimpleNamespace(id=1, bot=False), content='<@9> 엄마한테 전화 걸어줘', guild=object(),
                              channel=SimpleNamespace(send=AsyncMock()))
    asyncio.run(PowerBot.on_message(bot, message))
    assert calls == [('call', '엄마')]


def test_registration_dm_contains_one_termux_command(tmp_path):
    from agent.power_bot import PowerBot
    dm = SimpleNamespace(send=AsyncMock())
    owner = SimpleNamespace(create_dm=AsyncMock(return_value=dm))
    bot = SimpleNamespace(owner_id=1, pc_registry=PCRegistry(tmp_path / 'pc.sqlite3'), fetch_user=AsyncMock(return_value=owner))
    message = SimpleNamespace(guild=object(), channel=SimpleNamespace(send=AsyncMock()))
    asyncio.run(PowerBot.handle_phone(bot, message, 'register', '내 폰'))
    text = dm.send.await_args.args[0]
    assert 'curl -fsSL https://game.daehoon.games/pc-power/phone-install.sh | bash -s -- ' in text
    assert text.count('```') == 2
    message.channel.send.assert_awaited_once()


def test_install_script_is_served_and_is_valid_bash(tmp_path):
    import shutil, subprocess
    script = Path(__file__).parents[1] / 'deploy/install-phone.sh'
    text = script.read_text(encoding='utf-8')
    assert '\r' not in text and text.startswith('#!/data/data/com.termux/files/usr/bin/bash')
    assert "'allow_phone_call': True" in text and "'actions': ['phone_call']" in text
    if shutil.which('bash'):
        assert subprocess.run(['bash', '-n', str(script)]).returncode == 0
    from agent.pc_control import pc_handler
    handler = pc_handler(PCRegistry(tmp_path / 'pc.sqlite3'), tmp_path)
    assert handler.do_GET  # '/phone-install.sh' is mapped below.
    import inspect
    assert "'/phone-install.sh':'install-phone.sh'" in inspect.getsource(pc_handler)


def test_phone_registration_can_be_removed_and_registered_again(tmp_path):
    r = PCRegistry(tmp_path / 'pc.sqlite3', clock=lambda: 1000)
    first = r.redeem(r.invite('내 폰', kind='phone'))
    assert parse_phone_command('폰 등록 해제') == ('remove', None)
    assert r.remove_phones() == ['내 폰']
    with pytest.raises(PermissionError):
        r.poll(first['node'], first['token'])
    assert r.redeem(r.invite('내 폰', kind='phone'))['node'] != first['node']
