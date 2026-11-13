import json

import pytest

from agent.device_nodes import DeviceStore
from agent.pc_control import PCRegistry

NOW = 1_800_000_000


def test_phone_task_validation_and_capability(tmp_path):
    s = DeviceStore(tmp_path / 'n.sqlite3', clock=lambda: NOW)
    s.enroll('phone-0123456789abcdef', ['phone_task'])
    ok = s.enqueue('phone-0123456789abcdef', 'phone_task', {'op': 'tap', 'args': {'i': 3}}, 'k1', 60)
    assert ok['status'] == 'queued'
    for bad in ({'op': 'shell', 'args': {}}, {'op': 'tap'}, {'op': 'type', 'args': {'text': 'x' * 4000}}, {'op': 'tap', 'args': []}):
        with pytest.raises(ValueError):
            s.enqueue('phone-0123456789abcdef', 'phone_task', bad, 'k-' + json.dumps(bad)[:20].replace(' ', ''), 60)


def test_registration_lets_the_agent_drive_the_phone(tmp_path):
    from agent import device_pairing as pairing
    r = PCRegistry(tmp_path / 'pc.sqlite3', clock=lambda: NOW)
    agent = r.enroll('agent-main', ['voice_command'])
    s = pairing.start(r, 'phone', 'SM-A336N'); pairing.accept(r); phone = pairing.poll(r, s['id'])
    assert 'phone_task' in phone['actions']
    job = r.request('agent-main', agent, 'phone', 'phone_task', {'op': 'screen', 'args': {}}, 'p1', 60)
    assert r.poll(phone['node'], phone['token'], actions=['phone_task'])['id'] == job['id']


SCREEN = {'package': 'com.kakao.talk', 'nodes': [
    {'i': 0, 'text': '친구', 'c': True, 'b': [0, 0, 10, 10]},
    {'i': 1, 'text': '결제하기', 'c': True, 'b': [0, 20, 10, 30]},
    {'i': 2, 'text': '15,000원', 'b': [0, 40, 10, 50]}]}


class FakePhone:
    def __init__(self):
        self.calls = []
    def __call__(self, op, args):
        self.calls.append((op, args))
        return {'ok': True, 'screen': SCREEN} if op != 'screen' else SCREEN


def tools(phone, approve=lambda *a: False):
    from agent.phone_tools import build_phone_tools
    return {t.name: t for t in build_phone_tools(phone, approve)}


def test_tools_send_steps_and_return_the_new_screen():
    phone = FakePhone()
    t = tools(phone)
    assert json.loads(t['phone_screen'].run({}))['package'] == 'com.kakao.talk'
    out = json.loads(t['phone_tap'].run({'i': 0}))
    assert out['screen']['nodes'][0]['text'] == '친구'
    t['phone_type'].run({'i': 0, 'text': '안녕'})
    t['phone_key'].run({'key': 'back'})
    t['phone_open'].run({'app': 'com.kakao.talk'})
    assert [c[0] for c in phone.calls] == ['screen', 'tap', 'type', 'key', 'open']


def test_payment_buttons_wait_for_the_owner():
    phone = FakePhone()
    asked = []
    t = tools(phone, approve=lambda kind, detail: asked.append((kind, detail)) or False)
    t['phone_screen'].run({})
    assert 'error' in t['phone_tap'].run({'i': 1})
    assert asked and asked[0][0] == 'payment' and '15,000원' in asked[0][1]['amounts']
    assert [c[0] for c in phone.calls] == ['screen']  # Not tapped.
    t2 = tools(phone, approve=lambda *a: True)
    t2['phone_screen'].run({})
    t2['phone_tap'].run({'i': 1})
    assert phone.calls[-1] == ('tap', {'i': 1})


def test_tap_needs_a_fresh_screen_and_a_known_index():
    t = tools(FakePhone())
    assert t['phone_tap'].run({'i': 0}).startswith('error')  # No screen read yet.
    t['phone_screen'].run({})
    assert t['phone_tap'].run({'i': 99}).startswith('error')


def test_phone_errors_become_messages():
    def broken(op, args):
        raise RuntimeError('접근성 서비스가 꺼져 있어요')
    assert '접근성' in tools(broken)['phone_screen'].run({})


def test_link_waits_for_the_phone_and_reads_large_results_from_a_file():
    from agent.phone_tools import phone_link, PhoneError
    posts = []
    states = iter([{'status': 'dispatched'}, {'status': 'succeeded', 'result': {'data_file': 'f1'}}])
    def post(path, body):
        posts.append((path, body))
        return {'id': 'j1', 'status': 'queued'} if path == '/request' else next(states)
    run = phone_link(post, lambda fid: ('screen.json', json.dumps(SCREEN).encode()), sleep=lambda s: None, monotonic=iter(range(100)).__next__)
    assert run('screen', {})['package'] == 'com.kakao.talk'
    assert posts[0][1]['target'] == 'phone' and posts[0][1]['action'] == 'phone_task'
    lost = phone_link(lambda p, b: {'id': 'j'} if p == '/request' else {'status': 'expired'}, None, sleep=lambda s: None,
                      monotonic=iter(range(100)).__next__)
    with pytest.raises(PhoneError, match='연결'):
        lost('screen', {})
