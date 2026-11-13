import json
import threading
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from agent.device_nodes import DeviceStore, handler_for

NOW = 1_800_000_000


@pytest.fixture
def store(tmp_path):
    s = DeviceStore(tmp_path / 'nodes.sqlite3', clock=lambda: NOW)
    s.phone_token = s.enroll('phone-0123456789abcdef', ['phone_call', 'phone_alarm_at'])
    s.agent_token = s.enroll('agent-main', ['voice_command'])
    s.grant('agent-main', 'phone-0123456789abcdef', 'phone_alarm_at')
    return s


def alarm(store, at, text='일어나', key='k1'):
    return store.request('agent-main', store.agent_token, 'phone-0123456789abcdef', 'phone_alarm_at', {'at': at, 'text': text}, key, 300)


def test_alarm_job_waits_until_the_alarm_time_not_five_minutes(store):
    job = alarm(store, NOW + 26 * 3600)
    row = [j for j in store.list_jobs() if j['id'] == job['id']][0]
    assert row['expires'] == NOW + 26 * 3600


@pytest.mark.parametrize('payload', [
    {'at': NOW - 1, 'text': 'x'},                 # past
    {'at': NOW + 32 * 86400, 'text': 'x'},        # too far
    {'at': str(NOW + 60), 'text': 'x'},           # not an integer
    {'at': NOW + 60, 'text': ''},                 # empty label
    {'at': NOW + 60, 'text': 'x' * 61},           # long label
    {'at': NOW + 60},                             # missing label
    {'at': NOW + 60, 'text': 'x', 'extra': 1},    # unknown field
])
def test_bad_alarm_payloads_are_refused(store, payload):
    with pytest.raises(ValueError):
        store.request('agent-main', store.agent_token, 'phone-0123456789abcdef', 'phone_alarm_at', payload, 'bad', 300)


def test_app_poll_takes_only_alarm_jobs(store):
    store.enqueue('phone-0123456789abcdef', 'phone_call', {'name': '엄마'}, 'call-1', 60)
    job = alarm(store, NOW + 3600)
    taken = store.poll('phone-0123456789abcdef', store.phone_token, actions=['phone_alarm_at'])
    assert taken['id'] == job['id'] and taken['payload'] == {'at': NOW + 3600, 'text': '일어나'}
    assert store.poll('phone-0123456789abcdef', store.phone_token, actions=['phone_alarm_at']) is None
    # The call is still there for the call receiver.
    assert store.poll('phone-0123456789abcdef', store.phone_token)['action'] == 'phone_call'


def test_http_poll_accepts_an_action_filter(store):
    alarm(store, NOW + 3600)
    server = ThreadingHTTPServer(('127.0.0.1', 0), handler_for(store))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    def post(body):
        request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/poll', data=json.dumps(body).encode(),
                                         headers={'Authorization': 'Bearer ' + store.phone_token})
        with urllib.request.urlopen(request, timeout=3) as response:
            return json.load(response)
    try:
        assert post({'node': 'phone-0123456789abcdef', 'actions': ['phone_alarm_at']})['job']['action'] == 'phone_alarm_at'
        with pytest.raises(urllib.error.HTTPError) as error:
            post({'node': 'phone-0123456789abcdef', 'actions': ['no_such_action']})
        assert error.value.code == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)


def test_phone_registration_includes_alarms():
    from agent.pc_control import PHONE_CAPABILITIES
    assert 'phone_alarm_at' in PHONE_CAPABILITIES and 'phone_call' in PHONE_CAPABILITIES


class Relay:
    def __init__(self):
        self.sent = []
    def __call__(self, path, body):
        self.sent.append((path, body))
        return {'id': 'job1', 'status': 'queued', 'deduplicated': False}


def tool(relay, phone='phone-0123456789abcdef'):
    from agent.phone_alarm import build_phone_alarm_tools
    return build_phone_alarm_tools(relay, phone, now=lambda: NOW)[0]


def test_tool_sends_a_korean_time_alarm_to_the_phone():
    relay = Relay()
    # NOW is 2027-01-15 08:00 UTC = 17:00 KST; next day 04:00 KST.
    out = json.loads(tool(relay).run({'at': '2027-01-16T04:00', 'text': '기상'}))
    path, body = relay.sent[0]
    assert path == '/request' and body['target'] == 'phone-0123456789abcdef' and body['action'] == 'phone_alarm_at'
    assert body['payload'] == {'at': 1_800_000_000 - 17 * 3600 + 28 * 3600, 'text': '기상'}
    assert out['alarm_kst'] == '2027-01-16 04:00' and out['queued'] is True


def test_tool_refuses_past_times_and_defaults_the_label():
    relay = Relay()
    assert tool(relay).run({'at': '2020-01-01T04:00'}).startswith('error')
    assert relay.sent == []
    tool(relay).run({'at': '2027-01-16T04:00'})
    assert relay.sent[0][1]['payload']['text'] == '대비스 알람'
