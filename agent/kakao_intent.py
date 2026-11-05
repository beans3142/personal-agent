"""Literal owner-authored group sends, without model-generated arguments."""
import re


def group_send_intent(text):
    match = re.fullmatch(
        r'\s*(?:카톡|카카오톡)(?:으로)?\s*(?P<room>.+?)\s*방'
        r'(?:이\s*있음[.!]?\s*거기에|에서|에)\s*'
        r'@(?P<mention>[^\s@]+)\s+(?:[을를]\s*)?(?:태그하고|멘션하고)\s*'
        r'(?P<body>.+?)\s*(?:보내줘|전송해줘)\s*[.!]?\s*', text, re.S)
    if not match:
        return None
    room, mention, body = (match[k].strip() for k in ('room', 'mention', 'body'))
    if len(body) > 2000 or not 1 <= len(room) <= 30 or not 1 <= len(mention) <= 40 or '\n' in room:
        return None
    if len(body) > 1 and (body[0], body[-1]) in {('"','"'), ('“','”')}:
        body = body[1:-1]
    if not body.strip():
        return None
    return {'recipient': room, 'text': body, 'room_type': 'group', 'mention': mention}
