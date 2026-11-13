import asyncio
from contextlib import asynccontextmanager
from types import SimpleNamespace
from unittest.mock import AsyncMock

import discord
import pytest

from agent.gateway import PaymentApprovalView, Gateway, instructions_for, kakao_status_reply, quoted_kakao_message


class Channel:
    def __init__(self, channel_id):
        self.id = channel_id
        self.status = SimpleNamespace(id=99, edit=AsyncMock(), delete=AsyncMock())
        self.send = AsyncMock(return_value=self.status)

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
        send_long=AsyncMock(), send_outbox=AsyncMock(), watch_approvals=AsyncMock(),
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
    # One status message is sent first and then edited into the answer.
    assert msg.channel.send.await_count == 1
    assert msg.channel.send.await_args.args[0].startswith("⏳")
    assert msg.channel.status.edit.await_args.kwargs["content"] == "private result"
    bot.send_long.assert_not_awaited()
    bot.send_outbox.assert_awaited_once_with(msg.channel, screen=False)
    assert bot.owner_dm.await_count == 0
    assert [call.args[0] for call in msg.add_reaction.await_args_list] == ["⏳", "✅"]
    msg.remove_reaction.assert_awaited_once_with("⏳", bot.user)


def test_reaction_permission_failure_still_returns_result():
    bot, _, calls = gateway()
    msg = message()
    msg.add_reaction.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "denied")
    asyncio.run(Gateway.on_message(bot, msg))
    assert calls
    assert msg.channel.status.edit.await_args.kwargs["content"] == "private result"


def test_answer_still_arrives_when_status_message_cannot_be_sent():
    bot, _, calls = gateway()
    msg = message()
    msg.channel.send.side_effect = discord.Forbidden(SimpleNamespace(status=403, reason="Forbidden"), "denied")
    asyncio.run(Gateway.on_message(bot, msg))
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


def test_only_owner_retry_command_can_rearm_login_and_run_the_check(tmp_path, monkeypatch):
    import json
    monkeypatch.setattr('agent.gateway.DATA', tmp_path)
    state = tmp_path / 'login-automation-state.json'
    state.write_text('{"naver":{"paused":true,"reason":"captcha"}}')
    bot, _, calls = gateway()
    asyncio.run(Gateway.on_message(bot, message(author=2, content='<@9> 네이버 로그인 재시도')))
    assert json.loads(state.read_text())['naver']['paused'] and not calls
    asyncio.run(Gateway.on_message(bot, message(content='<@9> 네이버 로그인 재시도')))
    assert not json.loads(state.read_text())['naver']['paused']
    assert len(calls) == 1 and 'auth_ensure' in calls[0][1]


def test_dm_still_works_without_mention_and_empty_mention_is_ignored():
    bot, dm, calls = gateway()
    msg = message(content="hello", guild=False)
    asyncio.run(Gateway.on_message(bot, msg))
    assert calls[0][:2] == (str(msg.channel.id), "hello")
    assert bot.owner_dm.await_count == 0
    bot, _, calls = gateway()
    asyncio.run(Gateway.on_message(bot, message(content="<@!9>")))
    assert not calls


def test_payment_buttons_reject_nonowner():
    async def run():
        view = PaymentApprovalView(owner_id=1, on_answer=lambda value: None, timeout=5)
        other = SimpleNamespace(user=SimpleNamespace(id=2), response=SimpleNamespace(send_message=AsyncMock()))
        assert not await view.interaction_check(other)
        assert view.answer is None
        assert await view.interaction_check(SimpleNamespace(user=SimpleNamespace(id=1)))
    asyncio.run(run())


def test_payment_button_records_answer_once_and_removes_buttons():
    async def run():
        answers = []
        view = PaymentApprovalView(owner_id=1, on_answer=answers.append, timeout=5)
        interaction = SimpleNamespace(user=SimpleNamespace(id=1), message=SimpleNamespace(content="💳 결제 확인"),
                                      response=SimpleNamespace(edit_message=AsyncMock()))
        await view.decide(interaction, "allow")
        await view.decide(interaction, "deny")
        assert answers == ["allow"]
        kwargs = interaction.response.edit_message.await_args.kwargs
        assert kwargs["view"] is None and "결제를 진행" in kwargs["content"]
    asyncio.run(run())


def test_payment_request_is_shown_in_request_channel_with_buttons(tmp_path):
    from agent.approvals import ChannelApproval, pending_requests
    import threading, time as clock

    async def run():
        channel = Channel(20)
        bot = SimpleNamespace(owner_id=1)
        bot.show_payment_request = lambda *a, **k: Gateway.show_payment_request(bot, *a, **k)
        task = asyncio.create_task(Gateway.watch_approvals(bot, channel, tmp_path, interval=0.01))
        result = {}
        worker = threading.Thread(target=lambda: result.setdefault("ok", ChannelApproval(tmp_path, timeout=2, sleep=clock.sleep)("payment", {"button": "결제하기", "package": "com.example.shop", "amounts": ["32,900원"]})))
        worker.start()
        for _ in range(200):
            if channel.send.await_count:
                break
            await asyncio.sleep(0.01)
        kwargs = channel.send.await_args.kwargs
        assert "결제하기" in kwargs["content"] and "32,900원" in kwargs["content"]
        kwargs["view"].on_answer("allow")
        await asyncio.to_thread(worker.join)
        task.cancel()
        assert result["ok"] is True
        assert pending_requests(tmp_path) == []
    asyncio.run(run())


def test_turn_approver_never_asks_for_non_payment_tools():
    bot = SimpleNamespace(owner_id=1, loop=None)
    approve = Gateway.approver(bot, Channel(20))
    assert approve("mail_send", {"text": "x"}) is True
    assert approve("android_click", {"button": "삭제"}) is True


def test_public_prompt_does_not_automatically_load_private_facts():
    memory = SimpleNamespace(recent=lambda: [{"id": 1, "text": "private address"}])
    assert "private address" in instructions_for(memory)
    assert "private address" not in instructions_for(memory, public=True)


def test_owner_can_stop_kakao_watch_without_waiting_for_model():
    bot, _, calls = gateway()
    stopped=[]
    bot.kakao_watch=SimpleNamespace(stop=lambda:stopped.append(True))
    msg=message(content='<@9> 카톡 자동응답 중지')
    asyncio.run(Gateway.on_message(bot,msg))
    assert stopped == [True] and not calls


def test_kakao_send_status_cannot_echo_chat_content():
    assert kakao_status_reply('KAKAO_SENT') == '메시지 전송을 완료했습니다.'
    assert 'private chat' not in kakao_status_reply('KAKAO_SENT\nprivate chat')
    assert 'private chat' not in kakao_status_reply('private chat')


def test_followup_using_kakao_tools_cannot_leak_text(monkeypatch):
    from agent.loop import Tool
    saved=[]
    memory=SimpleNamespace(load_history=lambda _:[],save_history=lambda _,history:saved.extend(history),recent=lambda:[])
    fake=SimpleNamespace(memory=memory,model=None,android=None,tools=[Tool('android_screen','',{},lambda _: '{"nodes":[{"package":"com.kakao.talk","text":"private chat"}]}')])
    def run(model,tools,*args,**kwargs):
        tools[0].run({})
        args[1].append({'role':'assistant','content':'KAKAO_SENT\nprivate chat'})
        return 'KAKAO_SENT\nprivate chat'
    monkeypatch.setattr('agent.gateway.run_turn',run)
    result=Gateway.turn(fake,'guild:1','응 그대로 보내줘',lambda *_:True)
    assert result.startswith('메시지 전송을 완료했습니다.') and 'private chat' not in result
    assert 'private chat' not in str(saved)


def test_only_explicit_quoted_kakao_messages_use_programmatic_route():
    assert quoted_kakao_message('동횬 카톡으로 "ㅎㅇ" 보내줘') == ('동횬','ㅎㅇ')
    assert quoted_kakao_message('영진에게 카카오톡으로 “반가워” 보내줘') == ('영진','반가워')
    assert quoted_kakao_message('영진에게 반갑다고 카톡 보내줘') is None
    assert quoted_kakao_message('테스트 방 카톡으로 "hi" 보내줘') is None


def test_literal_kakao_send_uses_programmatic_route_with_or_without_quotes():
    assert quoted_kakao_message('카카오톡 도구 활용해서 테스트친구에게 ㅎㅇ라고 보내줘') == ('테스트친구', 'ㅎㅇ')
    assert quoted_kakao_message('카톡으로 테스트친구에게 "안녕"이라고 보내줘') == ('테스트친구', '안녕')
    assert quoted_kakao_message('테스트친구에게 안녕이라고 카톡 보내줘') == ('테스트친구', '안녕')
    assert quoted_kakao_message('테스트친구에게 반갑다고 카톡 보내줘') is None
    assert quoted_kakao_message('카톡으로 테스트친구에게 잘 지내냐고 물어봐') is None


def test_recipient_lookup_failure_does_not_fall_back_to_model():
    from agent.kakao_watch import KakaoRecipientUnavailable
    memory = SimpleNamespace(load_history=lambda _: [], save_history=lambda *a: None, recent=lambda: [])
    def unavailable(*args): raise KakaoRecipientUnavailable('ambiguous')
    fake = SimpleNamespace(memory=memory, android=None, kakao_watch=SimpleNamespace(send_once=unavailable),
        engine=SimpleNamespace(run=lambda *a, **k: pytest.fail('unsafe fallback')))
    reply = Gateway.turn(fake, 'guild:1', '테스트친구 카톡으로 "hi" 보내줘', lambda *_: True)
    assert reply.startswith('수신자를 확인해야 합니다.')


def test_typo_candidates_are_delivered_only_in_private_without_sending():
    from agent.kakao_watch import KakaoRecipientUnavailable
    memory = SimpleNamespace(load_history=lambda _: [], save_history=lambda *a: None, recent=lambda: [])
    notices = []
    def unavailable(*args): raise KakaoRecipientUnavailable('no exact match')
    fake = SimpleNamespace(memory=memory, android=None, notify_owner=notices.append,
        kakao_watch=SimpleNamespace(send_once=unavailable, find_candidates=lambda _: {'candidates': [{'name': 'private-person'}]}),
        engine=SimpleNamespace(run=lambda *a, **k: pytest.fail('unsafe fallback')))
    reply = Gateway.turn(fake, 'guild:1', '테스트친구 카톡으로 "hi" 보내줘', lambda *_: True)
    assert 'private-person' in notices[0] and 'private-person' not in reply
    assert '아직 메시지는 보내지 않았습니다' in reply


def test_quoted_send_does_not_call_model_or_expose_message(monkeypatch):
    memory=SimpleNamespace(load_history=lambda _:[],save_history=lambda *args:None,recent=lambda:[])
    sent=[]
    fake=SimpleNamespace(memory=memory,model=None,android=None,kakao_watch=SimpleNamespace(send_once=lambda *args:sent.append(args)))
    monkeypatch.setattr('agent.gateway.run_turn',lambda *args,**kwargs:pytest.fail('unnecessary model call'))
    result=Gateway.turn(fake,'guild:1','영진 카톡으로 "private greeting" 보내줘',lambda *_:True)
    assert sent == [('영진','private greeting')]
    assert result.startswith('메시지 전송을 완료했습니다.') and 'private greeting' not in result
    assert '모델 호출 0회' in result


def test_hermes_backend_retains_kakao_privacy_and_public_context():
    from agent.hermes_runner import HermesResult
    saved=[]
    calls=[]
    memory=SimpleNamespace(load_history=lambda _:[],save_history=lambda _,history:saved.extend(history),recent=lambda:[{'id':1,'text':'secret address'}])
    def run(text,instructions,history,public=False):
        calls.append((instructions,public))
        return HermesResult('KAKAO_SENT\nprivate chat',True,1000,12,2,1,'model')
    fake=SimpleNamespace(memory=memory,android=None,engine=SimpleNamespace(run=run))
    output=Gateway.turn(fake,'guild:1','응 보내줘',lambda *_:True)
    assert output.startswith('메시지 전송을 완료했습니다.')
    assert 'private chat' not in output and 'private chat' not in str(saved)
    assert calls[0][1] is True and 'secret address' not in calls[0][0]
    assert 'Hermes' in output


def test_no_tool_kakao_answer_is_never_reported_as_a_send_or_uncertain_send():
    from agent.hermes_runner import HermesResult
    memory=SimpleNamespace(load_history=lambda _:[],save_history=lambda *a:None,recent=lambda:[])
    fake=SimpleNamespace(memory=memory,android=None,engine=SimpleNamespace(run=lambda *a,**k:HermesResult('KAKAO_SENT',True,100,10,1,0)))
    reply=Gateway.turn(fake,'guild:1','카톡으로 메시지 보내줘',lambda *_:False)
    assert reply.startswith('카카오톡 도구가 실행되지 않아 메시지는 보내지 않았습니다.')


def test_literal_group_mention_uses_owner_arguments_without_model_or_extra_approval():
    memory=SimpleNamespace(load_history=lambda _:[],save_history=lambda *a:None,recent=lambda:[])
    sent=[]
    fake=SimpleNamespace(memory=memory,android=None,kakao_watch=SimpleNamespace(send_once=lambda *a,**kw:sent.append((a,kw))),
        engine=SimpleNamespace(run=lambda *a,**kw:pytest.fail('unnecessary model or MCP approval')))
    reply=Gateway.turn(fake,'guild:1','카톡 테스트 방이 있음. 거기에 @친구 을 태그하고 안녕 - daevis 보내줘',lambda *_:pytest.fail('duplicate approval'))
    assert sent==[(('테스트','안녕 - daevis'),{'room_type':'group','mention':'친구'})]
    assert reply.startswith('메시지 전송을 완료했습니다.') and '모델 호출 0회' in reply


def test_group_parser_requires_an_explicit_recipient_mention_and_literal_body():
    from agent.kakao_intent import group_send_intent
    assert group_send_intent('카톡 테스트방에서 @친구 를 태그하고 "대비스 테스트" 보내줘')['text']=='대비스 테스트'
    assert group_send_intent('카톡 그 사람들이 있는 최신 방에 알아서 보내줘') is None


def test_fixed_route_executes_code_without_model():
    from agent.loop import Tool
    memory=SimpleNamespace(load_history=lambda _:[],save_history=lambda *a:None,recent=lambda:[])
    tool=Tool('route_search','',{'type':'object','properties':{'origin':{'type':'string'},'destination':{'type':'string'}},'required':['origin','destination']},lambda a:'{"routes":[{"summary":"1시간 11:30 도착","detail":"빠른환승 3-2","reasons":["최소 환승"]}]}')
    fake=SimpleNamespace(memory=memory,android=None,engine=SimpleNamespace(run=lambda *a,**k:pytest.fail('unnecessary model')),tools=[tool])
    reply=Gateway.turn(fake,'guild:1','경로 "행신역" → "선릉역"',lambda *a:False)
    assert '11:30 도착' in reply and '빠른환승 3-2' in reply
    assert '모델 호출 0회' in reply and '방식 프로그램' in reply


def test_scheduled_hermes_results_also_hide_kakao_content():
    from agent.hermes_gateway import HermesGateway
    from agent.hermes_runner import HermesResult
    scopes=[]
    def run(*a,**k):
        scopes.append(k)
        return HermesResult('KAKAO_CHECKED\nprivate conversation',True)
    fake=SimpleNamespace(memory=SimpleNamespace(recent=lambda:[]),engine=SimpleNamespace(run=run))
    output=HermesGateway.run_scheduled(fake,'read status')
    assert 'private conversation' not in output and scopes[0]['owner_request'] is False


def test_managed_pc_receipt_does_not_send_a_second_answer():
    bot,_,_=gateway()
    def turn(*args):
        bot._pc_managed_message=12345
        return '이미 상태 메시지를 보냈습니다.'
    bot.turn=turn
    asyncio.run(Gateway.on_message(bot,message(content='<@9> 작업 끝났으니 데스크톱 전원 내려줘')))
    bot.send_long.assert_not_awaited()


def test_food_requests_go_to_baemin_without_asking_the_area():
    memory = SimpleNamespace(recent=lambda: [])
    text = instructions_for(memory, public=True)
    assert "지역을 되묻지 말고 baemin_search" in text and "최소 10곳" in text
    assert "일회용 수저" in text and "리뷰 이벤트" in text and "shared_kitchen" in text


def test_screenshot_is_attached_only_when_asked():
    from agent.gateway import wants_screen
    assert not wants_screen("초밥집 메뉴 정리해줘")
    assert wants_screen("배민 화면 보여줘")
    assert wants_screen("결제 화면 캡처해줘")


def test_phone_screenshot_is_dropped_but_route_map_kept(tmp_path, monkeypatch):
    import agent.gateway as gw
    (tmp_path / "android.png").write_bytes(b"x")
    (tmp_path / "route-ab-1.png").write_bytes(b"x")
    monkeypatch.setattr(gw, "current_outbox", lambda default: tmp_path)
    monkeypatch.setattr(gw.discord, "File", lambda path: SimpleNamespace(close=lambda: None, path=path))
    channel = Channel(20)
    asyncio.run(Gateway.send_outbox(SimpleNamespace(), channel))
    assert channel.send.await_count == 1
    assert not (tmp_path / "android.png").exists()


def test_phone_call_commands_are_left_to_the_always_on_power_bot():
    bot, _, calls = gateway()
    asyncio.run(Gateway.on_message(bot, message(content="<@9> 엄마한테 전화 걸어줘")))
    assert not calls


def test_blocked_kakao_reply_tells_the_owner_how_to_unlock():
    assert '카톡 잠금 해제' in kakao_status_reply('KAKAO_BLOCKED')


def test_owner_can_unlock_a_stuck_kakao_send_without_a_model(tmp_path, monkeypatch):
    import json
    import agent.gateway as gw
    monkeypatch.setattr(gw, 'DATA', tmp_path)
    (tmp_path / 'kakao-direct-send.json').write_text(json.dumps({'status': 'pending', 'request_hash': 'x', 'completed': []}))
    bot, _, calls = gateway()
    msg = message(content='카톡 잠금 해제', guild=False)
    asyncio.run(Gateway.on_message(bot, msg))
    assert not calls and '잠금을 풀었어요' in msg.channel.send.await_args.args[0]
    assert json.loads((tmp_path / 'kakao-direct-send.json').read_text())['status'] == 'reviewed'


def test_files_from_pc_and_code_runs_are_sent_and_removed(tmp_path, monkeypatch):
    import agent.gateway as gw
    files = tmp_path / "files"
    files.mkdir()
    (files / "발표.pptx").write_bytes(b"x" * 10)
    (files / "huge.bin").write_bytes(b"x" * 30)
    monkeypatch.setattr(gw, "current_outbox", lambda default: tmp_path)
    monkeypatch.setattr(gw, "ATTACHMENT_LIMIT", 20)
    monkeypatch.setattr(gw.discord, "File", lambda path, filename=None: SimpleNamespace(close=lambda: None, path=path, filename=filename))
    channel = Channel(20)
    asyncio.run(Gateway.send_outbox(SimpleNamespace(), channel))
    sent = [call.kwargs for call in channel.send.await_args_list]
    assert [k["file"].filename for k in sent if "file" in k] == ["발표.pptx"]
    assert [k["content"] for k in sent if "file" in k] == ["📎 발표.pptx"]
    assert any("huge.bin" in (call.args[0] if call.args else call.kwargs.get("content", "")) for call in channel.send.await_args_list)
    assert not list(files.iterdir())


def test_time_question_bypasses_engine_and_keeps_history(tmp_path, monkeypatch):
    from agent.memory import Memory
    from agent.gateway import Gateway
    monkeypatch.setenv('DAEVIS_SIMPLE_REPLIES', 'on')
    memory = Memory(tmp_path / 'memory.db')
    fake = SimpleNamespace(memory=memory, android=None,
                           engine=SimpleNamespace(run=lambda *a, **kw: pytest.fail('model must not run')))
    reply = Gateway.turn(fake, 'test-owner', '지금 몇 시야?', lambda *a: False)
    assert '한국 시간' in reply and '모델 호출 0회' in reply
    assert memory.load_history('test-owner')[-1]['role'] == 'assistant'


def test_phone_kakao_requests_skip_the_server_quick_path():
    from agent.gateway import quoted_kakao_message
    assert quoted_kakao_message('반수의신 카톡 "안녕" 보내줘') is not None
    assert quoted_kakao_message('내 폰으로 반수의신에게 "안녕" 카톡으로 보내줘') is None
    assert quoted_kakao_message('폰 카톡으로 반수의신에게 "안녕"이라고 보내줘') is None
