import pytest
from agent.owner_controls import ControlStore
from agent.owner_controls import power_intent_allowed


def test_report_preferences_are_independent_and_validated(tmp_path):
    store=ControlStore(tmp_path/'control.sqlite3')
    result=store.preferences({'trend_weekly':False,'jobs':False})
    assert result['trend_daily'] and not result['trend_weekly'] and not result['jobs']
    assert ControlStore(tmp_path/'control.sqlite3').preferences()==result
    with pytest.raises(ValueError):store.preferences({'made_up':True})
    with pytest.raises(ValueError):store.preferences({'jobs':'false'})


def test_web_signin_is_single_use_and_sessions_expire(tmp_path):
    now=[1000.0];store=ControlStore(tmp_path/'control.sqlite3',clock=lambda:now[0])
    code=store.issue_login();session=store.login(code)
    assert store.authorized(session)
    with pytest.raises(ValueError):store.login(code)
    assert not store.authorized('wrong')
    now[0]+=31*86400
    assert not store.authorized(session)


def test_operation_receipt_prevents_repeated_execution_after_restart(tmp_path):
    path=tmp_path/'c.sqlite3';store=ControlStore(path);calls=[]
    assert store.once('request-1',lambda:calls.append(1) or {'status':'scheduled'})=={'status':'scheduled'}
    assert ControlStore(path).once('request-1',lambda:calls.append(2))=={'status':'scheduled'}
    assert calls==[1]


@pytest.mark.parametrize('action,text,expected',[
    ('pc_shutdown','작업 끝났으니 내 컴퓨터 전원 내려줘',True),
    ('pc_shutdown','등록된 거 하나뿐이잖아 그거 꺼 데스크탑',True),
    ('server_stop','오늘은 여기까지 하자. 비서 서버 종료해줘',True),
    ('pc_shutdown','서버 꺼줘',False),('server_stop','내 PC 꺼줘',False),
    ('pc_shutdown','컴퓨터 끄지 마',False),('server_stop','서버를 끌 수 있어?',False),
    ('server_stop','"서버 꺼줘"를 영어로 번역해',False),('server_stop','서버 꺼줘 라는 문장은 무슨 뜻이야',False),
    ('pc_cancel','내 PC 종료 취소해',True),('pc_wake','노트북 깨워줘',True),
    ('pc_shutdown','내 PC 종료가 안 되는데 왜지?',False),
    ('server_stop','서버 종료해도 돼?',False),('server_stop','서버 끄는 방법 알려줘',False),
    ('pc_shutdown','내 PC 꺼줘서 고마워',False),('pc_shutdown','내 PC를 종료해줄래?',True),
    ('pc_shutdown','작업 끝나면 내 PC 꺼줘',False),('server_stop','내일 서버 종료해줘',False),
    ('server_stop','대비스야 내 PC 전원 내려줘',False),('server_stop','서버 말고 내 PC 꺼줘',False),
])
def test_current_request_gates_power_actions(action,text,expected):
    assert power_intent_allowed(action,text)==expected


def test_power_dispatch_uses_fake_executor_and_deduplicates_server_stop(tmp_path,monkeypatch):
    from types import SimpleNamespace
    from agent.owner_controls import dispatch_control
    calls=[];timers=[]
    class Timer:
        def __init__(self,delay,action):self.delay=delay;self.action=action
        def start(self):timers.append(self.delay)
    monkeypatch.setattr('threading.Timer',Timer)
    pc={'node':'test-node','label':'데스크톱','is_default':1}
    registry=SimpleNamespace(resolve=lambda _:pc,inventory=lambda:[pc],submit=lambda *a:calls.append(a) or {'status':'queued'})
    from unittest.mock import AsyncMock
    bot=SimpleNamespace(pc_registry=registry,control=SimpleNamespace(stop=lambda:pytest.fail('real stop must never run')),confirm_pc_shutdown=AsyncMock(),loop=object())
    def confirm(coro,loop):
        coro.close()
        return SimpleNamespace(result=lambda timeout:{'status':'confirmation_required','executed':False})
    monkeypatch.setattr('asyncio.run_coroutine_threadsafe',confirm)
    store=ControlStore(tmp_path/'c.sqlite3')
    body={'action':'server_stop','owner_text':'비서 서버 종료해줘','request_id':'req'}
    assert dispatch_control(store,bot,body)['status']=='scheduled'
    dispatch_control(store,bot,body);assert timers==[30]
    result=dispatch_control(store,bot,{'action':'pc_shutdown','owner_text':'내 PC 전원 내려줘','request_id':'req'})
    assert result['status']=='queued' and calls[0][1]=='shutdown'
    with pytest.raises(ValueError):dispatch_control(store,bot,{'action':'pc_shutdown','owner_text':'PC를 끌 수 있어?','request_id':'req2'})
    assert len(calls)==1
    with pytest.raises(ValueError):dispatch_control(store,bot,{'action':'pc_shutdown','owner_text':'노트북 전원 내려줘','pc':'데스크톱','request_id':'wrong-target'})
    assert len(calls)==1


def test_discord_buttons_share_store_and_reject_other_users(tmp_path):
    import asyncio
    async def check():
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from agent.brief_controls import BriefView,BriefHomeView
        store=ControlStore(tmp_path/'settings.sqlite3');bot=SimpleNamespace(owner_id=1,control_store=store)
        view=BriefView(bot);interaction=SimpleNamespace(user=SimpleNamespace(id=1),response=SimpleNamespace(edit_message=AsyncMock(),send_message=AsyncMock()))
        await view.children[3].callback(interaction)
        assert not store.preferences()['jobs']
        stranger=SimpleNamespace(user=SimpleNamespace(id=2),response=SimpleNamespace(edit_message=AsyncMock(),send_message=AsyncMock()))
        await view.children[3].callback(stranger)
        assert not store.preferences()['jobs'] and stranger.response.send_message.called
        home=BriefHomeView(bot);assert home.children[0].custom_id=='daevis_brief_settings'
    asyncio.run(check())
