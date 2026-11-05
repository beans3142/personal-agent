"""Self-chat only. Identity is proven by Kakao's literal My Chatroom action.

No recipient/name is accepted from the caller. No room title or message body is
returned. A successful send requires a newly observed matching outgoing bubble.
UI-language/version changes fail closed; uncertain sends need owner review.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import time
import uuid
import xml.etree.ElementTree as ET

from .android_control import AndroidControl
from .kakao_watch import KakaoWatcher, PREFIX, read_chat
from .loop import Tool


class KakaoSelf:
    def __init__(self, device, data_path, sleep=time.sleep, *, request_id=None):
        self.request_id = uuid.uuid4().hex if request_id is None else request_id
        if not isinstance(self.request_id, str) or not 1 <= len(self.request_id) <= 128:
            raise ValueError('trusted request_id must contain 1..128 characters')
        self.control = AndroidControl(device)  # No screenshot outbox.
        self.watcher = KakaoWatcher(device, Path(data_path) / 'kakao-self-unused-watch.json')
        self.path = Path(data_path) / 'kakao-self-send.json'
        self.sleep = sleep

    def open_self(self):
        """Navigate using fresh labels; return a private verified title internally."""
        self.control.device.open('kakao')
        for _ in range(16):
            observation = self.control.observe()
            nodes = observation['nodes']
            if any(n.get('resource') == 'com.kakao.talk.finder:id/input_focus' for n in nodes):
                # Search result profile photos are not proof of self identity.
                self.control.key('back')
                self.sleep(.4)
                continue
            if any(n.get('package') for n in nodes) and not any(n.get('package') == 'com.kakao.talk' for n in nodes):
                # Cold app startup can still show the launcher. Back would
                # cancel startup, so wait for the app within the bounded loop.
                self.sleep(1)
                continue
            # Chat/message labels are untrusted content, never profile actions.
            in_chat = any(n.get('resource') == PREFIX + 'message_edit_text' for n in nodes)
            own = [] if in_chat else [n for n in nodes if not n.get('resource') and
                   (n.get('text') in {'My Chatroom', '나와의 채팅'} or
                    n.get('description') in {'My Chatroom', '나와의 채팅'})]
            if len(own) == 1:
                node = own[0]
                try:
                    self.control.click(observation['snapshot'], node['id'], node.get('text') or node['description'])
                except ValueError:
                    # Profile animations invalidate targets; re-observe only.
                    self.sleep(0.4)
                    continue
                # Only this exact native self action establishes identity.
                for _ in range(4):
                    self.sleep(0.4)
                    xml = self.control.device.dump_hierarchy()
                    root = ET.fromstring(xml)
                    titles = [n.get('content-desc') or n.get('text') for n in root.iter('node')
                              if n.get('resource-id') == PREFIX + 'toolbar_default_title_text']
                    inputs = [n for n in root.iter('node') if n.get('resource-id') == PREFIX + 'message_edit_text']
                    if len(titles) == 1 and titles[0] and len(inputs) == 1:
                        messages = read_chat(xml, titles[0], None)  # Reject member-count/group UI.
                        if any(message['incoming'] for message in messages):
                            raise ValueError('self room unexpectedly contains incoming messages')
                        return titles[0]
                raise ValueError('self chat did not become verifiable')
            if len(own) > 1:
                raise ValueError('ambiguous self chat action')
            profiles = [n for n in nodes if n.get('clickable') and
                        n.get('description', '').endswith(', Profile photo')]
            friends = [n for n in nodes if n.get('description') == 'Friends tab']
            if len(profiles) == 1:
                node = profiles[0]
                try:
                    self.control.click(observation['snapshot'], node['id'], node['description'])
                except ValueError:
                    pass
            elif len(friends) == 1:
                node = friends[0]
                try:
                    self.control.click(observation['snapshot'], node['id'], node['description'])
                except ValueError:
                    pass
            else:
                self.control.key('back')
            self.sleep(0.4)
        raise ValueError('self identity could not be established; no text entered')

    def _save(self, state):
        temporary = self.path.with_suffix('.tmp')
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as stream:
            json.dump(state, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(self.path)

    def send(self, text):
        if not isinstance(text, str) or not text.strip() or len(text) > 2000 or '\x00' in text:
            raise ValueError('self message must contain 1..2000 characters')
        self.path.parent.mkdir(parents=True, exist_ok=True)
        lock_path = self.path.with_suffix('.lock')
        try:
            lock = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise ValueError('self send locked; owner must review previous attempt') from None
        os.close(lock)
        try:
            state = json.loads(self.path.read_text()) if self.path.exists() else {'status': 'new', 'completed': []}
            if state['status'] == 'pending':
                raise ValueError('previous self send uncertain; owner review required')
            # The controller owns request_id; it is never a model tool input.
            # Same text in a later intentional owner request remains permitted.
            body_digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
            digest = hashlib.sha256((self.request_id + '\x00' + body_digest).encode('utf-8')).hexdigest()
            if digest in state['completed']:
                raise ValueError('same self message already sent in this owner request; automatic repeat denied')
            completed = state['completed'][-255:]
            room = self.open_self()
            self._save({'status': 'pending', 'hash': digest, 'completed': completed})
            self.watcher.send_text({'room': room, 'members': None,
                                    'until': (datetime.now(timezone.utc) + timedelta(seconds=45)).isoformat()},
                                   text, check_enabled=False)
            self._save({'status': 'succeeded', 'completed': completed + [digest]})
            return 'KAKAO_SENT'
        finally:
            lock_path.unlink(missing_ok=True)


def build_kakao_self_tools(device, data_path, *, request_id=None):
    helper = KakaoSelf(device, data_path, request_id=request_id)
    return [Tool('kakao_self_send', 'Send only to Kakao native My Chatroom after proving self identity. Returns status only. No recipient argument; uncertain sends never retry automatically.',
                 {'type': 'object', 'properties': {'text': {'type': 'string', 'minLength': 1, 'maxLength': 2000}},
                  'required': ['text'], 'additionalProperties': False},
                 lambda args: helper.send(args['text']), needs_approval=True)]
