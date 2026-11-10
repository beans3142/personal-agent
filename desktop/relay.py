"""Relay client for the desktop app, using this PC's existing node credentials.

Requests, receipts and history go to the always-on power relay; nothing here
talks to the main server directly, so the app works while that server is off.
"""
import json
from pathlib import Path
import re
import time
import urllib.error
import urllib.request
import uuid

BASE = 'https://game.daehoon.games/pc-power'
FINAL = ('succeeded', 'failed', 'expired', 'uncertain', 'cancelled', 'unknown')
NODE = re.compile(r'pc-[a-f0-9]{16}')
CODE = re.compile(r'(?<![A-Za-z0-9_-])([A-Za-z0-9_-]{40,64})(?![A-Za-z0-9_-])')


class RelayError(Exception):
    def __init__(self, code):
        super().__init__(f'relay answered HTTP {code}')
        self.code = code


def no_redirect_opener():
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None  # Bearer credentials never follow a redirect.
    return urllib.request.build_opener(NoRedirect)


def _send(opener, url, body, headers):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST',
                                     headers={'Content-Type': 'application/json', **headers})
    try:
        with opener.open(request, timeout=25) as response:
            return json.loads(response.read(1_000_000))
    except urllib.error.HTTPError as error:
        raise RelayError(error.code) from None


def http_post(base, token):
    if not base.startswith('https://'):
        raise ValueError('relay must be HTTPS')
    opener = no_redirect_opener()
    def post(endpoint, body):
        return _send(opener, base.rstrip('/') + endpoint, body, {'Authorization': 'Bearer ' + token})
    return post


class Relay:
    def __init__(self, config, post=None):
        self.config = config
        self.node = config['node']
        self.label = config.get('label') or '이 PC'
        self.post = post or http_post(config['url'], Path(config['token_file']).read_text(encoding='utf-8').strip())

    def submit(self, text, via, thread=None):
        text = (text or '').strip()
        if not 1 <= len(text) <= 500:
            raise ValueError('요청은 1~500자로 적어 주세요.')
        payload = {'text': text} if via == 'voice' else {'text': text, 'via': via}
        if thread:
            payload['thread'] = thread  # Continue that conversation.
        receipt = self.post('/request', {'node': self.node, 'target': 'voice', 'action': 'voice_command', 'payload': payload,
                                         'request_key': 'app-' + uuid.uuid4().hex, 'ttl_seconds': 300})
        return receipt['id']

    def status(self, job_id):
        return self.post('/request-status', {'node': self.node, 'id': job_id})

    def follow(self, job_id, on_update, sleep=time.sleep, limit=320):
        """Poll the receipt about once a second; report each change; return the final state."""
        last, errors = object(), 0
        for _ in range(limit):
            try:
                state = self.status(job_id)
                errors = 0
            except (OSError, RelayError, ValueError):
                errors += 1  # Wi-Fi blips and relay restarts; the receipt is still there.
                if errors >= 10:
                    raise
                sleep(1)
                continue
            seen = (state.get('status'), state.get('result'))
            if seen != last:
                last = seen
                on_update(state)
            if state.get('status') in FINAL:
                return state
            sleep(1)
        return {'status': 'timeout', 'result': None}

    def history(self, before=None, limit=20, q=None):
        body = {'node': self.node, 'limit': limit}
        if before is not None:
            body['before'] = before
        if q:
            body['q'] = q
        return self.post('/history', body)

    def approve(self, job_id, approval, value):
        return self.post('/approve', {'node': self.node, 'id': job_id, 'approval': approval, 'value': value})


def registration_code(pasted):
    """The one-use code from Discord's "PC 등록" message, alone or inside the PowerShell command."""
    text = (pasted or '').strip()
    quoted = re.search(r"-Code\s+'([^']*)'", text)
    candidate = quoted[1] if quoted else text
    match = CODE.fullmatch(candidate)
    if not match:
        raise ValueError('등록 코드를 찾지 못했어요. Discord에서 "PC 등록 이름"으로 받은 명령을 그대로 붙여 넣어 주세요.')
    return match[1]


def find_config(home=None):
    found = sorted(Path(home or Path.home()).glob('.daevis/pcs/*/node.json'))
    return found[0] if found else None


def enroll(pasted, home=None, post=None):
    """Redeem a registration code and store credentials like install-pc.ps1 does."""
    code = registration_code(pasted)
    if post is None:
        opener = no_redirect_opener()
        post = lambda url, body: _send(opener, url, body, {})
    reply = post(BASE + '/enroll', {'code': code})
    if not isinstance(reply, dict) or not NODE.fullmatch(str(reply.get('node'))) or reply.get('url') != BASE or not reply.get('token'):
        raise ValueError('등록 서버 응답이 예상과 달라요. 다시 등록해 주세요.')
    folder = Path(home or Path.home()) / '.daevis' / 'pcs' / reply['node']
    folder.mkdir(parents=True, exist_ok=True)
    token_file = folder / 'node-token'
    token_file.write_text(reply['token'], encoding='utf-8')
    config = {'url': BASE, 'node': reply['node'], 'label': reply.get('label', ''), 'token_file': str(token_file),
              'actions': ['text_message', 'pc_shutdown', 'pc_cancel_shutdown'], 'allow_shutdown': True, 'poll_seconds': 5}
    path = folder / 'node.json'
    path.write_text(json.dumps(config, ensure_ascii=False, indent=1), encoding='utf-8')
    return path
