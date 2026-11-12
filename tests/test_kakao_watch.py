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


def test_observed_group_identity_is_exact_and_not_a_similar_title():
    from agent.kakao_watch import observed_group_members
    assert observed_group_members(screen(room='room'), 'room') == 5
    with pytest.raises(ValueError): observed_group_members(screen(room='room2'), 'room')


def test_cache_lookup_does_not_open_ui_or_authorize_sending(tmp_path):
    watch=KakaoWatcher(None,tmp_path/'watch.json')
    watch.targets.remember('room','room','group',5)
    watch._search_results=lambda _:pytest.fail('cached lookup should not repeat UI search')
    result=watch.find_candidates('room')
    assert result['status']=='cached_hints' and result['requires_live_verification']
    assert result['candidates'][0]['room_type']=='group'


def test_group_mention_selects_native_member_and_appends_without_replacing_span(tmp_path,monkeypatch):
    from agent.kakao_watch import PREFIX
    watch=KakaoWatcher(None,tmp_path/'watch.json');draft=[''];selected=[False];clicks=[]
    def keys(text,clear=False):
        assert not clear
        if text=='hello':assert selected[0]
        draft[0]+=text
    def observe():
        nodes=[{'id':1,'resource':PREFIX+'message_edit_text','text':draft[0]}]
        if draft[0]=='@member' and not selected[0]:nodes.append({'id':2,'resource':PREFIX+'name_text','text':'member'})
        return {'snapshot':'s','nodes':nodes}
    def click(snapshot,index,label):
        clicks.append(index)
        if index==2:selected[0]=True;draft[0]+=' '
    watch.control=SimpleNamespace(device=SimpleNamespace(dump_hierarchy=lambda:screen(),device=SimpleNamespace(send_keys=keys)),
        observe=observe,click=click,input=lambda *a,**kw:pytest.fail('must not replace the native mention span'))
    monkeypatch.setattr('agent.kakao_watch.time.sleep',lambda _:None)
    assert watch.prepare_mention({'room':'room','members':5},'hello','member')=='@member hello'
    assert clicks==[1,2]


def test_native_search_input_can_be_unfocused_and_is_not_the_launcher(tmp_path):
    watch=KakaoWatcher(None,tmp_path/'watch.json');calls=[]
    watch.control=SimpleNamespace(observe=lambda:{'snapshot':'s','nodes':[
        {'id':1,'package':'com.kakao.talk','input':True,'focused':False},
        {'id':2,'package':'com.kakao.talk','input':True,'focused':False,'resource':'com.kakao.talk.finder:id/input_focus'}]},
        input=lambda snapshot,index,**kw:calls.append((index,kw['text'])))
    watch._enter_search_query('room')
    assert calls==[(1,'room')]


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


def test_post_send_transient_toolbar_does_not_resend_or_report_failure(tmp_path, monkeypatch):
    watch = KakaoWatcher(None, tmp_path / 'watch.json')
    clicked = []
    after = [screen(room='redrawing'), screen(messages=[(False, 'old'), (False, 'hello')])]
    def dump():
        return after.pop(0) if clicked and after else screen(messages=[(False, 'old')])
    observed = []
    def observe():
        observed.append(1)
        return {'snapshot': 's', 'nodes': [{'id': 1, 'resource': 'com.kakao.talk:id/message_edit_text'}] if len(observed) == 1 else [{'id': 2, 'description': 'Send'}]}
    watch.control = SimpleNamespace(device=SimpleNamespace(dump_hierarchy=dump), observe=observe,
        input=lambda *a, **kw: None, click=lambda *a: clicked.append(1))
    monkeypatch.setattr('agent.kakao_watch.time.sleep', lambda _: None)
    watch.send_text({'room': 'room', 'members': 5, 'until': '2099-01-01T00:00:00+00:00'}, 'hello', check_enabled=False)
    assert clicked == [1] and after == []


def test_verified_unsent_recovery_backlog_is_processed(tmp_path):
    watch=KakaoWatcher(None,tmp_path/'watch.json')
    old={'incoming':False,'text':'previous reply'}; waiting={'incoming':True,'text':'unanswered'}
    watch.save({'enabled':True,'room':'room','members':5,'until':'2099-01-01T00:00:00+00:00','tail':[old],'backlog':[waiting]})
    watch.chat=lambda _:[old]
    received=[]
    watch.tick(datetime(2026,11,2,12,tzinfo=timezone.utc),lambda event:received.extend(event['incoming']))
    assert received == [waiting] and not watch.load().get('backlog')


def test_unconfirmed_earlier_send_blocks_with_a_specific_reason(tmp_path):
    import json
    from agent.kakao_watch import KakaoSendBlocked
    (tmp_path / 'kakao-direct-send.json').write_text(json.dumps({'status': 'pending', 'request_hash': 'x', 'completed': []}))
    watch = KakaoWatcher(None, tmp_path / 'watch.json')
    watch.search_chat = lambda state: pytest.fail('must not search while blocked')
    with pytest.raises(KakaoSendBlocked):
        watch.send_once('someone', 'hi')


def test_owner_unlock_clears_only_a_pending_send(tmp_path):
    import json
    from agent.kakao_watch import unlock_direct_send
    journal = tmp_path / 'kakao-direct-send.json'
    assert unlock_direct_send(tmp_path) is None
    journal.write_text(json.dumps({'status': 'pending', 'request_hash': 'x', 'completed': ['a']}))
    (tmp_path / 'kakao-direct-send.lock').write_text('')
    stamp = unlock_direct_send(tmp_path)
    assert stamp is not None
    data = json.loads(journal.read_text())
    assert data['status'] == 'reviewed' and data['completed'] == ['a']
    assert not (tmp_path / 'kakao-direct-send.lock').exists()
    watch = KakaoWatcher(None, tmp_path / 'watch.json')
    sent = []
    watch.search_chat = lambda state: None
    watch.send_text = lambda state, text, **kw: sent.append(text)
    watch.send_once('someone', 'hi')
    assert sent == ['hi']


def test_kakao_tool_reports_blocked_instead_of_a_generic_error(tmp_path):
    import json
    from agent.kakao_watch import build_kakao_send_tools
    (tmp_path / 'kakao-direct-send.json').write_text(json.dumps({'status': 'pending', 'request_hash': 'x', 'completed': []}))
    send = build_kakao_send_tools(None, tmp_path)[0]
    assert send.run({'recipient': 'someone', 'text': 'hi'}) == 'KAKAO_BLOCKED'


def test_recipient_with_extra_spaces_is_retried_without_them(tmp_path):
    watch = KakaoWatcher(None, tmp_path / 'watch.json')
    searched, sent = [], []
    def search(state):
        searched.append(state['room'])
        if ' ' in state['room']:
            raise ValueError('no exact search result')
    watch.search_chat = search
    watch.send_text = lambda state, text, **kw: sent.append((state['room'], text))
    watch.send_once('반수의 신', 'hi')
    assert searched == ['반수의 신', '반수의신'] and sent == [('반수의신', 'hi')]


def test_name_without_spaces_is_not_retried(tmp_path):
    from agent.kakao_watch import KakaoRecipientUnavailable
    watch = KakaoWatcher(None, tmp_path / 'watch.json')
    searched = []
    def search(state):
        searched.append(state['room']); raise ValueError('no exact search result')
    watch.search_chat = search
    with pytest.raises(KakaoRecipientUnavailable):
        watch.send_once('없는사람', 'hi')
    assert searched == ['없는사람']


def test_names_differing_only_by_spaces_count_as_exact_candidates(tmp_path):
    from agent.kakao_watch import same_name
    assert same_name('반수의신', '반수의 신') and same_name('김 철수', '김철수')
    assert not same_name('반수의신', '반수의 신2')
    watch = KakaoWatcher(None, tmp_path / 'watch.json')
    watch.targets.remember('반수의신', '반수의신', 'individual', None)
    result = watch.find_candidates('반수의신')
    assert result['candidates'][0]['exact']


def test_exact_candidates_are_not_announced_as_needing_confirmation(tmp_path, monkeypatch):
    from agent.kakao_watch import build_kakao_send_tools, KakaoWatcher as W
    notices = []
    monkeypatch.setattr(W, 'find_candidates', lambda self, q: {'status': 'candidates_only', 'candidates': [{'name': '반수의신', 'exact': True}]})
    find = build_kakao_send_tools(None, tmp_path, notify_candidates=notices.append)[1]
    find.run({'query': '반수의 신'})
    assert notices == []


def test_old_uncertain_send_expires_without_blocking_new_request(tmp_path):
    import os, time, json
    watch = KakaoWatcher(None, tmp_path / 'watch.json')
    journal = tmp_path / 'kakao-direct-send.json'
    journal.write_text(json.dumps({'status': 'pending', 'request_hash': 'old', 'completed': []}))
    os.utime(journal, (time.time() - 7200,) * 2)
    watch.search_chat = lambda _: None
    sent = []
    watch.send_text = lambda *a, **kw: sent.append(1)
    watch.send_once('person', 'new request', request_id='new')
    assert sent == [1]
    assert 'old' in json.loads(journal.read_text())['completed']


def test_expired_uncertain_request_is_never_replayed(tmp_path):
    import os, time, json
    watch = KakaoWatcher(None, tmp_path / 'watch.json')
    watch.search_chat = lambda _: None
    def fail(*a, **kw): raise RuntimeError('uncertain')
    watch.send_text = fail
    with pytest.raises(RuntimeError):
        watch.send_once('person', 'hello', request_id='same')
    journal = tmp_path / 'kakao-direct-send.json'
    previous = json.loads(journal.read_text())
    previous['started_at'] = time.time() - 7200
    journal.write_text(json.dumps(previous))
    os.utime(journal, (time.time() - 7200,) * 2)
    watch.send_text = lambda *a, **kw: pytest.fail('must not resend old request')
    with pytest.raises(ValueError, match='already'):
        watch.send_once('person', 'hello', request_id='same')
    assert json.loads(journal.read_text())['status'] == 'expired_uncertain'
