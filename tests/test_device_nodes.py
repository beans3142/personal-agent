import importlib.util
import json
import sqlite3
import threading
import time
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

import pytest
import agent.device_nodes as device_nodes

from agent.device_nodes import DeviceStore, build_device_tools, handler_for, validate_bind_address


@pytest.fixture
def store(tmp_path):
    return DeviceStore(tmp_path / 'nodes.sqlite3', clock=lambda: 1000)


def test_enrollment_auth_and_secret_redaction(store):
    token = store.enroll('phone', ['phone_alarm'])
    assert len(token) >= 32
    assert store.authenticate('phone', token)
    assert not store.authenticate('phone', 'wrong')
    assert token not in json.dumps(store.list_nodes())
    assert token.encode() not in store.path.read_bytes()
    with pytest.raises(ValueError):
        store.enroll('phone', ['phone_alarm'])


def test_queue_dedup_dispatch_once_and_ack(store):
    token = store.enroll('phone', ['phone_alarm'])
    first = store.enqueue('phone', 'phone_alarm', {'text': 'wake up'}, 'request-1', 60)
    assert store.enqueue('phone', 'phone_alarm', {'text': 'wake up'}, 'request-1', 60)['id'] == first['id']
    with pytest.raises(ValueError):
        store.enqueue('phone', 'phone_alarm', {'text': 'different'}, 'request-1', 60)
    job = store.poll('phone', token)
    assert job['id'] == first['id']
    assert store.poll('phone', token) is None
    store.ack('phone', token, first['id'], 'succeeded')
    assert store.list_jobs()[0]['status'] == 'succeeded'
    store.ack('phone', token, first['id'], 'succeeded')
    with pytest.raises(ValueError):
        store.ack('phone', token, first['id'], 'failed')


def test_expiry_uncertain_and_heartbeat(store):
    token = store.enroll('phone', ['phone_alarm'])
    store.enqueue('phone', 'phone_alarm', {'text': 'expired'}, 'a', 1)
    store.clock = lambda: 1002
    assert store.poll('phone', token) is None
    assert store.list_jobs()[0]['status'] == 'expired'
    assert store.list_nodes()[0]['online']
    store.enqueue('phone', 'phone_alarm', {'text': 'lost'}, 'b', 60)
    store.poll('phone', token)
    store.clock = lambda: 1200
    assert not store.list_nodes()[0]['online']
    assert store.list_jobs()[0]['status'] == 'uncertain'


def test_auth_scope_validation_and_queue_limit(store):
    phone = store.enroll('phone', ['phone_alarm'])
    other = store.enroll('other', ['phone_alarm'])
    with pytest.raises(PermissionError):
        store.poll('phone', other)
    for action, payload in [('shell', {}), ('wol', {}), ('phone_alarm', {'text': '-x', 'command': 'rm'})]:
        with pytest.raises(ValueError):
            store.enqueue('phone', action, payload, 'bad', 10)
    for i in range(32):
        store.enqueue('phone', 'phone_alarm', {'text': 'ok'}, str(i), 60)
    with pytest.raises(ValueError):
        store.enqueue('phone', 'phone_alarm', {'text': 'ok'}, 'overflow', 60)
    assert store.poll('phone', phone)


def test_revoke_cancels_pending_and_blocks_token(store):
    token = store.enroll('phone', ['phone_alarm'])
    store.enqueue('phone', 'phone_alarm', {'text': 'x'}, 'r', 60)
    store.revoke('phone')
    with pytest.raises(PermissionError):
        store.poll('phone', token)
    assert store.list_jobs()[0]['status'] == 'cancelled'


def test_tools_never_enroll_and_writes_require_approval(tmp_path):
    tools = {t.name: t for t in build_device_tools(tmp_path, {})}
    assert set(tools) == {'device_nodes', 'device_jobs', 'device_enqueue'}
    assert tools['device_enqueue'].needs_approval
    assert not tools['device_nodes'].needs_approval


def load_client():
    spec = importlib.util.spec_from_file_location('device_node_client', Path(__file__).parents[1] / 'deploy/device-node.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_client_action_validation_and_magic_packet(monkeypatch):
    client = load_client()
    assert client.magic_packet('AA:BB:CC:DD:EE:FF') == b'\xff' * 6 + bytes.fromhex('aabbccddeeff') * 16
    with pytest.raises(ValueError):
        client.magic_packet('evil')
    with pytest.raises(ValueError):
        client.execute_action({'action': 'shell', 'payload': {}}, {})
    with pytest.raises(ValueError):
        client.execute_action({'action': 'wol', 'payload': {}}, {})


def test_http_loopback_dispatch_and_reject_bad_token(store):
    token = store.enroll('phone', ['phone_alarm'])
    store.enqueue('phone', 'phone_alarm', {'text': 'ok'}, 'http', 30)
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(store))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def post(endpoint, body, bearer):
        request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}{endpoint}',
                                        data=json.dumps(body).encode(),
                                        headers={'Authorization': 'Bearer ' + bearer})
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.load(response)
    try:
        with pytest.raises(urllib.error.HTTPError) as error:
            post('/poll', {'node': 'phone'}, 'bad')
        assert error.value.code == 401
        job = post('/poll', {'node': 'phone'}, token)['job']
        assert job['action'] == 'phone_alarm'
        assert post('/poll', {'node': 'phone'}, token)['job'] is None
        assert post('/ack', {'node': 'phone', 'id': job['id'], 'status': 'succeeded'}, token) == {'ok': True}
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_node_journal_retries_ack_without_reexecuting(monkeypatch):
    client = load_client()
    state = sqlite3.connect(':memory:')
    state.execute('CREATE TABLE journal(id TEXT PRIMARY KEY,status TEXT,acknowledged INTEGER)')
    job = {'id': 'a', 'action': 'phone_alarm', 'payload': {'text': 'ok'}, 'expires': 9999999999}
    actions, acks = [], []
    def post(config, endpoint, body):
        if endpoint == '/poll':
            return {'job': job if not actions else None}
        acks.append(body['status'])
        if len(acks) == 1:
            raise OSError('ack connection lost')
        return {'ok': True}
    monkeypatch.setattr(client, 'post', post)
    monkeypatch.setattr(client, 'execute_action', lambda *args: actions.append('ran'))
    with pytest.raises(OSError):
        client.run_once({'node': 'phone'}, state)
    client.run_once({'node': 'phone'}, state)
    assert actions == ['ran']
    assert acks == ['succeeded', 'succeeded']


def test_node_crashed_running_action_becomes_uncertain(monkeypatch):
    client = load_client()
    state = sqlite3.connect(':memory:')
    state.execute('CREATE TABLE journal(id TEXT PRIMARY KEY,status TEXT,acknowledged INTEGER)')
    state.execute("INSERT INTO journal VALUES('crash','running',0)")
    seen = []
    def post(config, endpoint, body):
        if endpoint == '/ack':
            seen.append(body['status'])
            return {'ok': True}
        return {'job': None}
    monkeypatch.setattr(client, 'post', post)
    client.run_once({'node': 'phone'}, state)
    assert seen == ['uncertain']


def test_client_expired_job_does_not_run(monkeypatch):
    client = load_client()
    state = sqlite3.connect(':memory:')
    state.execute('CREATE TABLE journal(id TEXT PRIMARY KEY,status TEXT,acknowledged INTEGER)')
    seen = []
    def post(config, endpoint, body):
        if endpoint == '/ack':
            seen.append(body['status'])
            return {'ok': True}
        return {'job': {'id': 'expired', 'expires': 1, 'action': 'phone_alarm', 'payload': {'text': 'x'}}}
    monkeypatch.setattr(client, 'post', post)
    monkeypatch.setattr(client, 'execute_action', lambda *args: pytest.fail('expired job executed'))
    client.run_once({'node': 'phone'}, state)
    assert seen == ['expired']


def test_notification_uses_fixed_argv_no_shell(monkeypatch):
    client = load_client()
    calls = []
    monkeypatch.setattr(client.shutil, 'which', lambda name: '/bin/termux-notification')
    monkeypatch.setattr(client.subprocess, 'run', lambda args, **kwargs: calls.append((args, kwargs)))
    text = '$(unsafe); --action unsafe'
    client.execute_action({'action': 'phone_alarm', 'payload': {'text': text}}, {'actions': ['phone_alarm']})
    argv, options = calls[0]
    assert argv[argv.index('--content') + 1] == text
    assert '--action' not in argv
    assert not options.get('shell', False)
    assert '--sound' in argv


def test_heartbeat_only_node_cannot_receive_actions(store):
    token = store.enroll('qa-node', [])
    assert store.poll('qa-node', token) is None
    assert store.list_nodes()[0]['online']
    with pytest.raises(ValueError):
        store.enqueue('qa-node', 'wol', {}, 'no-effects', 10)


def test_node_request_acl_issuer_dedup_and_status(store):
    a = store.enroll('a', ['text_message'])
    b = store.enroll('b', ['text_message'])
    c = store.enroll('c', ['text_message'])
    with pytest.raises(PermissionError):
        store.request('a', a, 'b', 'text_message', {'text': 'hi'}, 'same', 30)
    store.grant('a', 'b', 'text_message')
    store.grant('b', 'a', 'text_message')
    first = store.request('a', a, 'b', 'text_message', {'text': 'hi'}, 'same', 30)
    assert store.request('a', a, 'b', 'text_message', {'text': 'hi'}, 'same', 30)['id'] == first['id']
    second = store.request('b', b, 'a', 'text_message', {'text': 'hello'}, 'same', 30)
    assert second['id'] != first['id']
    assert store.request_status('a', a, first['id'])['status'] == 'queued'
    with pytest.raises(PermissionError):
        store.request_status('c', c, first['id'])
    store.grant('c', 'b', 'text_message')
    third = store.request('c', c, 'b', 'text_message', {'text': 'another issuer'}, 'same', 30)
    assert third['id'] != first['id']
    with pytest.raises(PermissionError):
        store.request('a', c, 'b', 'text_message', {'text': 'spoof'}, 'x', 30)
    with pytest.raises(ValueError):
        store.request('a', a, 'b', 'text_message', {'text': 'changed'}, 'same', 30)
    with pytest.raises(PermissionError):
        store.request('a', a, 'b', 'phone_alarm', {'text': 'no'}, 'alarm', 30)
    with pytest.raises(ValueError):
        store.request('a', a, 'b', 'text_message', {'text': 'x', 'origin': 'c'}, 'bad-payload', 30)
    store.ungrant('c', 'b', 'text_message')
    assert store.request_status('c', c, third['id'])['status'] == 'cancelled'
    with pytest.raises(PermissionError):
        store.request('c', c, 'b', 'text_message', {'text': 'no'}, 'after-ungrant', 30)
    store.revoke('a')
    with pytest.raises(PermissionError):
        store.request('a', a, 'b', 'text_message', {'text': 'no'}, 'revoked', 30)
    assert store.request_status('b', b, second['id'])['status'] == 'cancelled'


def test_node_request_ttl_and_grant_validation(store):
    a = store.enroll('a', [])
    b = store.enroll('b', ['text_message'])
    for source, target, action in [('missing', 'b', 'text_message'), ('a', 'missing', 'text_message'), ('a', 'b', 'wol')]:
        with pytest.raises(ValueError):
            store.grant(source, target, action)
    store.grant('a', 'b', 'text_message')
    receipt = store.request('a', a, 'b', 'text_message', {'text': 'expire'}, 'short', 1)
    store.clock = lambda: 1002
    assert store.poll('b', b) is None
    assert store.request_status('a', a, receipt['id'])['status'] == 'expired'
    for ttl in (0, 301, True):
        with pytest.raises(ValueError):
            store.request('a', a, 'b', 'text_message', {'text': 'x'}, 'bad-ttl', ttl)


def test_bidirectional_http_uses_real_client_helpers(store, tmp_path, monkeypatch, capsys):
    store.clock = time.time
    client = load_client()
    tokens = {name: store.enroll(name, ['text_message']) for name in ('pc', 'phone', 'third')}
    store.grant('pc', 'phone', 'text_message')
    store.grant('phone', 'pc', 'text_message')
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(store))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    configs = {}
    for name, token in tokens.items():
        token_path = tmp_path / (name + '.token')
        token_path.write_text(token)
        configs[name] = {'url': f'http://127.0.0.1:{server.server_port}', 'node': name,
                         'token_file': str(token_path), 'actions': ['text_message']}
    def journal():
        db = sqlite3.connect(':memory:')
        db.execute('CREATE TABLE journal(id TEXT PRIMARY KEY,status TEXT,acknowledged INTEGER)')
        return db
    try:
        pc_receipt = client.send_request(configs['pc'], 'phone', 'text_message', {'text': 'from pc'}, 'pc-message', 30)
        phone_receipt = client.send_request(configs['phone'], 'pc', 'text_message', {'text': 'from phone'}, 'phone-message', 30)
        client.run_once(configs['phone'], journal())
        client.run_once(configs['pc'], journal())
        printed = capsys.readouterr().out
        assert 'from pc' in printed and 'from phone' in printed
        assert client.request_status(configs['pc'], pc_receipt['id'])['status'] == 'succeeded'
        assert client.request_status(configs['phone'], phone_receipt['id'])['status'] == 'succeeded'
        with pytest.raises(urllib.error.HTTPError):
            client.request_status(configs['third'], pc_receipt['id'])
        with pytest.raises(urllib.error.HTTPError):
            client.send_request(configs['pc'], 'phone', 'wol', {}, 'forbidden', 30)
        spoofed = {**configs['pc'], 'node': 'phone'}
        with pytest.raises(urllib.error.HTTPError):
            client.send_request(spoofed, 'pc', 'text_message', {'text': 'spoofed'}, 'forged', 30)
        with pytest.raises(urllib.error.HTTPError):
            client.post(configs['pc'], '/request', {'node': 'pc', 'target': 'phone', 'action': 'text_message',
                                                  'payload': {'text': 'x'}, 'request_key': 'origin', 'origin': 'third'})
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_node_text_message_is_terminal_only_and_escapes_controls(capsys):
    client = load_client()
    client.execute_action({'action': 'text_message', 'payload': {'text': 'hello\x1b[2J\nworld'}},
                          {'actions': ['text_message']})
    printed = capsys.readouterr().out
    assert 'hello' in printed and '\x1b' not in printed


def test_poll_backoff_is_bounded_and_resets():
    client = load_client()
    backoff = client.PollBackoff()
    assert [backoff.delay(OSError('offline')) for _ in range(8)] == [15, 30, 60, 120, 300, 300, 300, 300]
    assert backoff.delay() == 15
    assert backoff.delay(OSError('offline')) == 15
    assert backoff.delay(urllib.error.HTTPError('https://node', 503, 'unavailable', {}, None)) == 30
    for status in (401, 403):
        with pytest.raises(SystemExit) as stopped:
            backoff.delay(urllib.error.HTTPError('https://node', status, 'secret', {}, None))
        assert stopped.value.code == 0


def test_poll_loop_stops_on_revocation_and_logs_only_outage_start(capsys):
    client = load_client()
    state = sqlite3.connect(':memory:')
    state.execute('CREATE TABLE journal(id TEXT PRIMARY KEY,status TEXT,acknowledged INTEGER)')
    results = iter([OSError('private body'), urllib.error.HTTPError('https://node', 503, 'private body', {}, None),
                    None, OSError('private body'), urllib.error.HTTPError('https://node', 401, 'private body', {}, None)])
    sleeps = []
    def attempt(config, db):
        value = next(results)
        if value:
            raise value
    with pytest.raises(SystemExit) as stopped:
        client.poll_forever({}, state, sleep=sleeps.append, attempt=attempt)
    assert stopped.value.code == 0
    assert sleeps == [15, 30, 15, 15]
    output = capsys.readouterr().out
    assert output.count('retry') == 2
    assert 'private body' not in output


@pytest.mark.parametrize(('url', 'expected'), [
    ('https://game.daehoon.games/daevis', 'https://game.daehoon.games/daevis'),
    ('https://game.daehoon.games/daevis/', 'https://game.daehoon.games/daevis'),
    ('https://nodes.example/', 'https://nodes.example'),
    ('https://nodes.example', 'https://nodes.example'),
])
def test_node_cli_base_url_validation_accepts_only_supported_paths(url, expected):
    assert load_client().validate_base_url(url) == expected


@pytest.mark.parametrize('url', [
    'http://game.daehoon.games/daevis', 'https://user:secret@game.daehoon.games/daevis',
    'https://game.daehoon.games/daevis?token=x', 'https://game.daehoon.games/daevis#part',
    'https://game.daehoon.games/other', 'https://game.daehoon.games/daevis/../admin',
    'https://game.daehoon.games/%64aevis', 'https://game.daehoon.games/daevis//',
    'https://game.daehoon.games//daevis', 'https://game.daehoon.games/daevis%2f..',
    'https://game.daehoon.games\\@evil.example/daevis', 'https://game.daehoon.games/daevis\n',
])
def test_node_cli_base_url_rejects_unsafe_or_other_paths(url):
    with pytest.raises(ValueError):
        load_client().validate_base_url(url)


def test_client_post_keeps_configured_daevis_prefix_and_normalizes_slash(tmp_path, monkeypatch):
    client = load_client()
    token = tmp_path / 'node-token'
    token.write_text('test-token')
    requests = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def read(self, size): return b'{"job":null}'
    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            return Response()
    monkeypatch.setattr(client.urllib.request, 'build_opener', lambda *args: Opener())
    assert client.post({'url': 'https://game.daehoon.games/daevis/', 'token_file': str(token)},
                       '/poll', {'node': 'test'}) == {'job': None}
    assert requests[0].full_url == 'https://game.daehoon.games/daevis/poll'


def test_send_cli_normalizes_proxy_base_and_prints_receipt_only(tmp_path, monkeypatch, capsys):
    client = load_client()
    config_path, payload_path = tmp_path / 'node.json', tmp_path / 'message.json'
    config_path.write_text(json.dumps({'url': 'https://game.daehoon.games/daevis/', 'node': 'pc'}))
    payload_path.write_text(json.dumps({'text': 'private body'}))
    seen = []
    def send(config, *args):
        seen.append((config, args))
        return {'id': 'receipt', 'status': 'queued'}
    monkeypatch.setattr(client, 'send_request', send)
    monkeypatch.setattr('sys.argv', ['device-node.py', '--config', str(config_path), '--send', '--target', 'phone',
                                    '--action', 'text_message', '--payload-file', str(payload_path), '--request-key', 'unique'])
    client.main()
    assert seen[0][0]['url'] == 'https://game.daehoon.games/daevis'
    assert 'private body' not in capsys.readouterr().out


def test_cli_rejects_other_path_before_connecting(tmp_path, monkeypatch):
    client = load_client()
    config_path = tmp_path / 'node.json'
    config_path.write_text(json.dumps({'url': 'https://game.daehoon.games/admin', 'node': 'pc'}))
    monkeypatch.setattr('sys.argv', ['device-node.py', '--config', str(config_path)])
    monkeypatch.setattr(client, 'poll_forever', lambda *args: pytest.fail('invalid URL connected'))
    with pytest.raises(SystemExit) as stopped:
        client.main()
    assert stopped.value.code == 2


@pytest.mark.parametrize('address', ['127.0.0.1', '127.10.20.30', '10.0.0.4', '172.16.0.1', '172.31.255.254', '192.168.1.2'])
def test_device_listener_accepts_only_loopback_or_rfc1918_ipv4(address):
    assert validate_bind_address(address) == address


@pytest.mark.parametrize('address', ['0.0.0.0', '8.8.8.8', '169.254.1.2', '100.64.0.1',
                                     '172.15.255.255', '172.32.0.1', '192.0.2.1', '224.0.0.1',
                                     '255.255.255.255', '::1', '::', 'localhost', '10.0.0.1/24'])
def test_device_listener_rejects_other_bind_addresses(address):
    with pytest.raises(ValueError):
        validate_bind_address(address)


@pytest.mark.parametrize(('options', 'expected'), [([], '127.0.0.1'), (['--bind', '10.20.30.40'], '10.20.30.40')])
def test_device_server_cli_uses_validated_bind_without_network(monkeypatch, options, expected):
    seen = []
    class Server:
        def __init__(self, address, handler): seen.append(address)
        def serve_forever(self): pass
    monkeypatch.setattr(device_nodes, 'ThreadingHTTPServer', Server)
    monkeypatch.setattr(device_nodes, 'DeviceStore', lambda path: object())
    monkeypatch.setattr('sys.argv', ['device_nodes.py', 'serve', *options])
    device_nodes.main()
    assert seen == [(expected, 8791)]


def test_device_server_cli_rejects_public_bind_before_creating_store(monkeypatch):
    monkeypatch.setattr(device_nodes, 'DeviceStore', lambda path: pytest.fail('invalid bind created store'))
    monkeypatch.setattr('sys.argv', ['device_nodes.py', 'serve', '--bind', '0.0.0.0'])
    with pytest.raises(SystemExit) as stopped:
        device_nodes.main()
    assert stopped.value.code == 2
