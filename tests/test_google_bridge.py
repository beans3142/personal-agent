import json
from pathlib import Path

import pytest

from agent import google_bridge
from agent.google_bridge import GoogleBridge, GoogleBridgeError, build_google_tools, kst_iso


class FakeBridge(GoogleBridge):
    def __init__(self, replies):
        super().__init__('https://script.example/exec', 'k')
        self.replies, self.sent = list(replies), []

    def post(self, body):
        self.sent.append(body)
        return self.replies.pop(0)


def tools(bridge):
    return {t.name: t for t in build_google_tools(bridge)}


def test_times_without_offset_are_korean_time():
    assert kst_iso('2026-11-15T15:00') == '2026-11-15T15:00:00+09:00'
    assert kst_iso('2026-11-15T06:00:00+00:00') == '2026-11-15T06:00:00+00:00'
    with pytest.raises(ValueError):
        kst_iso('내일 3시')


def test_every_call_carries_the_key_and_errors_surface():
    bridge = FakeBridge([{'ok': False, 'error': 'forbidden'}])
    with pytest.raises(GoogleBridgeError, match='forbidden'):
        bridge.call('calendar_list', start='a', end='b')
    assert bridge.sent[0]['key'] == 'k' and bridge.sent[0]['action'] == 'calendar_list'


def test_calendar_add_defaults_to_one_hour_and_reports_the_created_event():
    event = {'id': 'e1', 'title': '치과', 'start': '2026-11-15T06:00:00.000Z', 'end': '2026-11-15T07:00:00.000Z',
             'all_day': False, 'location': ''}
    bridge = FakeBridge([{'ok': True, 'event': event}])
    out = json.loads(tools(bridge)['calendar_add'].run({'title': '치과', 'start': '2026-11-15T15:00'}))
    sent = bridge.sent[0]
    assert sent['start'] == '2026-11-15T15:00:00+09:00' and sent['end'] == '2026-11-15T16:00:00+09:00'
    assert out['created']['title'] == '치과' and out['created']['start_kst'] == '2026-11-15 15:00'


def test_calendar_add_rejects_bad_input_without_calling_google():
    bridge = FakeBridge([])
    add = tools(bridge)['calendar_add']
    assert add.run({'title': '', 'start': '2026-11-15T15:00'}).startswith('error')
    assert add.run({'title': 'x', 'start': '2026-11-15T15:00', 'end': '2026-11-15T14:00'}).startswith('error')
    assert add.run({'title': 'x', 'start': '모레'}).startswith('error')
    assert bridge.sent == []


def test_all_day_event_sends_a_date_only():
    bridge = FakeBridge([{'ok': True, 'event': {'id': 'e', 'title': '휴가', 'start': '2026-11-20T00:00:00+09:00',
                                                 'end': '2026-11-21T00:00:00+09:00', 'all_day': True, 'location': ''}}])
    tools(bridge)['calendar_add'].run({'title': '휴가', 'start': '2026-11-20', 'all_day': True})
    assert bridge.sent[0]['all_day'] is True and bridge.sent[0]['start'] == '2026-11-20T00:00:00+09:00'


def test_calendar_list_shows_korean_times():
    bridge = FakeBridge([{'ok': True, 'events': [{'id': 'e', 'title': '회의', 'start': '2026-11-15T01:00:00.000Z',
                                                   'end': '2026-11-15T02:00:00.000Z', 'all_day': False, 'location': '판교'}]}])
    out = json.loads(tools(bridge)['calendar_list'].run({'start': '2026-11-15', 'end': '2026-11-16'}))
    assert out['events'][0]['start_kst'] == '2026-11-15 10:00' and out['events'][0]['location'] == '판교'


def test_small_upload_sends_base64_and_refuses_large_files(tmp_path, monkeypatch):
    small = tmp_path / '보고서.txt'
    small.write_bytes(b'hello')
    bridge = FakeBridge([{'ok': True, 'file': {'id': 'f', 'name': '보고서.txt', 'url': 'https://drive.google.com/file/d/f/view', 'size': 5}}])
    info = bridge.upload(small)
    assert bridge.sent[0]['data'] == 'aGVsbG8=' and bridge.sent[0]['name'] == '보고서.txt'
    assert info['url'].startswith('https://drive.google.com/')
    monkeypatch.setattr(google_bridge, 'MAX_DRIVE_BYTES', 3)
    with pytest.raises(GoogleBridgeError):
        bridge.upload(small)
    assert len(bridge.sent) == 1


def test_from_env_needs_both_url_and_key(tmp_path):
    url_file = tmp_path / 'google-bridge.url'
    assert google_bridge.from_env({'DAEVIS_GOOGLE_KEY': 'k'}, url_file) is None
    url_file.write_text('https://script.google.com/macros/s/x/exec\n')
    assert google_bridge.from_env({}, url_file) is None
    bridge = google_bridge.from_env({'DAEVIS_GOOGLE_KEY': 'k'}, url_file)
    assert bridge.url == 'https://script.google.com/macros/s/x/exec'


def test_drive_session_creates_the_file_first_and_returns_an_upload_address():
    bridge = FakeBridge([{'ok': True, 'token': 'tok', 'folder': 'fold'}])
    calls = []
    def rest(method, url, headers, body):
        calls.append((method, url, headers, body))
        if method == 'POST':
            return 200, {}, json.dumps({'id': 'fid', 'name': 'big.zip', 'webViewLink': 'https://drive.google.com/file/d/fid/view'}).encode()
        return 200, {'Location': 'https://upload.example/session'}, b''
    bridge.rest = rest
    out = bridge.drive_session('big.zip', 1_000_000_000, 'application/zip')
    assert out == {'id': 'fid', 'url': 'https://drive.google.com/file/d/fid/view', 'session': 'https://upload.example/session'}
    create, start = calls
    assert json.loads(create[3]) == {'name': 'big.zip', 'parents': ['fold'], 'mimeType': 'application/zip'}
    assert create[2]['Authorization'] == 'Bearer tok'
    assert start[0] == 'PATCH' and '/upload/drive/v3/files/fid?uploadType=resumable' in start[1]
    assert start[2]['X-Upload-Content-Length'] == '1000000000'


class PcLink:
    """Fake PC: answers stat, launch and status shell commands."""
    last_target = None
    def __init__(self, size, states):
        self.size, self.states, self.commands = size, list(states), []
    def run(self, op, step, pc, timeout=60):
        assert op == 'shell'
        command = step['command']
        self.commands.append(command)
        if 'Get-Item' in command:
            return {'exit_code': 0, 'output': json.dumps({'name': 'big.zip', 'size': self.size}), 'timed_out': False}
        if 'Start-Process' in command:
            return {'exit_code': 0, 'output': 'started', 'timed_out': False}
        state = self.states.pop(0) if self.states else {}
        return {'exit_code': 0, 'output': json.dumps(state), 'timed_out': False}


class Drive:
    def __init__(self):
        self.sessions = []
    def drive_session(self, name, size, mime):
        self.sessions.append((name, size, mime))
        return {'id': 'fid', 'url': 'https://drive.google.com/file/d/fid/view', 'session': "https://upload.example/s?x='1"}


def drive_tool(link, drive, tmp_path):
    from agent.pc_tools import build_pc_tools
    tools = {t.name: t for t in build_pc_tools(link, lambda: tmp_path, tmp_path, drive=drive, sleep=lambda _: None, drive_wait=30)}
    return tools['pc_drive_upload']


def test_pc_uploads_a_large_file_straight_to_drive(tmp_path):
    link = PcLink(1_000_000_000, [{'state': 'uploading', 'sent': 10, 'size': 1_000_000_000},
                                  {'state': 'done', 'sent': 1_000_000_000, 'size': 1_000_000_000, 'code': 200}])
    drive = Drive()
    out = json.loads(drive_tool(link, drive, tmp_path).run({'path': 'C:/Users/me/big.zip'}))
    assert out['drive_url'] == 'https://drive.google.com/file/d/fid/view' and out['upload'] == 'done'
    assert [(name, size) for name, size, _ in drive.sessions] == [('big.zip', 1_000_000_000)]
    launch = next(c for c in link.commands if 'Start-Process' in c)
    # The address is a literal inside the script, which is itself a literal in the command: quotes doubled twice.
    assert "https://upload.example/s?x=''''1" in launch
    # The file never passes through the server.
    assert not any(tmp_path.iterdir())


def test_slow_upload_returns_the_link_while_the_pc_keeps_going(tmp_path):
    link = PcLink(5_000_000_000, [{'state': 'uploading', 'sent': 1, 'size': 5_000_000_000}] * 50)
    out = json.loads(drive_tool(link, Drive(), tmp_path).run({'path': 'C:/big.iso'}))
    assert out['upload'] == 'uploading' and out['drive_url'] and '계속' in out['note']


def test_failed_upload_is_reported(tmp_path):
    link = PcLink(10, [{'state': 'error', 'error': 'disk'}])
    out = json.loads(drive_tool(link, Drive(), tmp_path).run({'path': 'C:/a.txt'}))
    assert out['upload'] == 'error' and 'disk' in out['error']


def test_too_large_file_never_opens_a_session(tmp_path):
    drive = Drive()
    big = PcLink(11 * 1024 ** 3, [])
    assert drive_tool(big, drive, tmp_path).run({'path': 'C:/x'}).startswith('error')
    assert drive.sessions == []


def test_drive_tool_is_absent_without_a_bridge(tmp_path):
    from agent.pc_tools import build_pc_tools
    names = {t.name for t in build_pc_tools(object(), lambda: tmp_path, tmp_path)}
    assert 'pc_drive_upload' not in names
