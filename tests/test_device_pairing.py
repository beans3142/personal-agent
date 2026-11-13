import pytest

from agent.pc_control import PCRegistry
from agent import device_pairing as pairing

NOW = 1_800_000_000


@pytest.fixture
def reg(tmp_path):
    clock = {'t': NOW}
    r = PCRegistry(tmp_path / 'pc.sqlite3', clock=lambda: clock['t'])
    r.clock_box = clock
    r.agent_token = r.enroll('agent-main', ['voice_command'])
    return r


def test_new_phone_waits_until_accepted_then_connects(reg):
    started = pairing.start(reg, 'phone', 'SM-A336N')
    assert len(started['code']) == 4 and started['code'].isdigit()
    assert pairing.poll(reg, started['id']) == {'status': 'pending'}
    assert [(p['kind'], p['code'], p['name']) for p in pairing.pending(reg)] == [('phone', started['code'], 'SM-A336N')]
    assert pairing.accept(reg) == {'kind': 'phone', 'name': 'SM-A336N', 'code': started['code']}
    reply = pairing.poll(reg, started['id'])
    assert reply['status'] == 'approved' and reply['node'].startswith('phone-') and reg.authenticate(reply['node'], reply['token'])
    assert pairing.poll(reg, started['id'])['status'] == 'unknown'  # Handed out once.
    job = reg.request('agent-main', reg.agent_token, 'phone', 'phone_alarm_at', {'at': NOW + 60, 'text': 'x'}, 'a', 300)
    assert reg.poll(reply['node'], reply['token'], actions=['phone_alarm_at'])['id'] == job['id']


def test_new_pc_connects_the_same_way(reg):
    started = pairing.start(reg, 'pc', 'DESKTOP-1')
    pairing.accept(reg, started['code'])
    reply = pairing.poll(reg, started['id'])
    assert reply['node'].startswith('pc-') and [p['label'] for p in reg.inventory()] == ['DESKTOP-1']


def test_deny_and_expiry_never_register(reg):
    a = pairing.start(reg, 'phone', 'A')
    pairing.deny(reg, a['code'])
    assert pairing.poll(reg, a['id'])['status'] == 'unknown'
    b = pairing.start(reg, 'phone', 'B')
    reg.clock_box['t'] += pairing.WAIT + 1
    assert pairing.poll(reg, b['id'])['status'] == 'expired'
    with pytest.raises(ValueError, match='no waiting'):
        pairing.accept(reg)
    assert reg.phone_inventory() == []


def test_several_waiting_needs_the_code(reg):
    a = pairing.start(reg, 'phone', 'A')
    b = pairing.start(reg, 'phone', 'B')
    with pytest.raises(ValueError, match='several'):
        pairing.accept(reg)
    assert pairing.accept(reg, b['code'])['name'] == 'B'
    assert pairing.poll(reg, a['id']) == {'status': 'pending'}


def test_reconnecting_the_same_device_replaces_its_old_registration(reg):
    first = pairing.start(reg, 'phone', 'SM-A336N'); pairing.accept(reg); old = pairing.poll(reg, first['id'])
    second = pairing.start(reg, 'phone', 'SM-A336N'); pairing.accept(reg); new = pairing.poll(reg, second['id'])
    assert not reg.authenticate(old['node'], old['token'])
    assert [p['node'] for p in reg.phone_inventory()] == [new['node']]


def test_anonymous_side_is_limited(reg):
    for _ in range(pairing.MAX_WAITING):
        pairing.start(reg, 'phone', 'm')
    with pytest.raises(ValueError):
        pairing.start(reg, 'phone', 'm')
    with pytest.raises(ValueError):
        pairing.start(reg, 'server', 'm')
    assert pairing.poll(reg, 'not-an-id')['status'] == 'unknown'
    assert pairing.pending(reg)[0]['name'] == 'm'


def test_only_connected_devices_or_the_agent_may_answer(reg):
    s = pairing.start(reg, 'phone', 'SM-A336N')
    with pytest.raises(PermissionError):
        pairing.handle(reg, '/pair/accept', {'node': 'agent-main'}, 'wrong-token')
    with pytest.raises(PermissionError):
        pairing.handle(reg, '/pair/pending', {'node': 'voice'}, 'x')
    assert pairing.handle(reg, '/pair/pending', {'node': 'agent-main'}, reg.agent_token)['requests'][0]['code'] == s['code']
    assert pairing.handle(reg, '/pair/accept', {'node': 'agent-main', 'code': s['code']}, reg.agent_token)['name'] == 'SM-A336N'
    with pytest.raises(ValueError):
        pairing.handle(reg, '/pair/start', {'kind': 'phone'}, None)


def test_names_are_cleaned(reg):
    pairing.start(reg, 'phone', '<script>' + 'x' * 100)
    pairing.start(reg, 'pc', '')
    assert [p['name'] for p in pairing.pending(reg)] == ['scriptxxxxxxxxxxxxxxxxxx', 'PC']


def test_http_routes(reg, tmp_path):
    import json, threading, urllib.error, urllib.request
    from http.server import ThreadingHTTPServer
    from agent.pc_control import pc_handler
    (tmp_path / 'device-node.py').write_text('x')
    server = ThreadingHTTPServer(('127.0.0.1', 0), pc_handler(reg, tmp_path))
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    def post(path, body, token=None):
        headers = {'Content-Type': 'application/json', **({'Authorization': 'Bearer ' + token} if token else {})}
        request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}{path}', data=json.dumps(body).encode(), headers=headers)
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.load(response)
    try:
        s = post('/pair/start', {'kind': 'phone', 'name': 'SM-A336N'})
        assert post('/pair/poll', {'id': s['id']}) == {'status': 'pending'}
        with pytest.raises(urllib.error.HTTPError) as error:
            post('/pair/accept', {'node': 'agent-main'}, 'bad')
        assert error.value.code == 401
        assert post('/pair/accept', {'node': 'agent-main'}, reg.agent_token)['name'] == 'SM-A336N'
        assert post('/pair/poll', {'id': s['id']})['status'] == 'approved'
    finally:
        server.shutdown(); server.server_close(); thread.join(timeout=3)


def test_agent_tools_accept_through_the_relay():
    import json
    sent = []
    def post(path, body):
        sent.append((path, body))
        return {'kind': 'phone', 'name': 'SM-A336N', 'code': '1234'}
    tools = {t.name: t for t in pairing.build_pairing_tools(post)}
    assert json.loads(tools['device_accept'].run({}))['name'] == 'SM-A336N'
    tools['device_deny'].run({'code': ' 1234 '})
    tools['device_requests'].run({})
    assert sent == [('/pair/accept', {'node': 'agent-main'}), ('/pair/deny', {'node': 'agent-main', 'code': '1234'}),
                    ('/pair/pending', {'node': 'agent-main'})]
    def broken(path, body):
        raise OSError('400')
    assert pairing.build_pairing_tools(broken)[1].run({}).startswith('error')
