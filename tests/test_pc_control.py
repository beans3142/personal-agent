import json
import time
import importlib.util
from pathlib import Path
import pytest
from agent.pc_control import PCRegistry, parse_pc_command


def test_registration_codes_are_single_use_and_names_do_not_silently_fallback(tmp_path):
    r=PCRegistry(tmp_path/'pc.sqlite3',clock=lambda:1000)
    code=r.invite('노트북')
    registered=r.redeem(code)
    assert registered['node'] and registered['token']
    assert code not in (tmp_path/'pc.sqlite3').read_text(errors='ignore')
    with pytest.raises(ValueError):r.redeem(code)
    with pytest.raises(ValueError):r.resolve('없는 PC')
    assert r.resolve(None)['label']=='노트북'
    with pytest.raises(ValueError):r.invite('서버')


def test_shutdown_requires_online_device_and_expires_before_a_later_boot(tmp_path):
    r=PCRegistry(tmp_path/'pc.sqlite3',clock=lambda:1000)
    a=r.redeem(r.invite('데스크톱'));b=r.redeem(r.invite('노트북'))
    with pytest.raises(ValueError,match='offline'):r.submit('데스크톱','shutdown','x')
    r.poll(a['node'],a['token'])
    job=r.submit('데스크톱','shutdown','x')
    assert job['node']==a['node']
    assert r.poll(b['node'],b['token']) is None
    r.clock=lambda:1020
    assert r.poll(a['node'],a['token']) is None
    assert r.job(job['id'])['status']=='expired'


def test_cancel_prevents_undelivered_shutdown(tmp_path):
    r=PCRegistry(tmp_path/'pc.sqlite3',clock=lambda:1000);a=r.redeem(r.invite('데스크톱'))
    r.poll(a['node'],a['token']);old=r.submit(None,'shutdown','a');cancel=r.submit(None,'cancel','b')
    assert r.job(old['id'])['status']=='cancelled'
    assert r.poll(a['node'],a['token'])['action']=='pc_cancel_shutdown'


@pytest.mark.parametrize('text,action,target',[
    ('대비스 내 PC 꺼줘','shutdown',None),('데스크톱 꺼줘','shutdown','데스크톱'),
    ('노트북 종료 취소','cancel','노트북'),('PC 등록 노트북','register','노트북'),
    ('PC 목록','list',None),('기본 PC 노트북','default','노트북')])
def test_command_target_is_explicit(text,action,target):
    assert parse_pc_command(text)==(action,target)


@pytest.mark.parametrize('text',['대비스 오프라인','서버 꺼줘','비서 서버 꺼줘','데스크톱 끄는 법 설명해줘'])
def test_server_and_discussion_are_never_pc_shutdown(text):
    assert parse_pc_command(text) is None


def client():
    p=Path(__file__).parents[1]/'deploy/device-node.py'
    spec=importlib.util.spec_from_file_location('pc_node',p);m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m


def test_shutdown_wait_is_cancellable_and_never_forces_apps(monkeypatch):
    c=client();config={'actions':['pc_shutdown','pc_cancel_shutdown'],'allow_shutdown':True}
    monkeypatch.setattr(c.sys,'platform','win32')
    monkeypatch.setattr(c.time,'monotonic',lambda:100)
    c.execute_action({'id':'one','action':'pc_shutdown','payload':{}},config)
    calls=[]
    assert c.run_due_shutdown(config,run=lambda *a,**kw:calls.append(a),now=159) is False
    c.execute_action({'action':'pc_cancel_shutdown','payload':{}},config)
    assert c.run_due_shutdown(config,run=lambda *a,**kw:calls.append(a),now=161) is False
    assert not calls
    c.execute_action({'id':'two','action':'pc_shutdown','payload':{}},config)
    assert c.run_due_shutdown(config,run=lambda *a,**kw:calls.append(a),now=161) is True
    assert calls[0][0][-3:]==['/s','/t','0']
    assert '/f' not in calls[0][0]


def test_shutdown_is_not_implicitly_enabled_on_phone_or_old_pc_config():
    c=client()
    with pytest.raises(ValueError):c.execute_action({'action':'pc_shutdown','payload':{}},{'actions':['pc_shutdown']})


def test_expired_registration_cannot_create_a_device(tmp_path):
    r=PCRegistry(tmp_path/'pc.sqlite3',clock=lambda:1000);code=r.invite('노트북');r.clock=lambda:2000
    with pytest.raises(ValueError):r.redeem(code)
    assert r.inventory()==[]


def test_default_switch_is_explicit_and_revocation_keeps_other_pc(tmp_path):
    r=PCRegistry(tmp_path/'pc.sqlite3',clock=lambda:1000)
    a=r.redeem(r.invite('데스크톱'));b=r.redeem(r.invite('노트북'))
    r.set_default('노트북');assert r.resolve(None)['node']==b['node']
    r.remove('데스크톱')
    assert not r.authenticate(a['node'],a['token']) and r.authenticate(b['node'],b['token'])


def test_exact_pc_suffix_name_wins_over_a_different_registered_pc(tmp_path):
    r=PCRegistry(tmp_path/'pc.sqlite3');a=r.redeem(r.invite('업무 PC'));b=r.redeem(r.invite('업무'))
    assert r.resolve('업무 PC')['node']==a['node']
    assert r.resolve('업무')['node']==b['node']
    assert parse_pc_command('서버 말고 내 PC 꺼줘')==('shutdown',None)
    assert parse_pc_command('내 PC 말고 서버 꺼줘') is None


def test_anonymous_http_cannot_dispatch_pc_shutdown(tmp_path):
    from agent.pc_control import pc_handler
    from http.server import ThreadingHTTPServer
    import threading,urllib.request,urllib.error
    r=PCRegistry(tmp_path/'pc.sqlite3');a=r.redeem(r.invite('데스크톱'))
    server=ThreadingHTTPServer(('127.0.0.1',0),pc_handler(r,tmp_path));t=threading.Thread(target=server.serve_forever,daemon=True);t.start()
    try:
        req=urllib.request.Request('http://127.0.0.1:'+str(server.server_port)+'/poll',data=json.dumps({'node':a['node']}).encode())
        with pytest.raises(urllib.error.HTTPError) as e:urllib.request.urlopen(req,timeout=3)
        assert e.value.code==401
        assert r.list_jobs()==[]
    finally:server.shutdown();server.server_close();t.join(timeout=3)


def test_reused_pc_name_does_not_reuse_old_physical_wake_target(tmp_path,monkeypatch):
    import asyncio
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from agent.power_bot import PowerBot
    config=tmp_path/'wake.json';config.write_text(json.dumps({'pc_node':'old-device'}))
    monkeypatch.setenv('PC_WAKE_CONFIG',str(config))
    monkeypatch.setattr('agent.power_bot.wake_pc',lambda _:pytest.fail('must not wake another device'))
    bot=SimpleNamespace(pc_registry=SimpleNamespace(resolve=lambda _: {'label':'デスクトップ','node':'new-device'}))
    message=SimpleNamespace(channel=SimpleNamespace(send=AsyncMock()))
    asyncio.run(PowerBot.handle_pc(bot,message,'wake','デスクトップ'))
    assert '등록되지 않았어요' in message.channel.send.await_args.args[0]
