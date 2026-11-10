"""Desktop app logic without the window: relay client, enrollment, speech, settings, request flow."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from desktop import core, relay, settings, tts


# --- relay client ----------------------------------------------------------------

class FakePost:
    def __init__(self, replies):
        self.replies, self.calls = replies, []

    def __call__(self, endpoint, body):
        self.calls.append((endpoint, body))
        reply = self.replies[endpoint]
        return reply.pop(0) if isinstance(reply, list) else reply


def client(replies):
    post = FakePost(replies)
    return relay.Relay({'node': 'pc-0123456789abcdef', 'label': '데스크톱'}, post=post), post


def test_submit_sends_typed_text_with_a_fresh_key():
    r, post = client({'/request': {'id': 'j1', 'status': 'queued'}})
    assert r.submit('  오늘 일정  ', 'typed') == 'j1'
    endpoint, body = post.calls[0]
    assert endpoint == '/request' and body['target'] == 'voice' and body['action'] == 'voice_command'
    assert body['payload'] == {'text': '오늘 일정', 'via': 'typed'} and body['request_key'].startswith('app-')
    assert body['node'] == 'pc-0123456789abcdef' and body['ttl_seconds'] == 300


def test_spoken_requests_do_not_send_via_for_older_relays():
    r, post = client({'/request': {'id': 'j1'}})
    r.submit('안녕', 'voice')
    assert post.calls[0][1]['payload'] == {'text': '안녕'}


@pytest.mark.parametrize('text', ['', '   ', '가' * 501])
def test_empty_or_long_text_is_refused_locally(text):
    r, post = client({})
    with pytest.raises(ValueError):
        r.submit(text, 'typed')
    assert post.calls == []


def test_history_and_approve_bodies():
    r, post = client({'/history': {'items': [], 'more': False}, '/approve': {'ok': True}})
    r.history()
    r.history(before=123.5)
    r.approve('j1', 'abcdefabcdefabcd', 'allow')
    assert post.calls == [('/history', {'node': 'pc-0123456789abcdef', 'limit': 20}),
                          ('/history', {'node': 'pc-0123456789abcdef', 'limit': 20, 'before': 123.5}),
                          ('/approve', {'node': 'pc-0123456789abcdef', 'id': 'j1', 'approval': 'abcdefabcdefabcd', 'value': 'allow'})]


def test_follow_reports_each_change_once_and_returns_the_final_state():
    states = [{'status': 'queued', 'result': None}, {'status': 'dispatched', 'result': {'step': 'a'}},
              {'status': 'dispatched', 'result': {'step': 'a'}}, {'status': 'dispatched', 'result': {'step': 'b'}},
              {'status': 'succeeded', 'result': {'reply': '네', 'text': '**네**'}}]
    r, _ = client({'/request-status': states})
    seen = []
    final = r.follow('j1', seen.append, sleep=lambda s: None)
    assert [s['result'] for s in seen] == [None, {'step': 'a'}, {'step': 'b'}, {'reply': '네', 'text': '**네**'}]
    assert final['status'] == 'succeeded'


def test_follow_survives_short_network_errors_but_not_endless_ones():
    states = [OSError('reset'), relay.RelayError(502), {'status': 'succeeded', 'result': {'reply': '네'}}]
    def post(endpoint, body):
        state = states.pop(0)
        if isinstance(state, Exception):
            raise state
        return state
    r = relay.Relay({'node': 'pc-1'}, post=post)
    assert r.follow('j1', lambda s: None, sleep=lambda s: None)['status'] == 'succeeded'
    def down(endpoint, body):
        raise OSError('offline')
    with pytest.raises(OSError):
        relay.Relay({'node': 'pc-1'}, post=down).follow('j1', lambda s: None, sleep=lambda s: None)


def test_follow_gives_up_with_a_timeout_state():
    r, _ = client({'/request-status': {'status': 'dispatched', 'result': None}})
    assert r.follow('j1', lambda s: None, sleep=lambda s: None, limit=3)['status'] == 'timeout'


def test_https_is_required_and_redirects_are_not_followed():
    with pytest.raises(ValueError):
        relay.http_post('http://example.com', 'token')
    opener = relay.no_redirect_opener()
    assert any(type(h).__name__ == 'NoRedirect' for h in opener.handlers)


# --- enrollment -------------------------------------------------------------------

CODE = 'A' * 43


@pytest.mark.parametrize('pasted', [
    CODE,
    f"& ([scriptblock]::Create((Invoke-WebRequest -UseBasicParsing 'https://x/pc-power/install.ps1').Content)) -Code '{CODE}'",
    f'  {CODE}\n',
])
def test_registration_code_is_found_in_what_the_owner_pastes(pasted):
    assert relay.registration_code(pasted) == CODE


@pytest.mark.parametrize('pasted', ['', 'short', 'A' * 39, 'Code: ' + 'A' * 70])
def test_bad_codes_are_rejected(pasted):
    with pytest.raises(ValueError):
        relay.registration_code(pasted)


def test_enroll_writes_the_same_files_as_the_powershell_installer(tmp_path):
    def post(url, body):
        assert url == relay.BASE + '/enroll' and body == {'code': CODE}
        return {'node': 'pc-0123456789abcdef', 'token': 'secret-token', 'label': '노트북', 'url': relay.BASE, 'sha256': 'x'}
    path = relay.enroll(CODE, home=tmp_path, post=post)
    config = json.loads(path.read_text(encoding='utf-8'))
    assert path == tmp_path / '.daevis/pcs/pc-0123456789abcdef/node.json'
    assert config['node'] == 'pc-0123456789abcdef' and config['url'] == relay.BASE and config['allow_shutdown'] is True
    assert config['actions'] == ['text_message', 'pc_shutdown', 'pc_cancel_shutdown'] and config['label'] == '노트북'
    assert Path(config['token_file']).read_text() == 'secret-token'


@pytest.mark.parametrize('reply', [
    {'node': '../evil', 'token': 't', 'label': 'x', 'url': relay.BASE},
    {'node': 'pc-0123456789abcdef', 'token': 't', 'label': 'x', 'url': 'https://evil.example/pc-power'},
])
def test_enroll_rejects_unexpected_replies(tmp_path, reply):
    with pytest.raises(ValueError):
        relay.enroll(CODE, home=tmp_path, post=lambda url, body: reply)
    assert not (tmp_path / '.daevis').exists()


def test_find_config_picks_the_registered_pc(tmp_path):
    assert relay.find_config(tmp_path) is None
    folder = tmp_path / '.daevis/pcs/pc-1'
    folder.mkdir(parents=True)
    (folder / 'node.json').write_text('{"node": "pc-1"}', encoding='utf-8')
    assert relay.find_config(tmp_path) == folder / 'node.json'


# --- speech -------------------------------------------------------------------------

def test_voice_list_has_neural_and_offline_choices():
    ids = [v.id for v in tts.VOICES]
    assert 'windows' in ids and 'ko-KR-SunHiNeural' in ids and 'ko-KR-InJoonNeural' in ids
    assert tts.voice('missing').id == tts.DEFAULT_VOICE


@pytest.mark.parametrize('rate,edge,windows', [(0, '+0%', 0), (30, '+30%', 3), (-50, '-50%', -5), (99, '+50%', 5)])
def test_speed_maps_to_both_engines(rate, edge, windows):
    assert tts.edge_rate(rate) == edge and tts.windows_rate(rate) == windows


def test_windows_voice_gets_text_only_through_the_environment():
    argv, env = tts.windows_command("'; Remove-Item C:\\ ; '", 3)
    assert 'Remove-Item' not in ' '.join(argv) and env['DAEVIS_SAY'].startswith("';") and env['DAEVIS_RATE'] == '3'


def test_neural_voice_failure_falls_back_to_windows():
    played = []
    speaker = tts.Speaker(edge=lambda text, voice, rate, path: (_ for _ in ()).throw(OSError('offline')),
                          play=lambda path: played.append(('mp3', path)),
                          windows=lambda text, rate: played.append(('windows', text)))
    assert speaker.speak('안녕', 'ko-KR-SunHiNeural', 0) == 'windows'
    assert played == [('windows', '안녕')]


def test_neural_voice_is_synthesised_then_played(tmp_path):
    played = []
    def edge(text, voice, rate, path):
        Path(path).write_bytes(b'mp3')
        played.append((voice, rate))
    speaker = tts.Speaker(edge=edge, play=lambda path: played.append(Path(path).read_bytes()),
                          windows=lambda text, rate: pytest.fail('no fallback'), folder=tmp_path)
    assert speaker.speak('안녕', 'ko-KR-InJoonNeural', 20) == 'edge'
    assert played == [('ko-KR-InJoonNeural', '+20%'), b'mp3']
    assert list(tmp_path.iterdir()) == []  # Temporary audio is removed.


def test_empty_text_is_not_spoken():
    speaker = tts.Speaker(edge=lambda *a: pytest.fail(), play=lambda p: None, windows=lambda *a: pytest.fail())
    assert speaker.speak('  ', 'windows', 0) is None


# --- settings --------------------------------------------------------------------------

def test_settings_default_save_and_reload(tmp_path):
    s = settings.load(tmp_path / 's.json')
    assert s == settings.DEFAULTS
    s['voice'] = 'ko-KR-InJoonNeural'
    settings.save(tmp_path / 's.json', s)
    assert settings.load(tmp_path / 's.json')['voice'] == 'ko-KR-InJoonNeural'


def test_broken_or_foreign_settings_fall_back_to_defaults(tmp_path):
    path = tmp_path / 's.json'
    path.write_text('{broken', encoding='utf-8')
    assert settings.load(path) == settings.DEFAULTS
    path.write_text(json.dumps({'voice': 1, 'rate': 'fast', 'unknown': True, 'listen': False}), encoding='utf-8')
    loaded = settings.load(path)
    assert loaded['voice'] == settings.DEFAULTS['voice'] and loaded['rate'] == 0 and loaded['listen'] is False and 'unknown' not in loaded


# --- request flow -----------------------------------------------------------------------

class FakeRelay:
    def __init__(self, states):
        self.states, self.submitted, self.approved = states, [], []

    def submit(self, text, via):
        self.submitted.append((text, via))
        return 'j1'

    def follow(self, job_id, on_update, sleep=None, limit=None):
        for state in self.states:
            on_update(state)
        return self.states[-1]

    def approve(self, job_id, approval, value):
        self.approved.append((job_id, approval, value))


def controller(states, speak_typed=False):
    events, spoken = [], []
    c = core.Controller(FakeRelay(states), SimpleNamespace(speak=lambda text, voice, rate: spoken.append(text)),
                        {**settings.DEFAULTS, 'speak_typed': speak_typed}, events.append)
    return c, events, spoken


def test_typed_request_shows_steps_and_the_full_answer_without_speaking():
    c, events, spoken = controller([{'status': 'dispatched', 'result': {'step': '⏳ 검색하는 중…'}},
                                    {'status': 'succeeded', 'result': {'reply': '맑아요', 'text': '**맑아요**'}}])
    c.request('날씨', 'typed')
    kinds = [e['kind'] for e in events]
    assert kinds[0] == 'started' and 'step' in kinds and kinds[-1] == 'answer'
    assert events[-1]['text'] == '**맑아요**' and events[-1]['ok'] and spoken == []


def test_spoken_request_speaks_server_boot_notice_once_and_the_short_answer():
    boot = {'say': '서버를 켜고 있어요.'}
    c, events, spoken = controller([{'status': 'dispatched', 'result': boot}, {'status': 'dispatched', 'result': {**boot, 'step': 's'}},
                                    {'status': 'succeeded', 'result': {'reply': '맑아요', 'text': '**맑아요**'}}])
    c.request('날씨', 'voice')
    assert spoken == ['서버를 켜고 있어요.', '맑아요']


def test_typed_answers_are_spoken_when_the_owner_wants():
    c, _, spoken = controller([{'status': 'succeeded', 'result': {'reply': '네'}}], speak_typed=True)
    c.request('안녕', 'typed')
    assert spoken == ['네']


def test_payment_question_is_shown_once_and_answered_through_the_relay():
    approval = {'id': 'abcdefabcdefabcd', 'text': '💳 결제 확인', 'expires_in': 100}
    c, events, _ = controller([{'status': 'dispatched', 'result': {'step': 's', 'approval': approval}},
                               {'status': 'dispatched', 'result': {'step': 's', 'approval': approval}},
                               {'status': 'dispatched', 'result': {'step': 's2'}},
                               {'status': 'succeeded', 'result': {'reply': '주문했어요'}}])
    c.request('주문해줘', 'typed')
    asked = [e for e in events if e['kind'] == 'approval']
    assert len(asked) == 1 and asked[0]['approval'] == approval and asked[0]['job'] == 'j1'
    assert any(e['kind'] == 'approval_closed' for e in events)
    c.answer_payment('j1', 'abcdefabcdefabcd', 'allow')
    assert c.relay.approved == [('j1', 'abcdefabcdefabcd', 'allow')]


def test_failed_or_unreachable_requests_end_with_a_readable_message():
    c, events, spoken = controller([{'status': 'expired', 'result': None}])
    c.request('안녕', 'voice')
    assert events[-1]['kind'] == 'answer' and not events[-1]['ok'] and spoken == ['답을 받지 못했어요.']

    class Down(FakeRelay):
        def submit(self, text, via):
            raise OSError('offline')
    events.clear()
    c.relay = Down([])
    c.request('안녕', 'typed')
    assert events[-1]['kind'] == 'answer' and '연결' in events[-1]['text']


def test_cuda_folders_are_found_in_user_python_installs(tmp_path):
    folder = tmp_path / 'Packages/PythonSoftwareFoundation.Python.3.13_x/LocalCache/local-packages/Python313/site-packages/nvidia/cublas/bin'
    folder.mkdir(parents=True)
    assert core.cuda_dirs({'LOCALAPPDATA': str(tmp_path), 'APPDATA': str(tmp_path / 'none')}) == [folder]


def test_step_events_carry_the_step_list_for_the_timeline():
    c, events, _ = controller([{'status': 'dispatched', 'result': {'step': '⏳ 검색하는 중…', 'steps': ['웹 검색하는 중']}},
                               {'status': 'succeeded', 'result': {'reply': '네'}}])
    c.request('검색해줘', 'typed')
    step = next(e for e in events if e['kind'] == 'step')
    assert step['steps'] == ['웹 검색하는 중']


# --- command line for other agents ---------------------------------------------------

def test_cli_ask_prints_json_lines_and_exits_by_result():
    import io
    from desktop import cli
    out = io.StringIO()
    relay_ = FakeRelay([{'status': 'dispatched', 'result': {'step': 's', 'steps': ['a']}},
                        {'status': 'succeeded', 'result': {'reply': '네', 'text': '**네**'}}])
    code = cli.run(['ask', '안녕', '--json'], relay_, out)
    lines = [json.loads(line) for line in out.getvalue().splitlines()]
    assert code == 0 and lines[0]['kind'] == 'started' and lines[-1] == {'kind': 'answer', 'job': 'j1', 'ok': True, 'text': '**네**'}
    assert relay_.submitted == [('안녕', 'typed')]


def test_cli_plain_output_and_failure_code():
    import io
    from desktop import cli
    out = io.StringIO()
    code = cli.run(['ask', '안녕'], FakeRelay([{'status': 'expired', 'result': None}]), out)
    assert code == 1 and out.getvalue().strip() == '답을 받지 못했어요.'


def test_cli_history_json():
    import io
    from desktop import cli
    out = io.StringIO()
    r = SimpleNamespace(history=lambda before=None, limit=20: {'items': [{'id': 'j1', 'text': '안녕'}], 'more': False})
    assert cli.run(['history', '--limit', '5', '--json'], r, out) == 0
    assert json.loads(out.getvalue())['items'][0]['id'] == 'j1'


def test_cli_rejects_unknown_commands():
    import io
    from desktop import cli
    with pytest.raises(SystemExit):
        cli.run(['shutdown'], None, io.StringIO())


def test_display_errors_do_not_end_the_request():
    def broken(event):
        if event['kind'] == 'step':
            raise UnicodeEncodeError('cp949', '', 0, 1, 'x')
    spoken = []
    c = core.Controller(FakeRelay([{'status': 'dispatched', 'result': {'step': '⏳'}}, {'status': 'succeeded', 'result': {'reply': '네'}}]),
                        SimpleNamespace(speak=lambda text, voice, rate: spoken.append(text)), dict(settings.DEFAULTS), broken)
    c.request('안녕', 'voice')
    assert spoken == ['네']
