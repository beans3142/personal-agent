from datetime import datetime, timezone
import pytest
from types import SimpleNamespace

from agent.kakao_watch import read_chat, new_messages, KakaoWatcher


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
