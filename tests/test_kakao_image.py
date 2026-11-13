import json
from types import SimpleNamespace

import pytest

from agent import kakao_image
from agent.kakao_watch import KakaoWatcher, PREFIX, KakaoSendBlocked, build_kakao_send_tools


def node(resource='', text='', desc='', bounds='[0,0][10,10]', cls='android.widget.TextView', clickable='false', inner=''):
    return (f'<node text="{text}" resource-id="{resource}" class="{cls}" package="com.kakao.talk" content-desc="{desc}" '
            f'clickable="{clickable}" enabled="true" bounds="{bounds}">{inner}</node>')


def chat(bubbles, room='나'):
    parts = [node(PREFIX + 'toolbar_default_title_text', text=room)]
    for incoming, photo, text in bubbles:
        inner = (node(PREFIX + 'profile_layout') if incoming else '') + \
                (node(PREFIX + 'image', desc='Photo') if photo else '') + (node(PREFIX + 'message', text=text) if text else '')
        parts.append(node(PREFIX + 'bubble_root', inner=inner))
    return '<hierarchy>' + ''.join(parts) + '</hierarchy>'


def test_chat_items_include_photos_and_direction():
    items = kakao_image.chat_items(chat([(False, False, 'hi'), (True, True, ''), (False, True, '')]), '나', None)
    assert items == [{'incoming': False, 'photo': False, 'text': 'hi'}, {'incoming': True, 'photo': True, 'text': ''},
                     {'incoming': False, 'photo': True, 'text': ''}]


def test_chat_items_reject_another_room():
    with pytest.raises(ValueError):
        kakao_image.chat_items(chat([], room='남'), '나', None)


class FakeAdb:
    def __init__(self, indexed):
        self.indexed, self.calls = list(indexed), []

    def shell(self, argv):
        self.calls.append(argv)
        if argv[:2] == ['content', 'query']:
            return SimpleNamespace(output='\n'.join(f'Row: {i} _data={p}' for i, p in enumerate(self.indexed)) or 'No result found.')
        if argv[0] == 'rm':
            self.indexed.remove(argv[2].replace('/sdcard', '/storage/emulated/0'))
        return SimpleNamespace(output='')

    def push(self, local, remote):
        self.calls.append(['push', remote])
        self.indexed.append(remote.replace('/sdcard', '/storage/emulated/0'))


def test_stage_leaves_only_the_requested_picture_in_the_album(tmp_path):
    picture = tmp_path / 'image-0123456789.png'
    picture.write_bytes(b'png')
    adb = FakeAdb(['/storage/emulated/0/Pictures/Daevis/old.png', '/storage/emulated/0/DCIM/keep.jpg'])
    adb_rm = []
    original = adb.shell
    def shell(argv):
        if argv[0] == 'rm':
            adb_rm.append(argv[2])
            adb.indexed.remove(argv[2])
            return SimpleNamespace(output='')
        return original(argv)
    adb.shell = shell
    remote = kakao_image.stage(adb, picture, sleep=lambda _: None)
    assert remote == '/sdcard/Pictures/Daevis/image-0123456789.png'
    assert adb_rm == ['/storage/emulated/0/Pictures/Daevis/old.png']
    assert '/storage/emulated/0/DCIM/keep.jpg' in adb.indexed


def test_stage_fails_when_the_album_has_extra_pictures(tmp_path):
    picture = tmp_path / 'image-0123456789.png'
    picture.write_bytes(b'png')
    adb = FakeAdb([])
    # Another app writes into the album and rm cannot clear it.
    adb.shell = lambda argv: (SimpleNamespace(output='Row: 0 _data=/x/Pictures/Daevis/a.png\nRow: 1 _data=/x/Pictures/Daevis/b.png')
                              if argv[:2] == ['content', 'query'] else SimpleNamespace(output=''))
    adb.push = lambda local, remote: None
    with pytest.raises(ValueError):
        kakao_image.stage(adb, picture, sleep=lambda _: None)


def test_image_send_uses_the_same_room_proof_lock_and_journal(tmp_path, monkeypatch):
    watch = KakaoWatcher(None, tmp_path / 'watch.json')
    picture = tmp_path / 'image-0123456789.png'
    picture.write_bytes(b'png')
    calls = []
    watch.search_chat = lambda state: calls.append(('search', state['room']))
    import agent.kakao_image as module
    monkeypatch.setattr(module, 'send_image', lambda w, state, path: calls.append(('image', state['room'], path.name)))
    watch.send_image_once('unique person', picture, request_id='r1')
    assert calls == [('search', 'unique person'), ('image', 'unique person', 'image-0123456789.png')]
    journal = json.loads((tmp_path / 'kakao-direct-send.json').read_text())
    assert journal['status'] == 'succeeded'
    with pytest.raises(ValueError):
        watch.send_image_once('unique person', picture, request_id='r1')  # no automatic repeat


def test_uncertain_image_send_blocks_later_sends(tmp_path, monkeypatch):
    watch = KakaoWatcher(None, tmp_path / 'watch.json')
    picture = tmp_path / 'image-0123456789.png'
    picture.write_bytes(b'png')
    watch.search_chat = lambda state: None
    import agent.kakao_image as module
    monkeypatch.setattr(module, 'send_image', lambda *a: (_ for _ in ()).throw(RuntimeError('uncertain')))
    with pytest.raises(RuntimeError):
        watch.send_image_once('unique person', picture, request_id='r1')
    with pytest.raises(KakaoSendBlocked):
        watch.send_once('unique person', 'hello', request_id='r2')


def test_tool_only_sends_pictures_from_this_requests_outbox(tmp_path, monkeypatch):
    monkeypatch.setenv('AGENT_OUTBOX', str(tmp_path))
    (tmp_path / 'image-0123456789.png').write_bytes(b'png')
    sent = []
    monkeypatch.setattr(KakaoWatcher, 'send_image_once', lambda self, recipient, path, **kw: sent.append((recipient, path.name)))
    tool = {t.name: t for t in build_kakao_send_tools(None, tmp_path)}['kakao_send_image']
    assert tool.run({'recipient': '나', 'file': 'image-0123456789.png'}) == 'KAKAO_IMAGE_SENT'
    assert tool.run({'recipient': '나', 'file': '../secret.png'}) == 'KAKAO_IMAGE_NOT_FOUND'
    assert tool.run({'recipient': '나', 'file': 'image-aaaaaaaaaa.png'}) == 'KAKAO_IMAGE_NOT_FOUND'
    assert sent == [('나', 'image-0123456789.png')]


def test_gateway_reports_a_sent_picture_in_plain_words():
    from agent.gateway import kakao_status_reply
    assert kakao_status_reply('KAKAO_IMAGE_SENT') == '카톡으로 사진을 보냈습니다.'
