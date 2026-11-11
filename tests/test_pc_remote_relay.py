"""Relay side of remote PC control: pc_task jobs, long polls and file attachments."""
import json
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from agent.pc_control import PCRegistry, pc_handler


def setup(tmp_path, clock=None):
    now = clock or [1000.0]
    r = PCRegistry(tmp_path / 'pc.sqlite3', clock=lambda: now[0])
    pc = r.redeem(r.invite('데스크톱'))
    r.ensure_voice()
    agent = r.enroll_agent()
    r.ensure_pc_tasks()
    return r, pc, agent, now


def task(r, agent, pc, op='windows', args=None, key='t1', ttl=60):
    return r.request('agent-main', agent, pc['node'], 'pc_task', {'op': op, 'args': args or {}}, key, ttl)


# --- jobs ----------------------------------------------------------------------------------

def test_main_agent_can_send_pc_tasks_after_migration(tmp_path):
    r, pc, agent, _ = setup(tmp_path)
    job = task(r, agent, pc)
    assert job['status'] == 'queued'
    got = r.task_poll(pc['node'], pc['token'], wait=0)
    assert got['action'] == 'pc_task' and got['payload'] == {'op': 'windows', 'args': {}} and got['source'] == 'agent-main'


def test_pc_registered_before_the_agent_gets_the_grant_on_migration(tmp_path):
    r = PCRegistry(tmp_path / 'pc.sqlite3', clock=lambda: 1000.0)
    pc = r.redeem(r.invite('데스크톱'))
    with r._db() as db:  # A PC enrolled by an older relay has no pc_task capability.
        db.execute("UPDATE nodes SET capabilities=? WHERE name=?", (json.dumps(['text_message', 'pc_shutdown', 'pc_cancel_shutdown']), pc['node']))
    r.ensure_voice()
    agent = r.enroll_agent()
    with pytest.raises((PermissionError, ValueError)):
        task(r, agent, pc)
    r.ensure_pc_tasks()
    assert task(r, agent, pc, key='t2')['status'] == 'queued'


def test_a_pc_registered_after_the_agent_is_granted_immediately(tmp_path):
    r, pc, agent, _ = setup(tmp_path)
    other = r.redeem(r.invite('노트북'))
    assert task(r, agent, other, key='n1')['status'] == 'queued'


def test_pcs_cannot_send_tasks_to_each_other(tmp_path):
    r, pc, agent, _ = setup(tmp_path)
    other = r.redeem(r.invite('노트북'))
    with pytest.raises(PermissionError):
        r.request(other['node'], other['token'], pc['node'], 'pc_task', {'op': 'shell', 'args': {}}, 'x', 60)


@pytest.mark.parametrize('payload', [
    {'op': 'format_disk', 'args': {}}, {'op': 'shell'}, {'op': 'shell', 'args': []},
    {'op': 'shell', 'args': {'command': 'x' * 4000}}, {'op': 'shell', 'args': {}, 'extra': 1},
])
def test_pc_task_payload_is_checked(tmp_path, payload):
    r, pc, agent, _ = setup(tmp_path)
    with pytest.raises(ValueError):
        r.request('agent-main', agent, pc['node'], 'pc_task', payload, 'bad', 60)


def test_the_legacy_power_receiver_never_takes_pc_tasks(tmp_path):
    r, pc, agent, _ = setup(tmp_path)
    job = task(r, agent, pc)
    assert r.poll(pc['node'], pc['token']) is None
    shutdown = r.submit('데스크톱', 'shutdown', 's1')
    assert r.task_poll(pc['node'], pc['token'], wait=0)['id'] == job['id']
    assert r.task_poll(pc['node'], pc['token'], wait=0) is None
    assert r.poll(pc['node'], pc['token'])['id'] == shutdown['id']


def test_task_poll_is_for_registered_pcs_only(tmp_path):
    r, pc, agent, _ = setup(tmp_path)
    with pytest.raises(PermissionError):
        r.task_poll('agent-main', agent, wait=0)
    with pytest.raises(PermissionError):
        r.task_poll(pc['node'], 'wrong', wait=0)


def test_long_poll_returns_as_soon_as_a_task_arrives(tmp_path):
    r, pc, agent, _ = setup(tmp_path)
    timer = threading.Timer(0.3, lambda: task(r, agent, pc))
    timer.start()
    started = time.monotonic()
    job = r.task_poll(pc['node'], pc['token'], wait=5, step=0.05)
    assert job is not None and time.monotonic() - started < 2


def test_long_poll_gives_up_after_the_wait(tmp_path):
    r, pc, _, _ = setup(tmp_path)
    started = time.monotonic()
    assert r.task_poll(pc['node'], pc['token'], wait=0.3, step=0.05) is None
    assert 0.25 <= time.monotonic() - started < 2


def test_long_poll_keeps_the_pc_online(tmp_path):
    r, pc, _, now = setup(tmp_path)
    r.task_poll(pc['node'], pc['token'], wait=0)
    assert r.agent_pcs_list()[0]['online'] is True
    now[0] += 60
    assert r.agent_pcs_list()[0]['online'] is False


def test_wait_status_returns_when_the_pc_answers(tmp_path):
    r, pc, agent, _ = setup(tmp_path)
    job = task(r, agent, pc)
    got = r.task_poll(pc['node'], pc['token'], wait=0)
    threading.Timer(0.3, lambda: r.ack(pc['node'], pc['token'], got['id'], 'succeeded', {'ok': True})).start()
    state = r.wait_status('agent-main', agent, job['id'], wait=5, step=0.05)
    assert state['status'] == 'succeeded' and state['result'] == {'ok': True}


def test_wait_status_only_for_the_issuer(tmp_path):
    r, pc, agent, _ = setup(tmp_path)
    job = task(r, agent, pc)
    with pytest.raises(PermissionError):
        r.wait_status(pc['node'], pc['token'], job['id'], wait=0)


def test_agent_sees_pc_labels_and_online_state(tmp_path):
    r, pc, agent, _ = setup(tmp_path)
    assert r.agent_pcs(agent) == [{'label': '데스크톱', 'node': pc['node'], 'default': True, 'online': False}]
    with pytest.raises(PermissionError):
        r.agent_pcs('wrong')


# --- files -----------------------------------------------------------------------------------

def test_pc_file_reaches_only_the_main_agent(tmp_path):
    r, pc, agent, _ = setup(tmp_path)
    other = r.redeem(r.invite('노트북'))
    meta = r.file_put(pc['node'], pc['token'], 'agent-main', '발표.pptx', b'PPTX')
    assert meta['size'] == 4 and len(meta['id']) == 32
    assert r.file_get('agent-main', agent, meta['id']) == ('발표.pptx', b'PPTX')
    for node, token in ((other['node'], other['token']), (pc['node'], 'wrong')):
        with pytest.raises(PermissionError):
            r.file_get(node, token, meta['id'])


def test_main_agent_sends_files_only_to_registered_devices(tmp_path):
    r, pc, agent, _ = setup(tmp_path)
    meta = r.file_put('agent-main', agent, pc['node'], 'result.csv', b'a,b')
    assert r.file_get(pc['node'], pc['token'], meta['id']) == ('result.csv', b'a,b')
    with pytest.raises(ValueError):
        r.file_put('agent-main', agent, 'voice', 'x.txt', b'x')
    with pytest.raises(ValueError):  # PCs hand files to the main agent only.
        r.file_put(pc['node'], pc['token'], pc['node'], 'x.txt', b'x')


@pytest.mark.parametrize('name', ['', '../x', 'a/b', 'a\\b', 'x' * 200, 'con\x00.txt'])
def test_file_names_are_plain(tmp_path, name):
    r, pc, agent, _ = setup(tmp_path)
    with pytest.raises(ValueError):
        r.file_put(pc['node'], pc['token'], 'agent-main', name, b'x')


def test_files_expire_and_size_is_limited(tmp_path, monkeypatch):
    r, pc, agent, now = setup(tmp_path)
    meta = r.file_put(pc['node'], pc['token'], 'agent-main', 'a.txt', b'x')
    now[0] += 3601
    with pytest.raises(ValueError):
        r.file_get('agent-main', agent, meta['id'])
    assert not list((tmp_path / 'pc-files').glob(meta['id']))
    monkeypatch.setattr('agent.pc_remote.FILE_LIMIT', 10)
    with pytest.raises(ValueError):
        r.file_put(pc['node'], pc['token'], 'agent-main', 'big.bin', b'x' * 11)


def test_total_file_storage_is_bounded(tmp_path, monkeypatch):
    r, pc, agent, _ = setup(tmp_path)
    monkeypatch.setattr('agent.pc_remote.STORE_LIMIT', 25)
    r.file_put(pc['node'], pc['token'], 'agent-main', 'a.bin', b'x' * 10)
    r.file_put(pc['node'], pc['token'], 'agent-main', 'b.bin', b'x' * 10)
    with pytest.raises(ValueError):
        r.file_put(pc['node'], pc['token'], 'agent-main', 'c.bin', b'x' * 10)


# --- HTTP ------------------------------------------------------------------------------------

@pytest.fixture
def served(tmp_path):
    r, pc, agent, now = setup(tmp_path)
    server = ThreadingHTTPServer(('127.0.0.1', 0), pc_handler(r, tmp_path))
    threading.Thread(target=server.serve_forever, daemon=True).start()
    base = f'http://127.0.0.1:{server.server_port}'
    r._test_port = server.server_port

    def call(path, body, token, headers=None, raw=False):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        request = urllib.request.Request(base + path, data=data, method='POST',
                                         headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json', **(headers or {})})
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                content = response.read()
                return response.status, (content, dict(response.headers)) if raw else json.loads(content)
        except urllib.error.HTTPError as error:
            return error.code, json.loads(error.read())
    yield r, pc, agent, call
    server.shutdown()


def test_http_task_round_trip(served):
    r, pc, agent, call = served
    code, job = call('/request', {'node': 'agent-main', 'target': pc['node'], 'action': 'pc_task',
                                  'payload': {'op': 'windows', 'args': {}}, 'request_key': 'h1', 'ttl_seconds': 60}, agent)
    assert code == 200
    code, polled = call('/pc/poll', {'node': pc['node'], 'wait': 1}, pc['token'])
    assert code == 200 and polled['job']['id'] == job['id']
    assert call('/ack', {'node': pc['node'], 'id': job['id'], 'status': 'succeeded', 'result': {'ok': True}}, pc['token'])[0] == 200
    code, state = call('/request-status', {'node': 'agent-main', 'id': job['id'], 'wait': 1}, agent)
    assert code == 200 and state['status'] == 'succeeded'
    code, pcs = call('/pc/list', {'node': 'agent-main'}, agent)
    assert code == 200 and pcs['pcs'][0]['label'] == '데스크톱' and pcs['pcs'][0]['online'] is True


def test_http_poll_rejects_bad_wait_and_strangers(served):
    r, pc, agent, call = served
    assert call('/pc/poll', {'node': pc['node'], 'wait': 999}, pc['token'])[0] == 400
    assert call('/pc/poll', {'node': pc['node'], 'wait': 0}, 'bad')[0] == 401
    assert call('/pc/list', {'node': pc['node']}, pc['token'])[0] == 401


def test_http_file_upload_and_download(served):
    r, pc, agent, call = served
    name = urllib.parse.quote('발표 자료.pptx')
    code, meta = call('/pc/upload', b'\x00PPTX\xff', pc['token'],
                      {'X-Daevis-Node': pc['node'], 'X-Daevis-Audience': 'agent-main', 'X-Daevis-Name': name, 'Content-Type': 'application/octet-stream'})
    assert code == 200 and meta['size'] == 6
    code, (content, headers) = call('/pc/download', {'node': 'agent-main', 'id': meta['id']}, agent, raw=True)
    assert code == 200 and content == b'\x00PPTX\xff'
    assert urllib.parse.unquote(headers['X-Daevis-Name']) == '발표 자료.pptx'
    assert call('/pc/download', {'node': pc['node'], 'id': meta['id']}, 'bad')[0] == 401
    assert call('/pc/upload', b'x', 'bad', {'X-Daevis-Node': pc['node'], 'X-Daevis-Audience': 'agent-main', 'X-Daevis-Name': 'a'})[0] == 401


# --- download links for files too big for Discord -------------------------------------------

def test_main_agent_can_share_a_pc_file_by_link(tmp_path):
    r, pc, agent, now = setup(tmp_path)
    meta = r.file_put(pc['node'], pc['token'], 'agent-main', '발표.pptx', b'PPTX')
    share = r.file_share('agent-main', agent, meta['id'])
    assert len(share['token']) >= 40 and share['expires_hours'] == 24
    assert r.shared(share['token'])[0] == '발표.pptx'
    now[0] += 3600 * 2  # Past the normal one-hour lifetime, still inside the link's day.
    assert r.shared(share['token'])[1].read_bytes() == b'PPTX'
    now[0] += 3600 * 23
    with pytest.raises(ValueError):
        r.shared(share['token'])
    with pytest.raises(PermissionError):
        r.file_share(pc['node'], pc['token'], meta['id'])


def test_share_tokens_are_not_guessable_or_stored(tmp_path):
    r, pc, agent, _ = setup(tmp_path)
    meta = r.file_put(pc['node'], pc['token'], 'agent-main', 'a.txt', b'x')
    share = r.file_share('agent-main', agent, meta['id'])
    assert share['token'] not in (tmp_path / 'pc.sqlite3').read_bytes().decode('latin-1')
    for bad in ('', 'x' * 43, meta['id']):
        with pytest.raises(ValueError):
            r.shared(bad)


def test_stream_upload_moves_a_spooled_file(tmp_path):
    r, pc, agent, _ = setup(tmp_path)
    spooled = tmp_path / 'upload.part'
    spooled.write_bytes(b'BIG')
    meta = r.file_put(pc['node'], pc['token'], 'agent-main', 'big.pptx', spooled)
    assert not spooled.exists() and r.file_get('agent-main', agent, meta['id'])[1] == b'BIG'


def test_http_share_link_downloads_without_credentials(served):
    r, pc, agent, call = served
    meta = r.file_put(pc['node'], pc['token'], 'agent-main', '발표 자료.pptx', b'PPTX')
    code, share = call('/pc/share', {'node': 'agent-main', 'id': meta['id']}, agent)
    assert code == 200 and share['url'].endswith('/share/' + share['token'])
    import urllib.request as u
    local = 'http://127.0.0.1:%d/share/%s' % (r._test_port, share['token'])
    with u.urlopen(local, timeout=5) as response:
        assert response.read() == b'PPTX'
        assert "filename*=UTF-8''" in response.headers['Content-Disposition']
    with pytest.raises(urllib.error.HTTPError):
        u.urlopen('http://127.0.0.1:%d/share/%s' % (r._test_port, 'y' * 43), timeout=5)


def test_results_are_cleared_once_read_and_old_tasks_pruned(tmp_path):
    r, pc, agent, now = setup(tmp_path)
    job = task(r, agent, pc)
    got = r.task_poll(pc['node'], pc['token'], wait=0)
    r.ack(pc['node'], pc['token'], got['id'], 'succeeded', {'ok': True, 'elements': [{'value': 'secret'}]})
    assert r.wait_status('agent-main', agent, job['id'], wait=0)['result']['elements'][0]['value'] == 'secret'
    with r._db() as db:
        assert db.execute('SELECT result FROM jobs WHERE id=?', (job['id'],)).fetchone()['result'] is None
    now[0] += 3601
    r.task_poll(pc['node'], pc['token'], wait=0)
    with r._db() as db:
        assert not db.execute('SELECT 1 FROM jobs WHERE id=?', (job['id'],)).fetchone()
