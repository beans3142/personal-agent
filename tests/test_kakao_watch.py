from datetime import datetime, timezone
import pytest
from types import SimpleNamespace

from agent.kakao_watch import read_chat, new_messages, KakaoWatcher


def test_tablet_uses_chat_list_search_and_ignores_conversation_search():
    from agent.kakao_watch import global_search_targets, PREFIX
    base = {'package': 'com.kakao.talk', 'resource': '', 'description': '', 'text': ''}
    nodes = [{**base, 'id': 1, 'resource': PREFIX+'toolbar', 'bounds': [72, 0, 448, 90]},
             {**base, 'id': 2, 'resource': PREFIX+'toolbar_main_title_text', 'text': 'Chats', 'bounds': [99, 43, 167, 73]},
             {**base, 'id': 3, 'description': 'Search', 'bounds': [294, 27, 340, 90]},
             {**base, 'id': 4, 'description': 'Search', 'bounds': [742, 27, 788, 90]}]
    assert [n['id'] for n in global_search_targets(nodes)] == [3]


def test_send_once_searches_before_entering_message(tmp_path):
    watch = KakaoWatcher(None, tmp_path / 'watch.json')
    calls = []
    watch.search_chat = lambda state: calls.append(('search', state['room']))
    watch.send_text = lambda state, text, **kw: calls.append(('send', text))
    watch.send_once('unique person', 'hello')
    assert calls == [('search', 'unique person'), ('send', 'hello')]


def test_self_recipient_uses_native_identity_instead_of_friend_search(tmp_path, monkeypatch):
    watch = KakaoWatcher(None, tmp_path / 'watch.json')
    watch.search_chat = lambda _: pytest.fail('self is not a normal indexed friend')
    monkeypatch.setattr('agent.kakao_self.KakaoSelf.open_self', lambda _: 'verified-owner')
    sent = []
    watch.send_text = lambda state, text, **kw: sent.append((state['room'], text))
    watch.send_once('나', 'self QA')
    assert sent == [('verified-owner', 'self QA')]


def test_send_once_records_pending_and_never_retries_uncertainty(tmp_path):
    watch = KakaoWatcher(None, tmp_path / 'watch.json')
    watch.search_chat = lambda state: None
    calls = []
    def fail(*args, **kwargs):
        calls.append(1)
        raise RuntimeError('uncertain')
    watch.send_text = fail
    with pytest.raises(RuntimeError): watch.send_once('person', 'hello')
    with pytest.raises(ValueError, match='uncertain'): watch.send_once('person', 'hello')
    assert calls == [1]


def test_same_owner_request_cannot_send_twice_after_success(tmp_path):
    watch = KakaoWatcher(None, tmp_path / 'watch.json')
    watch.search_chat = lambda _: None
    calls = []
    watch.send_text = lambda *a, **kw: calls.append(1)
    watch.send_once('person', 'hello', request_id='owner-one')
    with pytest.raises(ValueError, match='already sent'):
        watch.send_once('person', 'hello', request_id='owner-one')
    watch.send_once('person', 'hello', request_id='owner-two')
    assert calls == [1, 1]
    assert 'person' not in (tmp_path / 'kakao-direct-send.json').read_text()
    assert 'hello' not in (tmp_path / 'kakao-direct-send.json').read_text()


def test_search_result_ambiguity_never_selects_or_enters_message(tmp_path, monkeypatch):
    watch = KakaoWatcher(None, tmp_path / 'watch.json')
    watch._search_results = lambda _: None
    watch.control = SimpleNamespace(observe=lambda: {'snapshot': 's', 'nodes': [
        {'package': 'com.kakao.talk', 'clickable': True, 'description': 'person'},
        {'package': 'com.kakao.talk', 'clickable': True, 'description': 'person'}]},
        click=lambda *a: pytest.fail('ambiguous result selected'))
    monkeypatch.setattr('agent.kakao_watch.time.sleep', lambda _: None)
    with pytest.raises(ValueError, match='ambiguous'):
        watch.search_chat({'room': 'person', 'members': None})


def test_typo_candidates_never_select_or_send_and_require_real_name_child(tmp_path, monkeypatch):
    watch = KakaoWatcher(None, tmp_path / 'watch.json')
    terms = []
    watch._search_results = terms.append
    row = {'package': 'com.kakao.talk', 'clickable': True, 'description': 'abcde', 'class': 'android.view.View', 'bounds': [0, 0, 100, 100], 'text': ''}
    child = {**row, 'clickable': False, 'description': '', 'text': 'abcde', 'bounds': [1, 1, 50, 50]}
    watch.control = SimpleNamespace(observe=lambda: None, nodes=[row, child], click=lambda *a: pytest.fail('candidate selected'))
    monkeypatch.setattr('agent.kakao_watch.time.sleep', lambda _: None)
    result = watch.find_candidates('abcdf')
    assert result['status'] == 'candidates_only'
    assert result['candidates'] == [{'name': 'abcde', 'exact': False}]
    assert terms == ['abcdf', 'ab', 'df']
    watch.control.nodes = [row]
    assert watch.find_candidates('abcdf')['candidates'] == []


def test_send_tool_has_stable_request_id_and_approval_gate(tmp_path, monkeypatch):
    from agent.kakao_watch import build_kakao_send_tools
    ids = []
    monkeypatch.setattr(KakaoWatcher, 'send_once', lambda self, recipient, text, **kw: ids.append(kw['request_id']))
    send, lookup = build_kakao_send_tools(None, tmp_path, request_id='trusted-id')
    assert send.needs_approval and not lookup.needs_approval
    assert send.run({'recipient': 'person', 'text': 'hello'}) == 'KAKAO_SENT'
    assert ids == ['trusted-id']
    assert 'request_id' not in send.parameters['properties']


def screen(room="room", messages=()):
    bubbles = "".join(f'<node resource-id="com.kakao.talk:id/bubble_root"><node resource-id="{ "com.kakao.talk:id/profile_layout" if incoming else ""}"/><node resource-id="com.kakao.talk:id/bubble"><node resource-id="com.kakao.talk:id/message" text="{text}"/></node></node>' for incoming, text in messages)
    return f'<hierarchy><node resource-id="com.kakao.talk:id/toolbar_default_title_text" content-desc="{room}5"/><node resource-id="com.kakao.talk:id/toolbar_default_title_count" content-desc="5"/>{bubbles}</hierarchy>'


def test_reads_only_selected_room_and_classifies_own_bubbles():
    result = read_chat(screen(messages=[(True,"hello"),(False,"reply")]), "room", 5)
    assert result == [{"incoming": True,"text":"hello"}, {"incoming":False,"text":"reply"}]
    with pytest.raises(ValueError):
        read_chat(screen(room="other"), "room", 5)


def test_expired_send_is_blocked_before_touching_device(tmp_path):
    watch=KakaoWatcher(None,tmp_path/'watch.json')
    state={"enabled":True,"until":"2000-01-01T00:00:00+00:00"}
    watch.save(state)
    with pytest.raises(ValueError,match='deadline'):
        watch.send_text(state,'must not send')


def test_overlap_detects_new_messages_without_replying_to_self():
    a={"incoming":True,"text":"a"}; b={"incoming":False,"text":"b"}; c={"incoming":True,"text":"c"}
    assert new_messages([a,b],[b,c]) == [c]
    assert new_messages([a],[a,b]) == []
    assert new_messages([a,b],[a,b]) == []
    with pytest.raises(ValueError):
        new_messages([a],[c])


def test_first_poll_is_baseline_and_expiry_never_replies(tmp_path):
    now=datetime(2026,11,2,12,tzinfo=timezone.utc)
    watch=KakaoWatcher(None,tmp_path/'watch.json')
    watch.save({"enabled":True,"room":"room","members":5,"until":"2026-11-02T23:00:00+09:00"})
    sent=[]
    watch.chat=lambda state: [{"incoming":True,"text":"old"}]
    assert watch.tick(now,lambda _:sent.append(1)) == "baseline"
    assert watch.tick(now,lambda _:sent.append(1)) == "unchanged"
    assert watch.tick(datetime(2026,11,2,14,tzinfo=timezone.utc),lambda _:sent.append(1)) == "expired"
    assert not sent and not watch.load()['enabled']


def test_uncertain_send_is_not_retried_after_restart(tmp_path):
    watch=KakaoWatcher(None,tmp_path/'watch.json')
    watch.save({"enabled":True,"room":"room","members":5,"until":"2026-11-02T23:00:00+09:00","tail":[{"incoming":True,"text":"old"}]})
    watch.chat=lambda state: [{"incoming":True,"text":"old"},{"incoming":True,"text":"new"}]
    def fail(_): raise RuntimeError('uncertain')
    now=datetime(2026,11,2,12,tzinfo=timezone.utc)
    with pytest.raises(RuntimeError): watch.tick(now,fail)
    restored=KakaoWatcher(None,watch.path)
    assert restored.tick(now,lambda _:pytest.fail('duplicate send')) == "paused"


def test_message_arriving_during_reply_is_not_marked_answered(tmp_path):
    old={"incoming":True,"text":"old"}; first={"incoming":True,"text":"first"}; second={"incoming":True,"text":"second"}; own={"incoming":False,"text":"reply"}
    watch=KakaoWatcher(None,tmp_path/'watch.json')
    watch.save({"enabled":True,"room":"room","members":5,"until":"2026-11-02T23:00:00+09:00","tail":[old]})
    current=[old,first]
    watch.chat=lambda _: list(current)
    received=[]
    def reply(event):
        received.append(event['incoming'])
        current[:]=[old,first,second,own]
    now=datetime(2026,11,2,12,tzinfo=timezone.utc)
    watch.tick(now,reply)
    watch.tick(now,reply)
    assert received == [[first],[second]]


def test_stop_during_reply_stays_disabled_and_guards_send(tmp_path):
    old={"incoming":True,"text":"old"}; new={"incoming":True,"text":"new"}
    watch=KakaoWatcher(None,tmp_path/'watch.json')
    state={"enabled":True,"room":"room","members":5,"until":"2026-11-02T23:00:00+09:00","tail":[old]}
    watch.save(state)
    watch.chat=lambda _: [old,new]
    watch.tick(datetime(2026,11,2,12,tzinfo=timezone.utc),lambda _:watch.stop())
    assert not watch.load()['enabled']
    with pytest.raises(ValueError,match='stopped'):
        watch.send_text(state,'must not send')


def test_stop_during_poll_cannot_be_overwritten_by_old_state(tmp_path):
    watch=KakaoWatcher(None,tmp_path/'watch.json')
    watch.save({"enabled":True,"room":"room","members":5,"until":"2026-11-02T23:00:00+09:00"})
    def chat(_):
        watch.stop()
        return [{"incoming":True,"text":"old"}]
    watch.chat=chat
    watch.tick(datetime(2026,11,2,12,tzinfo=timezone.utc),lambda _:pytest.fail('stopped'))
    assert not watch.load()['enabled']


def test_room_opened_on_last_navigation_attempt_is_verified(tmp_path):
    watch=KakaoWatcher(None,tmp_path/'watch.json')
    selected=[]; observations=[]
    device=SimpleNamespace(dump_hierarchy=lambda:screen(room='room' if selected else 'other'),open=lambda _:None)
    def observe():
        observations.append(1)
        return {'snapshot':'s','nodes': [] if len(observations)==1 else [{'id':1,'description':'room, 5 friends, preview','clickable':True}]}
    watch.control=SimpleNamespace(device=device,observe=observe,click=lambda *args:selected.append(1))
    assert watch.chat({'room':'room','members':5}) == []


def test_existing_identical_bubble_does_not_confirm_noop_send(tmp_path):
    watch=KakaoWatcher(None,tmp_path/'watch.json')
    state={'enabled':True,'room':'room','members':5,'until':'2099-01-01T00:00:00+00:00'}
    watch.save(state)
    observations=[]
    def observe():
        observations.append(1)
        return {'snapshot':'s','nodes':[{'id':1,'resource':'com.kakao.talk:id/message_edit_text'}] if len(observations)==1 else [{'id':2,'description':'Send'}]}
    watch.control=SimpleNamespace(device=SimpleNamespace(dump_hierarchy=lambda:screen(messages=[(False,'hello')])),observe=observe,input=lambda *args,**kwargs:None,click=lambda *args:None)
    with pytest.raises(RuntimeError,match='uncertain'):
        watch.send_text(state,'hello')


def test_verified_unsent_recovery_backlog_is_processed(tmp_path):
    watch=KakaoWatcher(None,tmp_path/'watch.json')
    old={'incoming':False,'text':'previous reply'}; waiting={'incoming':True,'text':'unanswered'}
    watch.save({'enabled':True,'room':'room','members':5,'until':'2099-01-01T00:00:00+00:00','tail':[old],'backlog':[waiting]})
    watch.chat=lambda _:[old]
    received=[]
    watch.tick(datetime(2026,11,2,12,tzinfo=timezone.utc),lambda event:received.extend(event['incoming']))
    assert received == [waiting] and not watch.load().get('backlog')
