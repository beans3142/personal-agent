"""Relay features for the desktop app: typed text, progress, history, payment answers."""
import asyncio
import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from agent.pc_control import PCRegistry, pc_handler


def setup(tmp_path, clock=None):
    now = clock or [1000.0]
    r = PCRegistry(tmp_path / 'pc.sqlite3', clock=lambda: now[0])
    pc = r.redeem(r.invite('데스크톱'))
    r.ensure_voice()
    agent = r.enroll_agent()
    return r, pc, agent, now


def ask(r, pc, text, key='k1', via=None):
    payload = {'text': text} if via is None else {'text': text, 'via': via}
    return r.request(pc['node'], pc['token'], 'voice', 'voice_command', payload, key, 300)


# --- typed text -----------------------------------------------------------------

def test_typed_text_is_accepted_and_marked(tmp_path):
    r, pc, _, _ = setup(tmp_path)
    ask(r, pc, '오늘 일정 알려줘', via='typed')
    [job] = r.voice_jobs()
    assert job['payload'] == {'text': '오늘 일정 알려줘', 'via': 'typed'}


@pytest.mark.parametrize('payload', [
    {'text': '가' * 501}, {'text': '안녕', 'via': 'email'}, {'text': '안녕', 'via': 'typed', 'x': 1}, {'via': 'typed'},
])
def test_invalid_typed_payloads_are_rejected(tmp_path, payload):
    r, pc, _, _ = setup(tmp_path)
    with pytest.raises(ValueError):
        r.request(pc['node'], pc['token'], 'voice', 'voice_command', payload, 'bad', 300)


def test_longer_typed_text_up_to_500_chars_is_accepted(tmp_path):
    r, pc, _, _ = setup(tmp_path)
    assert ask(r, pc, '가' * 500, via='typed')['status'] == 'queued'


def test_forwarded_text_keeps_how_it_was_entered(tmp_path):
    r, pc, agent, _ = setup(tmp_path)
    r.forward_to_agent('안녕', 'voice-x', 'typed')
    assert r.poll('agent-main', agent)['payload'] == {'text': '안녕', 'via': 'typed'}


# --- progress from the agent -------------------------------------------------------

def test_agent_reports_progress_only_on_its_own_dispatched_job(tmp_path):
    r, pc, agent, _ = setup(tmp_path)
    forwarded = r.forward_to_agent('배민 검색해줘', 'voice-v1')
    with pytest.raises(ValueError):
        r.agent_progress(agent, forwarded['id'], {'step': '⏳ 검색하는 중…'})  # Not dispatched yet.
    r.poll('agent-main', agent)
    assert r.agent_progress(agent, forwarded['id'], {'step': '⏳ 검색하는 중…'}) == {'answers': []}
    assert r.job_result(forwarded['id'])['result'] == {'step': '⏳ 검색하는 중…'}
    with pytest.raises(PermissionError):
        r.agent_progress('wrong-token', forwarded['id'], {'step': 'x'})


def test_progress_rejects_oversized_or_non_object_results(tmp_path):
    r, pc, agent, _ = setup(tmp_path)
    forwarded = r.forward_to_agent('안녕', 'voice-v2')
    r.poll('agent-main', agent)
    for bad in (['x'], {'step': '가' * 3000}):
        with pytest.raises(ValueError):
            r.agent_progress(agent, forwarded['id'], bad)


# --- payment answers from the app ---------------------------------------------------

def test_pc_answer_reaches_the_agent_once_through_progress(tmp_path):
    r, pc, agent, _ = setup(tmp_path)
    voice = ask(r, pc, '배민에서 초밥 주문해줘')
    r.voice_jobs()
    forwarded = r.forward_to_agent('배민에서 초밥 주문해줘', 'voice-' + voice['id'])
    r.poll('agent-main', agent)
    assert r.approve(pc['node'], pc['token'], voice['id'], '0123456789abcdef', 'allow') is True
    assert r.agent_progress(agent, forwarded['id'], {'step': 's'}) == {'answers': [{'approval': '0123456789abcdef', 'value': 'allow'}]}
    assert r.agent_progress(agent, forwarded['id'], {'step': 's'}) == {'answers': []}


def test_only_the_requesting_pc_can_answer_and_values_are_checked(tmp_path):
    r, pc, agent, _ = setup(tmp_path)
    other = r.redeem(r.invite('노트북'))
    voice = ask(r, pc, '주문해줘')
    r.voice_jobs()
    r.forward_to_agent('주문해줘', 'voice-' + voice['id'])
    with pytest.raises(PermissionError):
        r.approve(other['node'], other['token'], voice['id'], '0123456789abcdef', 'allow')
    with pytest.raises(PermissionError):
        r.approve(pc['node'], 'bad-token', voice['id'], '0123456789abcdef', 'allow')
    for approval, value in (('nothex', 'allow'), ('0123456789abcdef', 'maybe')):
        with pytest.raises(ValueError):
            r.approve(pc['node'], pc['token'], voice['id'], approval, value)


def test_answer_without_a_forwarded_request_is_refused(tmp_path):
    r, pc, _, _ = setup(tmp_path)
    voice = ask(r, pc, 'PC 꺼줘')
    r.voice_jobs()
    with pytest.raises(ValueError):
        r.approve(pc['node'], pc['token'], voice['id'], '0123456789abcdef', 'allow')


# --- history -------------------------------------------------------------------------

def test_history_lists_requests_and_final_answers_newest_first(tmp_path):
    r, pc, _, now = setup(tmp_path)
    first = ask(r, pc, '첫 질문', 'k1')
    r.voice_jobs()
    r.voice_finish(first['id'], 'succeeded', {'reply': '짧은 답', 'text': '**긴** 답'})
    now[0] += 5
    ask(r, pc, '두 번째', 'k2', via='typed')
    r.voice_jobs()
    page = r.history(pc['node'], pc['token'])
    assert [i['text'] for i in page['items']] == ['두 번째', '첫 질문']
    assert page['items'][1]['reply'] == '**긴** 답' and page['items'][1]['status'] == 'succeeded'
    assert page['items'][0]['via'] == 'typed' and page['items'][0]['status'] == 'running'
    assert page['items'][0]['pc'] == '데스크톱' and page['me'] == '데스크톱'


def test_history_pages_with_before_cursor(tmp_path):
    r, pc, _, now = setup(tmp_path)
    for i in range(5):
        ask(r, pc, f'질문 {i}', f'k{i}')
        r.voice_jobs()
        now[0] += 1
    page = r.history(pc['node'], pc['token'], limit=2)
    assert [i['text'] for i in page['items']] == ['질문 4', '질문 3'] and page['more']
    page = r.history(pc['node'], pc['token'], before=page['items'][-1]['created'], limit=2)
    assert [i['text'] for i in page['items']] == ['질문 2', '질문 1'] and page['more']
    page = r.history(pc['node'], pc['token'], before=page['items'][-1]['created'], limit=2)
    assert [i['text'] for i in page['items']] == ['질문 0'] and not page['more']


def test_history_needs_a_registered_pc(tmp_path):
    r, pc, agent, _ = setup(tmp_path)
    with pytest.raises(PermissionError):
        r.history(pc['node'], 'nope')
    with pytest.raises(PermissionError):
        r.history('agent-main', agent)


def test_history_is_shared_by_the_owners_pcs(tmp_path):
    r, pc, _, _ = setup(tmp_path)
    laptop = r.redeem(r.invite('노트북'))
    ask(r, pc, '데스크톱에서 물어봄')
    r.voice_jobs()
    assert [i['pc'] for i in r.history(laptop['node'], laptop['token'])['items']] == ['데스크톱']


def test_old_history_and_finished_relay_jobs_are_pruned(tmp_path):
    r, pc, agent, now = setup(tmp_path)
    voice = ask(r, pc, '오래된 질문')
    r.voice_jobs()
    forwarded = r.forward_to_agent('오래된 질문', 'voice-' + voice['id'])
    r.poll('agent-main', agent)
    r.ack('agent-main', agent, forwarded['id'], 'succeeded', {'reply': '네'})
    r.voice_finish(voice['id'], 'succeeded', {'reply': '네'})
    now[0] += 2 * 86400
    fresh = ask(r, pc, '새 질문', 'k2')
    r.voice_jobs()
    r.voice_finish(fresh['id'], 'succeeded', {'reply': '응'})
    ids = {j['id'] for j in r.list_jobs()}
    assert voice['id'] not in ids and forwarded['id'] not in ids and fresh['id'] in ids
    assert [i['text'] for i in r.history(pc['node'], pc['token'])['items']] == ['새 질문', '오래된 질문']
    now[0] += 31 * 86400
    later = ask(r, pc, '한 달 뒤', 'k3')
    r.voice_jobs()
    r.voice_finish(later['id'], 'succeeded', {'reply': '응'})
    assert [i['text'] for i in r.history(pc['node'], pc['token'])['items']] == ['한 달 뒤']


def test_pending_shutdown_jobs_are_never_pruned(tmp_path):
    r, pc, _, now = setup(tmp_path)
    r.poll(pc['node'], pc['token'])
    shutdown = r.submit('데스크톱', 'shutdown', 'off-1')
    now[0] += 2 * 86400
    voice = ask(r, pc, '안녕')
    r.voice_jobs()
    r.voice_finish(voice['id'], 'succeeded', {'reply': '응'})
    assert shutdown['id'] in {j['id'] for j in r.list_jobs()}


# --- HTTP endpoints ----------------------------------------------------------------------

@pytest.fixture
def served(tmp_path):
    r, pc, agent, now = setup(tmp_path)
    server = ThreadingHTTPServer(('127.0.0.1', 0), pc_handler(r, tmp_path))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    def post(path, body, token):
        request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}{path}', data=json.dumps(body).encode(),
                                         method='POST', headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        try:
            with urllib.request.urlopen(request, timeout=5) as response:
                return response.status, json.loads(response.read())
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())
    yield r, pc, agent, post
    server.shutdown()


def test_http_history_progress_and_approve(served):
    r, pc, agent, post = served
    code, voice = post('/request', {'node': pc['node'], 'target': 'voice', 'action': 'voice_command',
                                    'payload': {'text': '주문해줘', 'via': 'typed'}, 'request_key': 'h1', 'ttl_seconds': 300}, pc['token'])
    assert code == 200
    r.voice_jobs()
    forwarded = r.forward_to_agent('주문해줘', 'voice-' + voice['id'], 'typed')
    r.poll('agent-main', agent)
    assert post('/history', {'node': pc['node']}, pc['token'])[1]['items'][0]['text'] == '주문해줘'
    assert post('/history', {'node': pc['node'], 'before': 9e9, 'limit': 5}, pc['token'])[0] == 200
    assert post('/approve', {'node': pc['node'], 'id': voice['id'], 'approval': 'abcdefabcdefabcd', 'value': 'deny'}, pc['token']) == (200, {'ok': True})
    code, body = post('/progress', {'node': 'agent-main', 'id': forwarded['id'], 'result': {'step': '⏳'}}, agent)
    assert code == 200 and body['answers'] == [{'approval': 'abcdefabcdefabcd', 'value': 'deny'}]
    assert post('/history', {'node': pc['node']}, 'bad')[0] == 401
    assert post('/history', {'node': pc['node'], 'limit': 1000}, pc['token'])[0] == 400
    assert post('/progress', {'node': pc['node'], 'id': forwarded['id'], 'result': {}}, pc['token'])[0] == 401


# --- relay loop ------------------------------------------------------------------------------

class FakeRegistry:
    def __init__(self, agent_results):
        self.agent_results, self.finished, self.progress, self.forwarded = list(agent_results), [], [], []
    def voice_finish(self, job_id, status, result): self.finished.append((job_id, status, result))
    def voice_progress(self, job_id, result): self.progress.append(result)
    def agent_online(self): return True
    def forward_to_agent(self, text, key, via='voice'): self.forwarded.append((text, key, via)); return {'id': 'a1'}
    def job_result(self, job_id): return self.agent_results.pop(0)


def relay(reg, payload, rule=None):
    from agent.voice_relay import handle_voice_job
    dm = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=7)))
    owner = SimpleNamespace(create_dm=AsyncMock(return_value=dm))
    bot = SimpleNamespace(pc_registry=reg, owner_id=1, fetch_user=AsyncMock(return_value=owner),
                          control=SimpleNamespace(start=lambda: 'started'), handle_phone=AsyncMock(), handle_pc=rule or AsyncMock(), handle=AsyncMock())
    asyncio.run(handle_voice_job(bot, {'id': 'j1', 'source': 'pc-1', 'payload': payload}, sleep=AsyncMock()))
    return dm


def test_relay_copies_agent_progress_to_the_pc_receipt_once_per_change():
    step = {'step': '⏳ 검색하는 중…'}
    approval = {'step': '⏳ 버튼 누르는 중…', 'approval': {'id': 'abcdefabcdefabcd', 'text': '💳 결제 확인', 'expires_in': 100}}
    reg = FakeRegistry([{'status': 'dispatched', 'result': step}, {'status': 'dispatched', 'result': step},
                        {'status': 'dispatched', 'result': approval},
                        {'status': 'succeeded', 'result': {'reply': '주문했어요.', 'text': '**주문**했어요.'}}])
    relay(reg, {'text': '주문해줘', 'via': 'typed'})
    assert reg.progress == [step, approval]
    assert reg.forwarded == [('주문해줘', 'voice-j1', 'typed')]
    assert reg.finished[0][1:] == ('succeeded', {'reply': '주문했어요.', 'text': '**주문**했어요.'})


def test_busy_but_running_server_is_not_announced_as_booting():
    from agent.voice_relay import handle_voice_job
    reg = FakeRegistry([{'status': 'succeeded', 'result': {'reply': '네'}}])
    reg.agent_online = lambda: False  # Agent is answering another request and not polling.
    dm = SimpleNamespace(send=AsyncMock(return_value=SimpleNamespace(id=7)))
    bot = SimpleNamespace(pc_registry=reg, owner_id=1, fetch_user=AsyncMock(return_value=SimpleNamespace(create_dm=AsyncMock(return_value=dm))),
                          control=SimpleNamespace(start=lambda: 'already_running'), handle_phone=AsyncMock(), handle_pc=AsyncMock(), handle=AsyncMock())
    asyncio.run(handle_voice_job(bot, {'id': 'j1', 'source': 'pc-1', 'payload': {'text': '안녕'}}, sleep=AsyncMock()))
    assert reg.progress == [] and reg.finished[0][1] == 'succeeded'


def test_typed_requests_are_echoed_with_a_keyboard_mark():
    reg = FakeRegistry([{'status': 'succeeded', 'result': {'reply': '네'}}])
    dm = relay(reg, {'text': '안녕', 'via': 'typed'})
    assert dm.send.await_args_list[0].args[0].startswith('⌨️ ')


def test_rule_command_result_includes_the_full_posted_text():
    async def handle_pc(message, action, target):
        await message.channel.send('🖥️ **데스크톱** 60초 뒤 꺼져요.')
    reg = FakeRegistry([])
    relay(reg, {'text': 'PC 꺼줘', 'via': 'typed'}, rule=handle_pc)
    assert reg.finished[0][2] == {'reply': '데스크톱 60초 뒤 꺼져요.', 'text': '🖥️ **데스크톱** 60초 뒤 꺼져요.'}


# --- agent side ---------------------------------------------------------------------------------

def gateway_with(reply_fn):
    dm = SimpleNamespace(id=55, send=AsyncMock())
    return SimpleNamespace(owner_dm=AsyncMock(return_value=dm), turn_lock=asyncio.Lock(), turn=reply_fn,
                           approver=lambda channel: None, send_long=AsyncMock(), send_outbox=AsyncMock(),
                           watch_approvals=AsyncMock())


def test_voice_turn_returns_full_text_and_reports_steps_and_payments(monkeypatch):
    from agent import voice_agent
    from agent.approvals import ChannelApproval
    from agent.artifacts import current_outbox
    from pathlib import Path
    reports, answered = [], []

    def turn(chat, text, approve):
        outbox = current_outbox(Path('unused'))
        from agent.progress import record
        record(outbox, 'baemin_search', {})
        approval = ChannelApproval(outbox, timeout=5, sleep=lambda s: __import__('time').sleep(0.05))
        answered.append(approval('payment', {'button': '결제하기', 'package': 'com.sampleapp', 'amounts': ['25,000원']}))
        return '**주문 완료**\n\n-# 처리 3초'

    def report(result):
        reports.append(result)
        if result.get('approval'):
            return [{'approval': result['approval']['id'], 'value': 'allow'}]
        return []

    monkeypatch.setattr(voice_agent, 'REPORT_SECONDS', 0.05)
    result = asyncio.run(voice_agent.voice_turn(gateway_with(turn), '초밥 주문해줘', report=report, via='typed'))
    assert answered == [True]
    assert result == {'reply': '주문 완료', 'text': '**주문 완료**\n\n-# 처리 3초'}
    assert any('배민' in r.get('step', '') for r in reports)
    payment = next(r['approval'] for r in reports if r.get('approval'))
    assert '배달의민족' in payment['text'] and '25,000원' in payment['text']


def test_typed_turn_is_echoed_to_dm_with_keyboard_mark():
    from agent import voice_agent
    gateway = gateway_with(lambda chat, text, approve: '네')
    asyncio.run(voice_agent.voice_turn(gateway, '안녕', via='typed'))
    assert gateway.send_long.await_args.args[1].startswith('⌨️ 안녕')


def test_long_answers_are_cut_to_fit_the_receipt():
    from agent import voice_agent
    result = asyncio.run(voice_agent.voice_turn(gateway_with(lambda c, t, a: '가' * 5000), '길게'))
    assert len(json.dumps(result, ensure_ascii=False)) <= 2048 and result['text'].endswith('…')


def test_poller_passes_entry_mode_and_reports_progress():
    from agent.voice_agent import poll_once
    posts = []
    def post(endpoint, body):
        posts.append((endpoint, body))
        if endpoint == '/poll':
            return {'job': {'id': 'a1', 'action': 'voice_command', 'payload': {'text': '안녕', 'via': 'typed'}, 'expires': 9e9}}
        return {'answers': [{'approval': 'x', 'value': 'deny'}]} if endpoint == '/progress' else {'ok': True}
    seen = {}
    async def answer(text, via, report):
        seen['via'] = via
        seen['answers'] = await asyncio.to_thread(report, {'step': '⏳'})
        return {'reply': '네'}
    asyncio.run(poll_once(post, answer, 'agent-main'))
    assert seen == {'via': 'typed', 'answers': [{'approval': 'x', 'value': 'deny'}]}
    assert ('/progress', {'node': 'agent-main', 'id': 'a1', 'result': {'step': '⏳'}}) in posts
    assert posts[-1][0] == '/ack'


def test_progress_lists_the_steps_taken_so_far(tmp_path):
    from agent.progress import record
    from agent.voice_agent import current_state
    for tool, args in [('baemin_search', {}), ('android_open', {'app': 'baemin'}), ('android_click', {}), ('android_click', {})]:
        record(tmp_path, tool, args)
    state = current_state(tmp_path)
    assert state['step'].startswith('⏳ 버튼 누르는 중')
    assert state['steps'] == ['배민에서 가게 메뉴 모으는 중', '앱 여는 중', '버튼 누르는 중']


def test_step_list_is_bounded(tmp_path):
    from agent.progress import record
    from agent.voice_agent import current_state
    for i in range(30):
        record(tmp_path, 'web_search' if i % 2 else 'web_fetch', {})
    assert len(current_state(tmp_path)['steps']) == 8
