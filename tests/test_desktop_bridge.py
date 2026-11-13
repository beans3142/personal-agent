"""Bridge between the HUD page and Python: what the page may call and what it is told."""
import json
from types import SimpleNamespace

import pytest

from desktop import bridge, settings


class FakeRelay:
    label = '데스크톱'

    def __init__(self):
        self.approved = []

    def history(self, before=None, limit=20, q=None):
        return {'items': [{'id': 'j1', 'text': '안녕', 'before': before, 'q': q}], 'more': False}

    def approve(self, job, approval, value):
        self.approved.append((job, approval, value))

    def pair(self, path, code=None):
        self.approved.append((path, code))
        return {'requests': [{'kind': 'phone', 'code': '1234', 'name': 'SM-A336N', 'expires_in': 500}]} if path == '/pair/pending' else {'name': 'SM-A336N'}


def make(tmp_path, registered=True):
    events = []
    b = bridge.Bridge(tmp_path / 's.json', emit=events.append, autostart=lambda on: events.append({'autostart': on}))
    b._speaker = SimpleNamespace(speak=lambda text, voice, rate: events.append({'spoke': text, 'voice': voice}), stop=lambda: None)
    if registered:
        b._connect(FakeRelay())
    return b, events


def test_boot_tells_the_page_about_link_voices_and_settings(tmp_path):
    b, _ = make(tmp_path)
    state = b.boot()
    assert state['registered'] and state['pc'] == '데스크톱'
    assert state['settings'] == settings.DEFAULTS
    assert {v['id'] for v in state['voices']} >= {'windows', 'ko-KR-SunHiNeural'}
    assert all(set(v) == {'id', 'name', 'detail', 'online'} for v in state['voices'])
    json.dumps(state)  # Must cross into JavaScript.
    assert b._ready.is_set() and state['status'] == ''
    assert state['features'] == {'wake': True, 'autostart': True, 'window': True, 'push': False, 'pair': True}


def test_unregistered_boot_and_failed_registration(tmp_path):
    b, _ = make(tmp_path, registered=False)
    assert b.boot()['registered'] is False
    assert b.register('nonsense')['ok'] is False


def test_settings_are_type_checked_saved_and_applied(tmp_path):
    b, events = make(tmp_path)
    assert b.set_setting('voice', 'ko-KR-InJoonNeural')['ok']
    assert b.set_setting('rate', 20)['ok']
    assert b.set_setting('autostart', False)['ok']
    assert {'autostart': False} in events
    for key, value in (('voice', 'nobody'), ('rate', 'fast'), ('rate', 500), ('listen', 'yes'), ('unknown', 1)):
        assert b.set_setting(key, value)['ok'] is False
    stored = settings.load(tmp_path / 's.json')
    assert stored['voice'] == 'ko-KR-InJoonNeural' and stored['rate'] == 20 and stored['autostart'] is False


def test_preview_speaks_a_sample_in_the_chosen_voice(tmp_path):
    b, events = make(tmp_path)
    b.preview('ko-KR-HyunsuMultilingualNeural', 0, wait=True)
    spoken = [e for e in events if 'spoke' in e]
    assert spoken and spoken[0]['voice'] == 'ko-KR-HyunsuMultilingualNeural'
    assert {'kind': 'mode', 'mode': 'speaking'} in events and events[-1] == {'kind': 'mode', 'mode': 'idle'}


def test_send_rejects_empty_text_and_runs_requests_in_the_background(tmp_path):
    b, _ = make(tmp_path)
    ran = []
    b._controller = SimpleNamespace(request=lambda text, via, thread=None: ran.append((text, via)), busy=SimpleNamespace(locked=lambda: False))
    assert b.send('   ')['ok'] is False
    assert b.send('오늘 일정', wait=True) == {'ok': True, 'queued': False}
    assert ran == [('오늘 일정', 'typed')]


def test_history_and_payment_pass_through_and_errors_become_messages(tmp_path):
    b, _ = make(tmp_path)
    assert b.history(12.5)['items'][0]['before'] == 12.5
    assert b.answer_payment('j1', 'abcdefabcdefabcd', 'allow')['ok']
    assert b._relay.approved == [('j1', 'abcdefabcdefabcd', 'allow')]
    assert b.answer_payment('j1', 'abcdefabcdefabcd', 'maybe')['ok'] is False
    assert b.history(None, ' 초밥 ')['items'][0]['q'] == '초밥'
    b._relay.history = lambda before=None, limit=20, q=None: (_ for _ in ()).throw(OSError('offline'))
    assert 'error' in b.history()


def test_page_cannot_reach_internal_objects(tmp_path):
    b, _ = make(tmp_path)
    public = {name for name in dir(b) if not name.startswith('_')}
    assert public == {'boot', 'register', 'send', 'history', 'answer_payment', 'set_setting', 'preview',
                      'stop_speaking', 'minimize', 'hide', 'quit', 'open_link',
                      'pair_start', 'pair_poll', 'pair_pending', 'pair_accept', 'pair_deny'}


def test_pump_batches_events_and_keeps_only_the_latest_level():
    from desktop.app import coalesce
    events = [{'kind': 'level', 'value': 0.1}, {'kind': 'step', 'text': 'a'}, {'kind': 'level', 'value': 0.2},
              {'kind': 'level', 'value': 0.3}, {'kind': 'answer', 'text': 'b'}]
    assert coalesce(events) == [{'kind': 'step', 'text': 'a'}, {'kind': 'answer', 'text': 'b'}, {'kind': 'level', 'value': 0.3}]


def test_download_links_open_only_relay_share_urls(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(bridge.os, 'startfile', lambda url: opened.append(url), raising=False)
    b = bridge.Bridge(tmp_path / 's.json')
    good = bridge.relay.BASE + '/share/' + 'a' * 43
    assert b.open_link(good) == {'ok': True} and opened == [good]
    for bad in ('https://evil.example/share/' + 'a' * 43, good + '/../x', 'file:///C:/Windows/notepad.exe', bridge.relay.BASE + '/share/x'):
        assert b.open_link(bad) == {'ok': False}
    assert opened == [good]


def test_new_pc_asks_to_connect_and_is_linked_once_accepted(tmp_path, monkeypatch):
    b, _ = make(tmp_path, registered=False)
    linked = []
    b._on_register = linked.append
    replies = iter([{'id': 'a' * 32, 'code': '0042', 'expires_in': 600}, {'status': 'pending'},
                    {'status': 'approved', 'node': 'pc-0123456789abcdef', 'token': 't', 'label': 'DESKTOP-1', 'url': bridge.relay.BASE}])
    sent = []
    monkeypatch.setattr(bridge.relay, 'anonymous_post', lambda path, body: (sent.append((path, body)), next(replies))[1])
    monkeypatch.setattr(bridge.relay, 'store_registration', lambda reply, home=None: tmp_path / 'node.json')
    assert b.pair_start() == {'ok': True, 'code': '0042', 'expires_in': 600}
    assert sent[0][0] == '/pair/start' and sent[0][1]['kind'] == 'pc'
    assert b.pair_poll() == {'status': 'pending'}
    assert b.pair_poll() == {'status': 'approved'}
    assert linked == [tmp_path / 'node.json']


def test_connected_pc_sees_and_answers_requests(tmp_path):
    b, _ = make(tmp_path)
    assert b.pair_pending()['requests'][0]['code'] == '1234'
    assert b.pair_accept('1234') == {'ok': True}
    assert b.pair_deny('1234') == {'ok': True}
    assert b._relay.approved[-2:] == [('/pair/accept', '1234'), ('/pair/deny', '1234')]
    unlinked, _ = make(tmp_path, registered=False)
    assert unlinked.pair_pending() == {'requests': []}
