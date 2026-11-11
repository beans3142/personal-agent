"""Server tools that operate the owner's PC through the relay."""
import json
from pathlib import Path

import pytest

from agent import pc_tools


class FakeRelay:
    """Answers like the relay: a PC list, queued jobs and receipts the test scripts."""
    def __init__(self, receipts=None, pcs=None):
        self.pcs = pcs if pcs is not None else [{'label': '데스크톱', 'node': 'pc-aaaaaaaaaaaaaaaa', 'default': True, 'online': True}]
        self.receipts, self.requests, self.files, self.uploaded = list(receipts or []), [], {}, []

    def post(self, endpoint, body):
        if endpoint == '/pc/list':
            return {'pcs': self.pcs}
        if endpoint == '/request':
            self.requests.append(body)
            return {'id': 'j%d' % len(self.requests), 'status': 'queued'}
        if endpoint == '/request-status':
            return self.receipts.pop(0)
        raise AssertionError(endpoint)

    def download(self, file_id):
        return self.files[file_id]

    def upload(self, audience, name, data):
        self.uploaded.append((audience, name, data))
        return {'id': 'u' * 32, 'name': name, 'size': len(data)}


def link(relay, **kw):
    return pc_tools.PCLink(relay.post, relay.download, relay.upload, sleep=lambda s: None, **kw)


def ok(result):
    return {'status': 'succeeded', 'result': result}


def tools(relay, tmp_path):
    return {t.name: t for t in pc_tools.build_pc_tools(link(relay), outbox=lambda: tmp_path / 'outbox', files_root=tmp_path)}


def test_run_sends_a_pc_task_and_waits_for_the_receipt(tmp_path):
    relay = FakeRelay([{'status': 'dispatched', 'result': None}, ok({'ok': True, 'windows': []})])
    result = link(relay).run('windows', {})
    assert result == {'ok': True, 'windows': []}
    request = relay.requests[0]
    assert request['action'] == 'pc_task' and request['target'] == 'pc-aaaaaaaaaaaaaaaa' and request['payload'] == {'op': 'windows', 'args': {}}
    assert request['node'] == 'agent-main' and request['request_key'].startswith('pc-')


def test_long_results_are_fetched_from_the_attachment(tmp_path):
    relay = FakeRelay([ok({'ok': True, 'data_file': 'd' * 32, 'preview': '…'})])
    relay.files['d' * 32] = ('result.json', json.dumps({'ok': True, 'files': [1, 2, 3]}).encode())
    assert link(relay).run('find', {})['files'] == [1, 2, 3]


@pytest.mark.parametrize('receipt,message', [
    ({'status': 'failed', 'result': {'error': 'remote_off', 'message': '꺼져 있어요'}}, '꺼져 있어요'),
    ({'status': 'failed', 'result': {'error': 'ValueError: not a file'}}, 'not a file'),
    ({'status': 'expired', 'result': None}, '받지 않았'),
    ({'status': 'uncertain', 'result': None}, '확인하지 못'),
])
def test_failures_become_readable_errors(receipt, message):
    with pytest.raises(pc_tools.PCError, match=message):
        link(FakeRelay([receipt])).run('shell', {'command': 'x'})


def test_offline_or_unknown_pcs_are_refused():
    relay = FakeRelay(pcs=[{'label': '데스크톱', 'node': 'pc-a', 'default': True, 'online': False}])
    with pytest.raises(pc_tools.PCError, match='연결되어 있지 않'):
        link(relay).run('windows', {})
    with pytest.raises(pc_tools.PCError, match='등록된 PC'):
        link(FakeRelay()).run('windows', {}, pc='노트북')


@pytest.mark.parametrize('name', ['데스크톱', '내 데스크탑', '데스크톱 PC', 'desktop', None])
def test_pc_names_resolve(name):
    assert link(FakeRelay()).target(name) == 'pc-aaaaaaaaaaaaaaaa'


def test_the_only_online_pc_is_used_when_the_default_is_off():
    relay = FakeRelay(pcs=[{'label': '데스크톱', 'node': 'pc-a', 'default': True, 'online': False},
                           {'label': '노트북', 'node': 'pc-b', 'default': False, 'online': True}])
    assert link(relay).target(None) == 'pc-b'


def test_send_file_puts_the_pc_file_in_the_outbox(tmp_path):
    relay = FakeRelay([ok({'ok': True, 'file': {'id': 'f' * 32, 'name': '발표.pptx', 'size': 4}, 'path': 'C:/x/발표.pptx'})])
    relay.files['f' * 32] = ('발표.pptx', b'PPTX')
    text = tools(relay, tmp_path)['pc_send_file'].run({'path': 'C:/x/발표.pptx'})
    assert (tmp_path / 'outbox' / 'files' / '발표.pptx').read_bytes() == b'PPTX'
    assert '발표.pptx' in text and 'error' not in text


def test_screenshot_is_sent_to_the_owner(tmp_path):
    relay = FakeRelay([ok({'ok': True, 'file': {'id': 'f' * 32, 'name': 'screen.jpg', 'size': 3}, 'width': 1600, 'height': 900, 'scale': 0.5, 'origin': [0, 0]})])
    relay.files['f' * 32] = ('screen.jpg', b'JPG')
    text = tools(relay, tmp_path)['pc_screenshot'].run({})
    assert (tmp_path / 'outbox' / 'files' / 'screen.jpg').exists()
    assert '0.5' in text


def test_tools_return_errors_as_text(tmp_path):
    relay = FakeRelay([{'status': 'failed', 'result': {'error': 'ValueError: unknown key'}}])
    assert tools(relay, tmp_path)['pc_act'].run({'action': 'key', 'keys': 'ctrl+banana'}).startswith('error:')


def test_save_file_uploads_only_files_from_allowed_folders(tmp_path):
    produced = tmp_path / 'runs' / 'r1' / 'out' / 'chart.png'
    produced.parent.mkdir(parents=True)
    produced.write_bytes(b'PNG')
    relay = FakeRelay([ok({'ok': True, 'path': 'C:/Users/me/Downloads/chart.png', 'size': 3})])
    text = tools(relay, tmp_path)['pc_save_file'].run({'path': str(produced)})
    assert relay.uploaded == [('pc-aaaaaaaaaaaaaaaa', 'chart.png', b'PNG')] and 'Downloads' in text
    secret = tmp_path.parent / '.env'
    assert tools(FakeRelay(), tmp_path)['pc_save_file'].run({'path': str(secret)}).startswith('error:')


def test_shell_passes_command_and_timeout(tmp_path):
    relay = FakeRelay([ok({'ok': True, 'exit_code': 0, 'output': 'hi', 'timed_out': False})])
    text = tools(relay, tmp_path)['pc_shell'].run({'command': 'echo hi', 'timeout': 30})
    assert relay.requests[0]['payload'] == {'op': 'shell', 'args': {'command': 'echo hi', 'timeout': 30}}
    assert relay.requests[0]['ttl_seconds'] >= 30 and '"output": "hi"' in text


def test_every_tool_has_a_closed_schema(tmp_path):
    for tool in tools(FakeRelay(), tmp_path).values():
        assert tool.parameters['type'] == 'object' and tool.parameters.get('additionalProperties') is False
        assert tool.name.startswith('pc_')


def test_big_pc_files_become_a_download_link(tmp_path):
    big = 68 * 1024 * 1024
    relay = FakeRelay([ok({'ok': True, 'file': {'id': 'f' * 32, 'name': '해설.pptx', 'size': big}, 'path': 'C:/x/해설.pptx'})])
    shared = []
    original = relay.post
    def post(endpoint, body):
        if endpoint == '/pc/share':
            shared.append(body)
            return {'token': 't' * 43, 'url': 'https://relay/share/' + 't' * 43, 'expires_hours': 24}
        return original(endpoint, body)
    relay.post = post
    result = json.loads(tools(relay, tmp_path)['pc_send_file'].run({'path': 'C:/x/해설.pptx'}))
    assert shared == [{'node': 'agent-main', 'id': 'f' * 32}]
    assert result['link'].endswith('t' * 43) and result['link_expires_hours'] == 24
    assert not (tmp_path / 'outbox' / 'files').exists()
