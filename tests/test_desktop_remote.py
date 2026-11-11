"""PC side of remote control: the job loop, the off switch, the action log and each step."""
import json
import os
from pathlib import Path
import time
import unicodedata

import pytest

from desktop import pc_ops, remote, uia


class FakeClient:
    def __init__(self, jobs=()):
        self.jobs, self.acks, self.uploads, self.files = list(jobs), [], [], {}

    def poll(self, wait):
        return self.jobs.pop(0) if self.jobs else None

    def ack(self, job_id, status, result):
        self.acks.append((job_id, status, result))

    def upload(self, name, data):
        self.uploads.append((name, data))
        return {'id': '%032x' % len(self.uploads), 'name': name, 'size': len(data)}

    def download(self, file_id):
        return self.files[file_id]


def job(op, args=None, expires=None, job_id='j1'):
    return {'id': job_id, 'action': 'pc_task', 'source': 'agent-main', 'payload': {'op': op, 'args': args or {}},
            'expires': expires or time.time() + 60}


def make(tmp_path, ops, enabled=True, jobs=()):
    client = FakeClient(jobs)
    runner = remote.Remote(client, lambda: {'remote_control': enabled}, tmp_path / 'remote-actions.jsonl', ops=ops)
    return runner, client


def test_a_step_runs_and_is_acknowledged_and_logged(tmp_path):
    runner, client = make(tmp_path, {'windows': lambda args, ctx: {'windows': [{'title': 'x'}]}}, jobs=[job('windows')])
    assert runner.run_once() is True
    assert client.acks == [('j1', 'succeeded', {'ok': True, 'windows': [{'title': 'x'}]})]
    entry = json.loads((tmp_path / 'remote-actions.jsonl').read_text(encoding='utf-8').splitlines()[0])
    assert entry['op'] == 'windows' and entry['status'] == 'succeeded' and 'time' in entry


def test_switch_off_refuses_without_running(tmp_path):
    ran = []
    runner, client = make(tmp_path, {'shell': lambda args, ctx: ran.append(1)}, enabled=False, jobs=[job('shell', {'command': 'dir'})])
    runner.run_once()
    assert ran == [] and client.acks[0][1] == 'failed' and client.acks[0][2]['error'] == 'remote_off'


def test_expired_steps_are_not_run(tmp_path):
    ran = []
    runner, client = make(tmp_path, {'shell': lambda args, ctx: ran.append(1)}, jobs=[job('shell', expires=time.time() - 1)])
    runner.run_once()
    assert ran == [] and client.acks[0][1] == 'expired'


def test_failures_are_reported_not_raised(tmp_path):
    def boom(args, ctx):
        raise FileNotFoundError('C:/nope')
    runner, client = make(tmp_path, {'send_file': boom}, jobs=[job('send_file'), job('format', job_id='j2')])
    runner.run_once(); runner.run_once()
    assert client.acks[0][1] == 'failed' and 'FileNotFoundError' in client.acks[0][2]['error']
    assert client.acks[1][1] == 'failed' and client.acks[1][2]['error'] == 'unknown op'


def test_long_results_travel_as_an_attachment(tmp_path):
    rows = [{'path': 'C:/' + 'x' * 50 + str(i)} for i in range(100)]
    runner, client = make(tmp_path, {'find': lambda args, ctx: {'files': rows}}, jobs=[job('find')])
    runner.run_once()
    _, status, result = client.acks[0]
    assert status == 'succeeded' and len(json.dumps(result, ensure_ascii=False)) <= 2048
    assert result['data_file'] and json.loads(client.uploads[0][1])['files'] == rows


def test_log_keeps_commands_short(tmp_path):
    runner, client = make(tmp_path, {'shell': lambda args, ctx: {'output': ''}}, jobs=[job('shell', {'command': 'x' * 5000})])
    runner.run_once()
    entry = json.loads((tmp_path / 'remote-actions.jsonl').read_text(encoding='utf-8'))
    assert len(json.dumps(entry)) < 1000


def test_toggle_flips_and_saves(tmp_path):
    path = tmp_path / 'settings.json'
    assert remote.toggle(path) is False
    assert json.loads(path.read_text(encoding='utf-8'))['remote_control'] is False
    assert remote.toggle(path) is True


# --- find ------------------------------------------------------------------------------------

def tree(tmp_path):
    files = {
        'Desktop/캡스톤 발표자료.pptx': 1, 'Desktop/메모.txt': 3, 'Documents/학회/최종발표.pdf': 2,
        'Documents/node_modules/발표.pptx': 9, 'AppData/Local/발표.pptx': 9, 'Downloads/발표 원고.docx': 4,
    }
    for name, age in files.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'x')
        os.utime(path, (time.time() - age * 1000, time.time() - age * 1000))
    return tmp_path


def test_find_matches_words_and_extensions_newest_first(tmp_path):
    root = tree(tmp_path)
    found = pc_ops.find({'query': '발표', 'exts': ['pptx', 'pdf'], 'roots': [str(root)]}, None)['files']
    assert [Path(f['path']).name for f in found] == ['캡스톤 발표자료.pptx', '최종발표.pdf']
    assert found[0]['size'] == 1 and 'modified' in found[0]


def test_find_handles_decomposed_hangul_names(tmp_path):
    (tmp_path / unicodedata.normalize('NFD', '발표.key')).write_bytes(b'x')
    found = pc_ops.find({'query': '발표', 'roots': [str(tmp_path)]}, None)['files']
    assert len(found) == 1


def test_find_has_a_time_budget_and_limit(tmp_path):
    root = tree(tmp_path)
    assert len(pc_ops.find({'query': '', 'roots': [str(root)], 'limit': 2}, None)['files']) == 2
    result = pc_ops.find({'query': '발표', 'roots': [str(root)], 'seconds': 0}, None)
    assert result['complete'] is False


# --- files -------------------------------------------------------------------------------------

class Ctx:
    def __init__(self, client):
        self.client = client

    def upload(self, name, data):
        return self.client.upload(name, data)

    def download(self, file_id):
        return self.client.download(file_id)


def test_send_file_uploads_the_bytes(tmp_path):
    path = tmp_path / '발표.pptx'
    path.write_bytes(b'PPTX')
    client = FakeClient()
    result = pc_ops.send_file({'path': str(path)}, Ctx(client))
    assert client.uploads == [('발표.pptx', b'PPTX')] and result['file']['size'] == 4


def test_send_file_rejects_folders_and_big_files(tmp_path, monkeypatch):
    with pytest.raises(ValueError):
        pc_ops.send_file({'path': str(tmp_path)}, Ctx(FakeClient()))
    big = tmp_path / 'big.bin'
    big.write_bytes(b'x' * 11)
    monkeypatch.setattr(pc_ops, 'SEND_LIMIT', 10)
    with pytest.raises(ValueError, match='MB'):
        pc_ops.send_file({'path': str(big)}, Ctx(FakeClient()))


def test_save_file_lands_in_downloads_without_overwriting(tmp_path, monkeypatch):
    monkeypatch.setattr(pc_ops, 'DOWNLOADS', tmp_path)
    client = FakeClient()
    client.files['f' * 32] = ('../결과.csv', b'a,b')
    (tmp_path / '결과.csv').write_bytes(b'old')
    result = pc_ops.save_file({'file': 'f' * 32}, Ctx(client))
    saved = Path(result['path'])
    assert saved.parent == tmp_path and saved.read_bytes() == b'a,b' and saved.name == '결과 (1).csv'
    assert (tmp_path / '결과.csv').read_bytes() == b'old'


# --- shell, system, keys ---------------------------------------------------------------------

def test_shell_runs_powershell_and_reports_exit_code():
    result = pc_ops.shell({'command': 'Write-Output (1+1); exit 3', 'timeout': 60}, None)
    assert result['exit_code'] == 3 and result['output'].strip() == '2'


def test_shell_times_out():
    result = pc_ops.shell({'command': 'Start-Sleep 20', 'timeout': 2}, None)
    assert result['timed_out'] is True


@pytest.mark.parametrize('text,expected', [
    ('ctrl+c', [0x11, 0x43]), ('Ctrl+Shift+Esc', [0x11, 0x10, 0x1B]), ('win+r', [0x5B, 0x52]),
    ('enter', [0x0D]), ('alt+f4', [0x12, 0x73]), ('volume_up', [0xAF]),
])
def test_key_names(text, expected):
    assert uia.parse_keys(text) == expected


@pytest.mark.parametrize('text', ['', 'ctrl+', 'ctrl+banana', 'a+b+c+d+e+f+g'])
def test_bad_key_names(text):
    with pytest.raises(ValueError):
        uia.parse_keys(text)


def test_system_actions_use_keys_and_commands(monkeypatch):
    pressed, ran = [], []
    monkeypatch.setattr(uia, 'press', lambda keys: pressed.append(keys))
    monkeypatch.setattr(pc_ops, '_run', lambda argv: ran.append(argv))
    monkeypatch.setattr(pc_ops, '_lock', lambda: ran.append('lock'))
    pc_ops.system({'action': 'mute'}, None)
    pc_ops.system({'action': 'volume', 'level': 30}, None)
    pc_ops.system({'action': 'lock'}, None)
    pc_ops.system({'action': 'shutdown'}, None)
    assert pressed[0] == 'volume_mute'
    assert pressed.count('volume_down') == 50 and pressed.count('volume_up') == 15
    assert 'lock' in ran and ['shutdown.exe', '/s', '/t', '60'] in ran
    with pytest.raises(ValueError):
        pc_ops.system({'action': 'format'}, None)


# --- screenshot --------------------------------------------------------------------------------

def test_screenshot_is_downscaled_jpeg(monkeypatch):
    from PIL import Image
    monkeypatch.setattr(pc_ops, '_grab', lambda: (Image.new('RGB', (3840, 1080), 'white'), (-1920, 0)))
    client = FakeClient()
    result = pc_ops.screenshot({'max_width': 1600}, Ctx(client))
    name, data = client.uploads[0]
    assert name.endswith('.jpg') and data[:2] == b'\xff\xd8'
    assert result['width'] == 1600 and result['scale'] == pytest.approx(1600 / 3840) and result['origin'] == [-1920, 0]


# --- UI tree -------------------------------------------------------------------------------------

class Node:
    def __init__(self, name, kind='Button', children=(), rect=(0, 0, 10, 10), offscreen=False, value=None):
        self.name, self.kind, self.children, self.rect, self.offscreen, self.value = name, kind, list(children), rect, offscreen, value


class FakeTree:
    def children(self, node):
        return node.children

    def info(self, node):
        return {'name': node.name, 'type': node.kind, 'rect': list(node.rect), 'offscreen': node.offscreen,
                **({'value': node.value} if node.value is not None else {})}


def test_snapshot_numbers_elements_and_skips_offscreen():
    root = Node('창', 'Window', [Node('확인'), Node('숨김', offscreen=True), Node('', 'Pane', [Node('이름', 'Edit', value='홍')])])
    snap = uia.Snapshot(FakeTree())
    items = snap.take(root, limit=50)
    assert [i['id'] for i in items] == ['e1', 'e2', 'e3', 'e4']
    assert [i['name'] for i in items] == ['창', '확인', '', '이름'] and items[3]['value'] == '홍'
    assert snap.element('e2').name == '확인'
    with pytest.raises(ValueError):
        snap.element('e99')


def test_snapshot_respects_limit_and_depth():
    root = Node('창', 'Window', [Node(str(i)) for i in range(30)])
    assert len(uia.Snapshot(FakeTree()).take(root, limit=10)) == 10
    deep = Node('0', children=[Node('1', children=[Node('2', children=[Node('3')])])])
    assert len(uia.Snapshot(FakeTree()).take(deep, limit=50, depth=2)) == 3


def test_app_settings_do_not_undo_the_hotkey(tmp_path):
    from desktop import bridge, settings
    path = tmp_path / 'settings.json'
    app = bridge.Bridge(settings_path=path)
    remote.toggle(path)  # Owner pressed Ctrl+Alt+F12: off.
    app.set_setting('speak_typed', True)
    assert settings.load(path)['remote_control'] is False


# --- whole-PC file index -----------------------------------------------------------------------

from desktop import file_index


def test_index_builds_searches_and_skips_dependency_folders(tmp_path):
    root = tree(tmp_path / 'pc')
    venv = tmp_path / 'pc' / 'project' / '.venv2'
    (venv / 'lib').mkdir(parents=True)
    (venv / 'pyvenv.cfg').write_text('x')
    (venv / 'lib' / '발표.pptx').write_bytes(b'x')
    db = tmp_path / 'files.sqlite3'
    assert file_index.build(db, [root], pc_ops.SKIP_DIRS) == 4
    names = [Path(r['path']).name for r in file_index.search(['발표'], ['.pptx', '.pdf'], 10, db)]
    assert names == ['캡스톤 발표자료.pptx', '최종발표.pdf']
    assert file_index.age(db) < 60
    assert file_index.search(['100%_x'], [], 10, db) == []


def test_interrupted_build_keeps_the_old_index(tmp_path):
    root = tree(tmp_path / 'pc')
    db = tmp_path / 'files.sqlite3'
    file_index.build(db, [root], pc_ops.SKIP_DIRS)
    assert file_index.build(db, [root], pc_ops.SKIP_DIRS, should_stop=lambda: True) is None
    assert len(file_index.search([], [], 50, db)) == 4


def test_find_uses_the_index_and_the_user_folders(tmp_path, monkeypatch):
    home = tree(tmp_path / 'home')
    elsewhere = tmp_path / 'd' / 'univ' / '캡스톤'
    elsewhere.mkdir(parents=True)
    (elsewhere / '최종 발표.html').write_bytes(b'x')
    db = tmp_path / 'files.sqlite3'
    file_index.build(db, [elsewhere.parent.parent], pc_ops.SKIP_DIRS)
    monkeypatch.setattr(file_index, 'PATH', db)
    monkeypatch.setattr(pc_ops, 'HOME', home)
    monkeypatch.setattr(pc_ops, '_indexed', lambda *a: [])
    found = pc_ops.find({'query': '발표'}, None)
    names = {Path(f['path']).name for f in found['files']}
    assert {'최종 발표.html', '캡스톤 발표자료.pptx', '최종발표.pdf', '발표 원고.docx'} <= names
    assert found['index_age_hours'] == 0.0
