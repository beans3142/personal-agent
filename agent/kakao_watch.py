"""Time-bounded replies in one explicitly selected Kakao room."""
from __future__ import annotations

import json
import threading
import re
import os
import time
import hashlib
import uuid
import unicodedata
from difflib import SequenceMatcher
import xml.etree.ElementTree as ET
from datetime import datetime, timezone, timedelta
from pathlib import Path

from .android_control import AndroidControl
from .kakao_targets import KakaoTargets

PREFIX = "com.kakao.talk:id/"
DIRECT_PENDING_SECONDS = 120


def expire_direct_pending(journal, now=None):
    """End an abandoned send without retrying it or forgetting its request hash.

    Caller holds the direct-send lock (or has verified no worker is running).
    Legacy journals have no timestamp, so their file mtime is the start time.
    """
    journal = Path(journal)
    previous = json.loads(journal.read_text()) if journal.exists() else {}
    if previous.get('status') != 'pending':
        return previous
    started = previous.get('started_at', journal.stat().st_mtime)
    if (time.time() if now is None else now) - started < DIRECT_PENDING_SECONDS:
        return previous
    completed = previous.get('completed', [])
    if previous.get('request_hash'):
        completed = list(dict.fromkeys(completed + [previous['request_hash']]))[-256:]
    previous = {**previous, 'status': 'expired_uncertain', 'completed': completed}
    temporary = journal.with_suffix('.tmp')
    temporary.write_text(json.dumps(previous))
    os.chmod(temporary, 0o600)
    os.replace(temporary, journal)
    return previous


class KakaoRecipientUnavailable(ValueError):
    """No draft/send was attempted; read-only recipient lookup may follow."""


def same_name(name, query):
    """Identical apart from spaces and Unicode form: "반수의 신" is "반수의신"."""
    squash = lambda value: ''.join(unicodedata.normalize('NFC', value).split())
    return squash(name) == squash(query)


class KakaoSendBlocked(ValueError):
    """An earlier send never confirmed; the owner must check it before new sends."""


def unlock_direct_send(data_dir):
    """Owner reviewed the unconfirmed send: keep its record, allow new sends.

    Returns when the stuck send started (file time), or None if nothing was locked.
    """
    journal = Path(data_dir) / 'kakao-direct-send.json'
    lock = journal.with_suffix('.lock')
    try:
        previous = json.loads(journal.read_text())
    except (OSError, ValueError):
        previous = {}
    if previous.get('status') != 'pending' and not lock.exists():
        return None
    started = datetime.fromtimestamp(journal.stat().st_mtime, timezone.utc) if journal.exists() else datetime.now(timezone.utc)
    if previous.get('status') == 'pending':
        temp = journal.with_suffix('.tmp')
        temp.write_text(json.dumps({**previous, 'status': 'reviewed'}))
        os.chmod(temp, 0o600)
        os.replace(temp, journal)
    lock.unlink(missing_ok=True)
    return started


def observed_group_members(xml, room):
    root = ET.fromstring(xml)
    titles = [n.get('content-desc') or n.get('text') for n in root.iter('node') if n.get('resource-id') == PREFIX+'toolbar_default_title_text']
    counts = [n.get('content-desc') or n.get('text') for n in root.iter('node') if n.get('resource-id') == PREFIX+'toolbar_default_title_count']
    if len(counts) != 1 or not counts[0] or not counts[0].isdigit() or int(counts[0]) < 2 or titles != [room+counts[0]]:
        raise ValueError('exact group title and member count not verified')
    return int(counts[0])


def global_search_targets(nodes):
    """Tablet has both list search and conversation search: select list only."""
    def inside(child, parent):
        a, b, c, d = parent['bounds']; x, y, z, w = child['bounds']
        return a <= x <= z <= c and b <= y <= w <= d
    bars = [n for n in nodes if n['resource'] == PREFIX + 'toolbar' and
            any(t['resource'] == PREFIX + 'toolbar_main_title_text' and t['text'] in {'Chats', '채팅'} and inside(t, n) for t in nodes)]
    return [n for n in nodes if n['package'] == 'com.kakao.talk' and n['description'] in {'Search', '검색'} and any(inside(n, bar) for bar in bars)]


def read_chat(xml: str, room: str, members: int | None) -> list[dict]:
    root = ET.fromstring(xml)
    titles = [n.get("content-desc") or n.get("text") for n in root.iter("node") if n.get("resource-id") == PREFIX + "toolbar_default_title_text"]
    counts = [n.get("content-desc") or n.get("text") for n in root.iter("node") if n.get("resource-id") == PREFIX + "toolbar_default_title_count"]
    counts = list(filter(None, counts))
    if titles != [room + (str(members) if members is not None else "")] or counts != ([str(members)] if members is not None else []):
        raise ValueError("selected Kakao room is not open")
    result = []
    for bubble in root.iter("node"):
        if bubble.get("resource-id") != PREFIX + "bubble_root":
            continue
        nodes = list(bubble.iter("node"))
        incoming = any(n.get("resource-id") == PREFIX + "profile_layout" for n in nodes)
        texts = [n.get("text") or n.get("content-desc") for n in nodes if n.get("resource-id") == PREFIX + "message"]
        body = "\n".join(filter(None, texts))
        if not body and any(n.get("resource-id") == PREFIX + "message_container" for n in nodes):
            body = "[비텍스트 메시지: 이미지/첨부 내용은 확인하지 못함]"
        if body:
            result.append({"incoming": incoming, "text": body})
    return result


def appended_messages(old: list[dict], current: list[dict]) -> list[dict]:
    if not old:
        return []
    for overlap in range(min(len(old), len(current)), 0, -1):
        if old[-overlap:] == current[:overlap]:
            return current[overlap:]
    raise ValueError("message window lost overlap; automatic replay is unsafe")


def new_messages(old: list[dict], current: list[dict]) -> list[dict]:
    return [m for m in appended_messages(old, current) if m["incoming"]]


class KakaoWatcher:
    def __init__(self, device, path: Path):
        self.control = AndroidControl(device)
        self.path = path
        self.targets = KakaoTargets(path.with_name('kakao-targets.json'))
        self._lock = threading.RLock()

    def load(self):
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {"enabled": False}

    def save(self, state, keep_stopped=False):
        with self._lock:
            if keep_stopped and not self.load().get("enabled"):
                state["enabled"] = False
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            temp.chmod(0o600)
            temp.replace(self.path)

    def stop(self):
        with self._lock:
            state = self.load()
            state["enabled"] = False
            self.save(state)

    def chat(self, state):
        device = self.control.device
        for attempt in range(3):
            xml = device.dump_hierarchy()
            try:
                return read_chat(xml, state["room"], state["members"])
            except ValueError:
                if attempt == 0:
                    device.open("kakao")
                    continue
            # The tablet chat list exposes a labelled button with the exact
            # title and member count. Never choose a similarly named room.
            observation = self.control.observe()
            prefix = (f"{state['room']}, ", f"me, {state['room']}, ") if state["members"] is None else f"{state['room']}, {state['members']} friends,"
            matches = [n for n in observation["nodes"] if n.get("description", "").startswith(prefix) and n.get("clickable") and (state["members"] is not None or not re.search(r",\s*\d+ friends,", n.get("description", "")))]
            if len(matches) == 1:
                node = matches[0]
                self.control.click(observation["snapshot"], node["id"], node["description"])
        # A match may have been clicked on the final navigation attempt.
        return read_chat(device.dump_hierarchy(), state["room"], state["members"])

    def tick(self, now: datetime, reply):
        state = self.load()
        if not state.get("enabled"):
            return "disabled"
        if now >= datetime.fromisoformat(state["until"]):
            state["enabled"] = False
            self.save(state, keep_stopped=True)
            return "expired"
        if state.get("pending"):
            return "paused"
        current = self.chat(state)
        if "tail" not in state:
            state["tail"] = current
            self.save(state, keep_stopped=True)
            return "baseline"
        incoming = state.pop("backlog", []) + new_messages(state["tail"], current)
        state["tail"] = current
        state["last_poll"] = now.isoformat()
        if not incoming:
            self.save(state, keep_stopped=True)
            return "unchanged"
        state["pending"] = True
        state["pending_incoming"] = incoming
        self.save(state, keep_stopped=True)  # An uncertain send must not resend.
        if not state["enabled"]:
            return "disabled"
        reply({**state, "incoming": incoming, "context": current[-12:]})
        # Keep the pre-generation cursor. Incoming messages that arrived while
        # drafting/sending must still be discovered by the next poll.
        state["pending"] = False
        state.pop("pending_incoming", None)
        state["replies"] = state.get("replies", 0) + 1
        self.save(state, keep_stopped=True)
        return "replied"

    def _search_results(self, query):
        """Open native global search and enter only the lookup query."""
        c = self.control
        c.device.open('kakao')
        if any(n.get('resource') == 'com.kakao.talk.finder:id/input_focus' for n in c.observe()['nodes']):
            return self._enter_search_query(query)
        # Return to the native chat list; never search a conversation's body.
        for _ in range(8):
            o = c.observe()
            tabs = [n for n in o['nodes'] if n.get('description', '').startswith('Chats tab')]
            titles = [n for n in o['nodes'] if n.get('resource') == PREFIX + 'toolbar_main_title_text' and n.get('text') in {'Chats', '채팅'}]
            if titles:
                break
            if len(tabs) == 1:
                n = tabs[0]
                try: c.click(o['snapshot'], n['id'], n['description'])
                except ValueError: pass
            elif any(n.get('package') == 'com.kakao.talk' for n in o['nodes']):
                c.key('back')
            time.sleep(.4)
        else:
            raise ValueError('chat list unavailable')
        for _ in range(5):
            o = c.observe()
            ids = {n['id'] for n in global_search_targets(c.nodes)}
            buttons = [n for n in o['nodes'] if n['id'] in ids]
            if len(buttons) == 1:
                n = buttons[0]
                try:
                    c.click(o['snapshot'], n['id'], n['description'])
                    break
                except ValueError: pass
            time.sleep(.3)
        else:
            raise ValueError('chat search unavailable')

        self._enter_search_query(query)

    def _enter_search_query(self, query):
        c = self.control
        for _ in range(20):
            o = c.observe()
            fields = [n for n in o['nodes'] if n.get('input') and n.get('package') == 'com.kakao.talk' and n.get('resource') not in {PREFIX + 'message_edit_text', 'com.kakao.talk.finder:id/input_focus'}]
            if len(fields) == 1:
                try:
                    c.input(o['snapshot'], fields[0]['id'], text=query)
                    break
                except ValueError: pass
            else:
                # Native search first exposes an unfocused launcher field;
                # explicitly activate it instead of waiting for the keyboard.
                launchers = [n for n in o['nodes'] if n.get('resource') == 'com.kakao.talk.finder:id/input_focus']
                if len(launchers) == 1:
                    n = launchers[0]
                    try: c.click(o['snapshot'], n['id'], n.get('text') or n.get('description', ''))
                    except ValueError: pass
            time.sleep(.4)
        else:
            raise ValueError('search input unavailable')

    def search_chat(self, state):
        """Use native search, select one exact name, then verify the chat title."""
        group = state.get('kind') == 'group' or state['members'] is not None
        c = self.control
        self._search_results(state['room'])
        for _ in range(10):
            time.sleep(.3)
            o = c.observe()
            matches = []
            for n in o['nodes']:
                if n.get('package') != 'com.kakao.talk' or not n.get('clickable') or n.get('input'):
                    continue
                desc, text = n.get('description', ''), n.get('text', '')
                group_label = bool(re.match(re.escape(state['room'])+r'\s+\d+\b', text) or re.match(re.escape(state['room'])+r',\s*\d+ friends,', desc))
                exact = desc == state['room'] or desc.startswith((state['room']+', ', 'me, '+state['room']+', '))
                if group_label if group else exact and not re.search(r',\s*\d+ friends,', desc):
                    matches.append(n)
            if len(matches) > 1:
                raise ValueError('ambiguous exact search results')
            if len(matches) == 1:
                n = matches[0]
                try:
                    c.click(o['snapshot'], n['id'], n.get('text') or n.get('description', ''))
                    break
                except ValueError: pass
        else:
            raise ValueError('no exact search result')
        for _ in range(8):
            time.sleep(.3)
            try:
                xml = c.device.dump_hierarchy()
                if group:
                    members = observed_group_members(xml, state['room'])
                    if state['members'] is not None and state['members'] != members:
                        raise ValueError('group member count changed')
                    state['members'] = members
                result = read_chat(xml, state['room'], state['members'])
                self.targets.remember(state['room'], state['room'], 'group' if group else 'individual', state['members'])
                return result
            except ValueError:
                pass
            if group:
                continue
            o = c.observe()
            # A Friends result opens its profile before 1:1 Chat.
            names = [n for n in o['nodes'] if n.get('text') == state['room'] and not n.get('input')]
            actions = [n for n in o['nodes'] if not n.get('resource') and (n.get('text') in {'1:1 Chat', '1:1 채팅'} or n.get('description') in {'1:1 Chat', '1:1 채팅'})]
            if len(names) == 1 and len(actions) == 1:
                n = actions[0]
                try: c.click(o['snapshot'], n['id'], n.get('text') or n.get('description'))
                except ValueError: pass
        raise ValueError('exact chat could not be verified')

    def find_candidates(self, query):
        """Fuzzy candidates are evidence for clarification, never send targets."""
        if not isinstance(query, str) or not 1 <= len(query.strip()) <= 30:
            raise ValueError('lookup query must contain 1..30 characters')
        cached = [e for kind in ('individual', 'group') if (e := self.targets.get(query, kind))]
        if cached:
            return {'status': 'cached_hints', 'candidates': [{'name': e['title'], 'exact': same_name(e['title'], query),
                    'room_type': e['kind'], 'members': e['members'], 'uses': e['hits']} for e in cached],
                    'requires_live_verification': True}
        normalize = lambda value: ''.join(unicodedata.normalize('NFC', value).split()).casefold()
        normalized = normalize(query)
        queries = list(dict.fromkeys([query, normalized[:2], normalized[-2:]])) if len(normalized) > 2 else [query]
        candidates = set()
        for term in queries:
            self._search_results(term)
            time.sleep(.5)
            self.control.observe()
            nodes = self.control.nodes
            # Friend results expose a labelled clickable row and a matching
            # child name. Exclude search fields, navigation and chat previews.
            for row in nodes:
                name = row['description']
                if not row['clickable'] or row['package'] != 'com.kakao.talk' or not name or len(name) > 30 or 'EditText' in row['class'] or 'tab' in row.get('resource', '').lower() or name in {'Friends', 'Chats', 'Search', '친구', '채팅', '검색'}:
                    continue
                x1, y1, x2, y2 = row['bounds']
                if any(n is not row and n['text'] == name and x1 <= n['bounds'][0] <= n['bounds'][2] <= x2 and y1 <= n['bounds'][1] <= n['bounds'][3] <= y2 for n in nodes):
                    candidates.add(name)
        ranked = sorted(candidates, key=lambda name: (-SequenceMatcher(None, normalized, normalize(name)).ratio(), name))
        return {'status': 'candidates_only', 'candidates': [{'name': name, 'exact': same_name(name, query)} for name in ranked[:3]],
                'limitations': 'Names only. No participant membership or latest-room proof. Do not send to an approximate match without owner confirmation.'}

    def send_once(self, recipient: str, text: str, *, request_id=None, room_type='individual', mention=None):
        if not recipient.strip() or not text.strip() or len(text) > 2000:
            raise ValueError('invalid direct message')
        if room_type not in {'individual', 'group'} or (mention and room_type != 'group'):
            raise ValueError('mentions require a group target')
        state = {"room": recipient, "members": None, 'kind': room_type, "until": (datetime.now(timezone.utc) + timedelta(seconds=60)).isoformat()}
        journal = self.path.with_name('kakao-direct-send.json')
        journal.parent.mkdir(parents=True, exist_ok=True)
        lock_path = journal.with_suffix('.lock')
        try:
            fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            raise KakaoSendBlocked('previous direct send locked; outcome uncertain') from None
        os.close(fd)
        try:
            previous = expire_direct_pending(journal)
            if previous.get('status') == 'pending':
                raise KakaoSendBlocked('previous direct send uncertain; owner review required')
            digest = hashlib.sha256(json.dumps([request_id or uuid.uuid4().hex, recipient, text, room_type, mention], ensure_ascii=False).encode()).hexdigest()
            completed = previous.get('completed', [])[-255:]
            if digest in completed:
                raise ValueError('already sent in this owner request; no automatic repeat')
            try:
                if room_type == 'individual' and recipient in {'나', '나 자신', '나와의 채팅', 'self'}:
                    # Self is not indexed as a normal friend in global search.
                    # Prove identity through Kakao's native My Chatroom action.
                    from .kakao_self import KakaoSelf
                    state['room'] = KakaoSelf(self.control.device, self.path.parent).open_self()
                else:
                    try:
                        self.search_chat(state)
                    except ValueError:
                        # Spoken or typed names often add spaces ("반수의 신" for "반수의신").
                        squashed = re.sub(r'\s+', '', recipient)
                        if squashed == recipient:
                            raise
                        state['room'] = squashed
                        self.search_chat(state)
            except ValueError as error:
                raise KakaoRecipientUnavailable('exact recipient unavailable; no message entered') from error
            def record(status):
                temp = journal.with_suffix('.tmp')
                with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as stream:
                    json.dump({'status': status, 'started_at': time.time(), 'request_hash': digest, 'completed': completed + ([digest] if status == 'succeeded' else [])}, stream)
                    stream.flush(); os.fsync(stream.fileno())
                temp.replace(journal)
            record('pending')
            if mention:
                self.send_text(state, text, check_enabled=False, mention=mention)
            else:
                self.send_text(state, text, check_enabled=False)
            record('succeeded')
        finally:
            lock_path.unlink(missing_ok=True)

    def prepare_mention(self, state, text, mention):
        if state['members'] is None or not isinstance(mention, str) or not 1 <= len(mention) <= 40:
            raise ValueError('invalid group mention')
        c = self.control
        read_chat(c.device.dump_hierarchy(), state['room'], state['members'])
        o = c.observe();fields = [n for n in o['nodes'] if n.get('resource') == PREFIX+'message_edit_text']
        if len(fields) != 1 or fields[0].get('text', '').strip():
            raise ValueError('existing or ambiguous message draft')
        field = fields[0]
        c.click(o['snapshot'], field['id'], field.get('text') or field.get('description', ''))
        c.device.device.send_keys('@'+mention, clear=False)
        for _ in range(8):
            time.sleep(.25);o = c.observe()
            candidates = [n for n in o['nodes'] if n.get('resource') == PREFIX+'name_text' and n.get('text') == mention]
            if len(candidates) > 1:
                raise ValueError('ambiguous mention member')
            if len(candidates) == 1:
                n = candidates[0];c.click(o['snapshot'], n['id'], n['text']);break
        else:
            raise ValueError('native mention picker unavailable; no send')
        o = c.observe();fields = [n for n in o['nodes'] if n.get('resource') == PREFIX+'message_edit_text']
        if len(fields) != 1 or not fields[0].get('text', '').startswith('@'+mention):
            raise ValueError('native mention selection not verified')
        prefix = fields[0]['text']
        read_chat(c.device.dump_hierarchy(), state['room'], state['members'])
        c.device.device.send_keys(text, clear=False)  # Preserve native mention spans.
        expected = prefix+text
        o = c.observe();fields = [n for n in o['nodes'] if n.get('resource') == PREFIX+'message_edit_text']
        if len(fields) != 1 or fields[0].get('text') != expected:
            raise ValueError('mentioned draft not verified; no send')
        return expected

    def send_text(self, state, text: str, *, check_enabled=True, mention=None):
        if not text.strip():
            raise ValueError("empty automatic reply")
        def guard():
            if check_enabled and not self.load().get("enabled"):
                raise ValueError("Kakao automatic replies stopped by owner")
            if datetime.now(timezone.utc) >= datetime.fromisoformat(state["until"]):
                raise ValueError("Kakao reply deadline reached")
            return read_chat(self.control.device.dump_hierarchy(), state["room"], state["members"])
        guard()
        observed = self.control.observe()
        fields = [n for n in observed["nodes"] if n.get("resource") == PREFIX + "message_edit_text"]
        if len(fields) != 1:
            raise ValueError("ambiguous Kakao input")
        if fields[0].get('text', '').strip():
            raise ValueError('existing message draft; no overwrite or send')
        if mention:
            text = self.prepare_mention(state, text, mention)
        else:
            self.control.input(observed["snapshot"], fields[0]["id"], text=text)
        guard()
        observed = self.control.observe()
        buttons = [n for n in observed["nodes"] if n.get("description") in ("Send", "전송") or n.get("resource") == PREFIX + "send"]
        if len(buttons) != 1:
            raise ValueError("ambiguous Kakao send button")
        with self._lock:
            before = guard()
            self.control.click(observed["snapshot"], buttons[0]["id"], buttons[0].get("text") or buttons[0].get("description", ""))
        for _ in range(20):
            # Native toolbar can disappear during the post-send redraw. This
            # loop only observes: it never clicks Send again or enters text.
            time.sleep(.25)
            try:
                messages = read_chat(self.control.device.dump_hierarchy(), state["room"], state["members"])
            except ValueError:
                continue
            appended = appended_messages(before, messages) if before else messages
            if any(not m["incoming"] and m["text"] == text for m in appended):
                return
        raise RuntimeError("send result is uncertain; do not retry automatically")


def build_kakao_send_tools(device, data_path, *, request_id=None, notify_candidates=None):
    from .loop import Tool
    watcher = KakaoWatcher(device, Path(data_path) / 'kakao-watch.json')
    trusted_id = request_id or uuid.uuid4().hex
    def send(args):
        try:
            watcher.send_once(args['recipient'], args['text'], request_id=trusted_id, room_type=args.get('room_type', 'individual'), mention=args.get('mention'))
        except KakaoRecipientUnavailable:
            return 'KAKAO_RECIPIENT_REQUIRED'
        except KakaoSendBlocked:
            return 'KAKAO_BLOCKED'
        return 'KAKAO_SENT'
    def find(args):
        result = watcher.find_candidates(args['query'])
        if notify_candidates and result['candidates'] and not any(c['exact'] for c in result['candidates']):
            notify_candidates('카톡 수신자 후보입니다. 아직 전송하지 않았습니다. 정확한 이름으로 다시 요청해 주세요.\n' + '\n'.join(f"{i+1}. {c['name']}" for i, c in enumerate(result['candidates'])))
        return json.dumps(result, ensure_ascii=False)
    return [Tool('kakao_send', 'Search exact individual/group room, verify native title/count, send once and verify outgoing bubble. For group use room_type=group; optional mention selects the actual Kakao member picker and preserves native mention. Never substitute literal @text. Status only.',
        {'type': 'object', 'properties': {'recipient': {'type': 'string', 'minLength': 1, 'maxLength': 30}, 'text': {'type': 'string', 'minLength': 1, 'maxLength': 2000}, 'room_type': {'type': 'string', 'enum': ['individual', 'group']}, 'mention': {'type': 'string', 'minLength': 1, 'maxLength': 40}}, 'required': ['recipient', 'text'], 'additionalProperties': False}, send, needs_approval=True),
        Tool('kakao_find_recipient', 'Read-only native search for exact names or typo candidates. Returns at most 3 names, never sends. Does NOT prove membership or most-recent-room selection. Approximate matches require owner confirmation; a candidate with exact=true (same name ignoring spaces) may be sent to under that exact name.',
        {'type': 'object', 'properties': {'query': {'type': 'string', 'minLength': 1, 'maxLength': 30}}, 'required': ['query'], 'additionalProperties': False}, find)]
