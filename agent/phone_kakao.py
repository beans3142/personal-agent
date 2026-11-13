"""KakaoTalk 1:1 send on the owner's phone, run by code (no model steps).

Search the exact name, open that 1:1 chat, check the chat title, type, send and
check the new bubble. Like the server Kakao tools, an unclear outcome is reported
as KAKAO_UNCERTAIN and never retried automatically.
"""
import json
import re
import time

from .loop import Tool

SEND_LABELS = {'Send', '전송', '보내기'}
SEARCH_LABELS = {'Search', '검색'}
ONE_ON_ONE = {'1:1 Chat', '1:1 채팅'}


def _nodes(result):
    return (result.get('screen') or result).get('nodes') or []


def _find(nodes, pred):
    return [n for n in nodes if pred(n)]


def _settled(phone, screen, sleep):
    """An app that just opened may not have drawn yet; read again until something is there."""
    for _ in range(4):
        if _nodes(screen):
            return screen
        sleep(1)
        screen = phone('screen', {})
    return screen


def _open_search(phone, sleep=time.sleep):
    screen = _settled(phone, phone('open', {'app': 'com.kakao.talk'}), sleep)
    for _ in range(4):
        search = _find(_nodes(screen), lambda n: n.get('desc') in SEARCH_LABELS and n.get('c'))
        if search:
            return phone('tap', {'i': search[0]['i']})
        screen = _settled(phone, phone('key', {'key': 'back'}), sleep)
        if not _find(_nodes(screen), lambda n: n.get('desc') in SEARCH_LABELS):
            screen = _settled(phone, phone('open', {'app': 'com.kakao.talk'}), sleep)
    return None


def _pick(phone, name, sleep):
    for _ in range(5):
        sleep(1)  # Kakao updates results a moment after typing.
        nodes = _nodes(phone('screen', {}))
        rooms = _find(nodes, lambda n: n.get('c') and (n.get('text') or '').startswith(name + ' ')
                      and not re.match(re.escape(name) + r' \d+ ', n.get('text') or ''))
        friends = _find(nodes, lambda n: n.get('c') and n.get('desc') == name)
        if len(rooms) > 1 or (not rooms and len(friends) > 1):
            return 'several'
        if rooms or friends:
            return (rooms or friends)[0]
    return None


def _open_chat(phone, pick, name, sleep):
    screen = phone('tap', {'i': pick['i']})
    for _ in range(3):
        nodes = _nodes(screen)
        if _find(nodes, lambda n: n.get('id') == 'toolbar_default_title_text' and name in (n.get('text'), n.get('desc'))):
            return nodes
        chat = _find(nodes, lambda n: n.get('c') and (n.get('text') in ONE_ON_ONE or n.get('desc') in ONE_ON_ONE))
        if chat:
            screen = phone('tap', {'i': chat[0]['i']})
        else:
            sleep(1)
            screen = phone('screen', {})
    return None


def kakao_send(phone, recipient, text, dry_run=False, sleep=time.sleep):
    name = recipient.strip()
    screen = _open_search(phone, sleep)
    if screen is None:
        return {'code': 'KAKAO_NOT_SENT', 'reason': 'search button not found'}
    field = _find(_nodes(screen), lambda n: n.get('e') and n.get('id') not in ('input_focus', 'message_edit_text'))
    for _ in range(4):
        if field:
            break
        sleep(1)
        field = _find(_nodes(phone('screen', {})), lambda n: n.get('e') and n.get('id') not in ('input_focus', 'message_edit_text'))
    if len(field) != 1:
        return {'code': 'KAKAO_NOT_SENT', 'reason': 'search field not found'}
    phone('type', {'i': field[0]['i'], 'text': name})
    pick = _pick(phone, name, sleep)
    if pick is None or pick == 'several':
        return {'code': 'KAKAO_RECIPIENT_REQUIRED', 'reason': 'no exact match' if pick is None else 'several matches'}
    nodes = _open_chat(phone, pick, name, sleep)
    if nodes is None:
        return {'code': 'KAKAO_NOT_SENT', 'reason': 'chat title not verified'}
    box = _find(nodes, lambda n: n.get('id') == 'message_edit_text')
    if len(box) != 1:
        return {'code': 'KAKAO_NOT_SENT', 'reason': 'message box not found'}
    if dry_run:
        return {'code': 'KAKAO_CHECKED', 'reason': 'chat opened and verified; nothing sent'}
    before = len(_find(nodes, lambda n: n.get('id') == 'message' and n.get('text') == text))
    # Kakao only shows Send for a box that was tapped (focused) before the text arrives.
    nodes = _nodes(phone('tap', {'i': box[0]['i']}))
    box = _find(nodes, lambda n: n.get('id') == 'message_edit_text') or box
    nodes = _nodes(phone('type', {'i': box[0]['i'], 'text': text}))
    send = _find(nodes, lambda n: n.get('c') and (n.get('desc') in SEND_LABELS or n.get('id') == 'send'))
    if len(send) != 1:
        return {'code': 'KAKAO_NOT_SENT', 'reason': 'send button not found'}
    try:
        nodes = _nodes(phone('tap', {'i': send[0]['i']}))
    except Exception:
        return {'code': 'KAKAO_UNCERTAIN', 'reason': 'send was tapped but no result came back; check the chat before retrying'}
    for _ in range(3):
        if len(_find(nodes, lambda n: n.get('id') == 'message' and n.get('text') == text)) > before:
            return {'code': 'KAKAO_SENT', 'recipient': name}
        sleep(1)
        nodes = _nodes(phone('screen', {}))
    return {'code': 'KAKAO_UNCERTAIN', 'reason': 'sent bubble not seen; check the chat before retrying'}


def build_phone_kakao_tool(phone):
    def run(args):
        recipient, text = str(args.get('recipient', '')).strip(), str(args.get('text', ''))
        if not recipient or not text.strip() or len(text) > 1000:
            return 'error: recipient와 text(1~1000자)가 필요해요'
        try:
            return json.dumps(kakao_send(phone, recipient, text, bool(args.get('dry_run'))), ensure_ascii=False)
        except Exception as error:
            return json.dumps({'code': 'KAKAO_UNCERTAIN', 'reason': str(error)}, ensure_ascii=False)
    return Tool('phone_kakao_send', '소유자 폰의 카카오톡으로 1:1 메시지를 보낸다. recipient는 카톡에 보이는 정확한 이름. '
                                   '"내 폰으로 카톡 보내", "폰 카톡으로" 요청에 쓴다. 결과 code를 그대로 답한다(KAKAO_SENT, KAKAO_NOT_SENT, '
                                   'KAKAO_RECIPIENT_REQUIRED, KAKAO_UNCERTAIN). KAKAO_UNCERTAIN이면 다시 보내지 않는다.',
                {'type': 'object', 'properties': {'recipient': {'type': 'string'}, 'text': {'type': 'string'},
                                                  'dry_run': {'type': 'boolean'}},
                 'required': ['recipient', 'text'], 'additionalProperties': False}, run)
