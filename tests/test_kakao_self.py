import json
from types import SimpleNamespace

import pytest

from agent.kakao_self import KakaoSelf, build_kakao_self_tools


def chat_xml(title='owner', count=False):
    return ('<hierarchy><node resource-id="com.kakao.talk:id/toolbar_default_title_text" text="' + title + '"/>'
            + ('<node resource-id="com.kakao.talk:id/toolbar_default_title_count" text="2"/>' if count else '')
            + '<node resource-id="com.kakao.talk:id/message_edit_text"/></hierarchy>')


def fake_control(contact=False, wrong_room=False):
    state = ['friends']
    clicks = []
    def observe():
        if state[0] == 'friends':
            nodes = [{'id': 1, 'description': 'private-owner, Profile photo', 'clickable': True}]
        elif state[0] == 'profile':
            nodes = [] if contact else [{'id': 2, 'text': 'My Chatroom', 'clickable': False}]
        else:
            nodes = [{'id': 3, 'resource': 'com.kakao.talk:id/message_edit_text'}]
        return {'snapshot': 'fresh', 'nodes': nodes}
    def click(snapshot, index, label):
        clicks.append(index)
        state[0] = 'profile' if index == 1 else 'chat'
    control = SimpleNamespace(observe=observe, click=click, key=lambda key: None,
                              device=SimpleNamespace(open=lambda app: None,
                                 dump_hierarchy=lambda: chat_xml(count=wrong_room)))
    return control, clicks


def test_self_navigation_requires_literal_my_chatroom(tmp_path):
    helper = KakaoSelf(None, tmp_path, sleep=lambda _: None)
    helper.control, clicks = fake_control()
    assert helper.open_self() == 'owner'
    assert clicks == [1, 2]


def test_self_navigation_leaves_global_search_before_selecting_profile(tmp_path):
    helper = KakaoSelf(None, tmp_path, sleep=lambda _: None)
    helper.control, clicks = fake_control()
    observe = helper.control.observe
    searching = [True]
    def observed():
        if searching[0]:
            return {'snapshot': 'search', 'nodes': [{'resource': 'com.kakao.talk.finder:id/input_focus'},
                {'id': 99, 'clickable': True, 'description': 'search-result, Profile photo'}]}
        return observe()
    helper.control.observe = observed
    helper.control.key = lambda key: searching.__setitem__(0, False)
    assert helper.open_self() == 'owner'
    assert clicks == [1, 2]


def test_cold_launcher_frame_does_not_press_back(tmp_path):
    helper = KakaoSelf(None, tmp_path, sleep=lambda _: None)
    helper.control, clicks = fake_control()
    observe = helper.control.observe
    first = [True]
    def cold_observe():
        if first.pop() if first else False:
            return {'snapshot': 'cold', 'nodes': [{'package': 'com.android.launcher3'}]}
        return observe()
    helper.control.observe = cold_observe
    helper.control.key = lambda _: pytest.fail('must not cancel startup with Back')
    assert helper.open_self() == 'owner'
    assert clicks == [1, 2]


def test_contact_profile_cannot_be_sent_to(tmp_path):
    helper = KakaoSelf(None, tmp_path, sleep=lambda _: None)
    helper.control, _ = fake_control(contact=True)
    with pytest.raises(ValueError, match='self'):
        helper.open_self()


def test_group_room_after_self_action_is_rejected(tmp_path):
    helper = KakaoSelf(None, tmp_path, sleep=lambda _: None)
    helper.control, _ = fake_control(wrong_room=True)
    with pytest.raises(ValueError):
        helper.open_self()


def test_send_journals_before_attempt_and_blocks_repeat_or_uncertainty(tmp_path):
    helper = KakaoSelf(None, tmp_path)
    helper.open_self = lambda: 'owner'
    calls = []
    def send(state, text, check_enabled):
        assert json.loads(helper.path.read_text())['status'] == 'pending'
        calls.append(text)
    helper.watcher.send_text = send
    assert helper.send('unique text') == 'KAKAO_SENT'
    assert calls == ['unique text']
    assert 'unique text' not in helper.path.read_text()
    with pytest.raises(ValueError):
        helper.send('unique text')
    helper.watcher.send_text = lambda *a, **kw: (_ for _ in ()).throw(RuntimeError('uncertain'))
    with pytest.raises(RuntimeError):
        helper.send('next text')
    with pytest.raises(ValueError, match='uncertain'):
        helper.send('any different text')


def test_tool_is_approval_gated_and_accepts_only_text(tmp_path):
    tools = build_kakao_self_tools(None, tmp_path)
    assert len(tools) == 1
    assert tools[0].name == 'kakao_self_send' and tools[0].needs_approval
    assert tools[0].parameters['additionalProperties'] is False
    assert set(tools[0].parameters['properties']) == {'text'}


def test_chat_message_named_my_chatroom_is_not_self_proof(tmp_path):
    helper = KakaoSelf(None, tmp_path, sleep=lambda _: None)
    clicks = []
    helper.control = SimpleNamespace(
        observe=lambda: {'snapshot': 's', 'nodes': [
            {'id': 1, 'resource': 'com.kakao.talk:id/message_edit_text'},
            {'id': 2, 'text': 'My Chatroom', 'resource': 'com.kakao.talk:id/message'}]},
        click=lambda *args: clicks.append(1), key=lambda key: None,
        device=SimpleNamespace(open=lambda app: None))
    with pytest.raises(ValueError, match='self identity'):
        helper.open_self()
    assert not clicks


def test_stale_navigation_target_is_reobserved_without_sending(tmp_path):
    helper = KakaoSelf(None, tmp_path, sleep=lambda _: None)
    helper.control, clicks = fake_control()
    click = helper.control.click
    calls = []
    def changing(snapshot, index, label):
        calls.append(index)
        if len(calls) == 2:
            raise ValueError('screen target changed')
        return click(snapshot, index, label)
    helper.control.click = changing
    assert helper.open_self() == 'owner'
    assert clicks == [1, 2]
    assert calls == [1, 2, 2]


def test_persisted_crash_lock_blocks_navigation_and_text_entry(tmp_path):
    helper = KakaoSelf(None, tmp_path)
    helper.path.with_suffix('.lock').write_text('')
    helper.open_self = lambda: pytest.fail('crashed send was retried')
    with pytest.raises(ValueError, match='locked'):
        helper.send('do not send')


def test_same_body_allowed_for_two_distinct_owner_requests(tmp_path):
    sent = []
    for request in ('owner-request-one', 'owner-request-two'):
        helper = KakaoSelf(None, tmp_path, request_id=request)
        helper.open_self = lambda: 'owner'
        helper.watcher.send_text = lambda state, text, **kwargs: sent.append(text)
        assert helper.send('same intentional body') == 'KAKAO_SENT'
    assert sent == ['same intentional body', 'same intentional body']
    same_request_again = KakaoSelf(None, tmp_path, request_id='owner-request-two')
    same_request_again.open_self = lambda: pytest.fail('duplicate request navigated')
    with pytest.raises(ValueError, match='already sent'):
        same_request_again.send('same intentional body')


def test_uncertain_send_blocks_a_different_owner_request(tmp_path):
    helper = KakaoSelf(None, tmp_path, request_id='first')
    helper.open_self = lambda: 'owner'
    helper.watcher.send_text = lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError('uncertain'))
    with pytest.raises(RuntimeError):
        helper.send('body')
    next_request = KakaoSelf(None, tmp_path, request_id='second')
    next_request.open_self = lambda: pytest.fail('uncertain send retried')
    with pytest.raises(ValueError, match='uncertain'):
        next_request.send('body')


def test_completed_history_prunes_oldest_without_blocking_new_request(tmp_path):
    helper = KakaoSelf(None, tmp_path, request_id='new')
    previous = ['completed-' + str(index) for index in range(256)]
    helper.path.write_text(json.dumps({'status': 'succeeded', 'completed': previous}))
    helper.open_self = lambda: 'owner'
    helper.watcher.send_text = lambda *args, **kwargs: None
    assert helper.send('new message') == 'KAKAO_SENT'
    completed = json.loads(helper.path.read_text())['completed']
    assert len(completed) == 256
    assert completed[:-1] == previous[1:]


def test_builder_request_id_not_exposed_as_model_argument(tmp_path):
    tools = build_kakao_self_tools(None, tmp_path, request_id='trusted-controller-id')
    assert set(tools[0].parameters['properties']) == {'text'}
