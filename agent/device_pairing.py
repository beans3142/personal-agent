"""Connecting a new Daevis app (PC program or Galaxy app) without codes or chat commands.

1. The new app posts /pair/start and shows a 4-digit code while it waits.
2. The owner accepts it on an already connected Daevis app (approval card) or by
   telling Daevis "연결 수락해" (main agent tool).
3. The new app's /pair/poll then receives its registration once.
Requests expire after WAIT seconds; at most MAX_WAITING wait at once.
"""
import re
import secrets

WAIT = 600
MAX_WAITING = 3
KINDS = ('pc', 'phone')


def _name(name):
    return re.sub(r'[^0-9A-Za-z가-힣 _-]', '', str(name or '')).strip()[:24]


def _table(db):
    db.execute('CREATE TABLE IF NOT EXISTS device_pairings(id TEXT PRIMARY KEY,kind TEXT,code TEXT,name TEXT,expires REAL,invite TEXT)')


def start(registry, kind, name):
    """Anonymous: open a waiting request."""
    if kind not in KINDS:
        raise ValueError('unknown device kind')
    now = registry.clock()
    with registry._db() as db:
        _table(db)
        db.execute('BEGIN IMMEDIATE')
        db.execute('DELETE FROM device_pairings WHERE expires<=?', (now,))
        if db.execute('SELECT count(*) FROM device_pairings WHERE invite IS NULL').fetchone()[0] >= MAX_WAITING:
            raise ValueError('too many waiting requests')
        pid, code = secrets.token_hex(16), f'{secrets.randbelow(10000):04d}'
        db.execute('INSERT INTO device_pairings VALUES(?,?,?,?,?,NULL)',
                   (pid, kind, code, _name(name) or ('폰' if kind == 'phone' else 'PC'), now + WAIT))
    return {'id': pid, 'code': code, 'expires_in': WAIT}


def pending(registry):
    with registry._db() as db:
        _table(db)
        rows = db.execute('SELECT kind,code,name,expires FROM device_pairings WHERE invite IS NULL AND expires>? ORDER BY expires',
                          (registry.clock(),)).fetchall()
    return [{'kind': r['kind'], 'code': r['code'], 'name': r['name'], 'expires_in': int(r['expires'] - registry.clock())} for r in rows]


def _waiting(registry, code):
    with registry._db() as db:
        _table(db)
        rows = db.execute('SELECT * FROM device_pairings WHERE invite IS NULL AND expires>?', (registry.clock(),)).fetchall()
    rows = [dict(r) for r in rows if code is None or r['code'] == str(code).strip()]
    if not rows:
        raise ValueError('no waiting device request')
    if len(rows) > 1:
        raise ValueError('several device requests are waiting; give the code shown on the new device')
    return rows[0]


def _replace(registry, kind, label):
    """The same device connecting again (reinstall, cleared data) replaces its old registration."""
    table = 'phones' if kind == 'phone' else 'pcs'
    with registry._db() as db:
        old = db.execute(f'SELECT alias,node FROM {table} WHERE alias=?', (registry.alias(label),)).fetchone()
    if old:
        registry.revoke(old['node'])
        with registry._db() as db:
            db.execute(f'DELETE FROM {table} WHERE alias=?', (old['alias'],))


def accept(registry, code=None):
    """Owner side: accept the one waiting request, or the one with this code."""
    row = _waiting(registry, code)
    _replace(registry, row['kind'], row['name'])
    invite = registry.invite(row['name'], kind=row['kind'])
    with registry._db() as db:
        db.execute('UPDATE device_pairings SET invite=? WHERE id=?', (invite, row['id']))
    return {'kind': row['kind'], 'name': row['name'], 'code': row['code']}


def deny(registry, code=None):
    row = _waiting(registry, code)
    with registry._db() as db:
        db.execute('DELETE FROM device_pairings WHERE id=?', (row['id'],))
    return {'kind': row['kind'], 'name': row['name'], 'code': row['code']}


def poll(registry, pid):
    """Anonymous with the secret request id: pending, expired/unknown, or the registration (handed out once)."""
    if not isinstance(pid, str) or not re.fullmatch(r'[0-9a-f]{32}', pid):
        return {'status': 'unknown'}
    with registry._db() as db:
        _table(db)
        row = db.execute('SELECT * FROM device_pairings WHERE id=?', (pid,)).fetchone()
        if not row:
            return {'status': 'unknown'}
        if row['invite'] is None:
            if row['expires'] <= registry.clock():
                db.execute('DELETE FROM device_pairings WHERE id=?', (pid,))
                return {'status': 'expired'}
            return {'status': 'pending'}
        db.execute('DELETE FROM device_pairings WHERE id=?', (pid,))
        invite = row['invite']
    return {'status': 'approved', **registry.redeem(invite)}


def owner_node(registry, node, token):
    """Callers allowed to see and answer requests: any connected Daevis app or the main agent."""
    if not isinstance(node, str) or not (node == 'agent-main' or re.fullmatch(r'(?:pc|phone)-[0-9a-f]{16}', node)):
        raise PermissionError('not an owner device')
    if not registry.authenticate(node, token):
        raise PermissionError('unauthorized')


def handle(registry, path, body, token):
    """HTTP body -> reply for the /pair/* endpoints."""
    if path == '/pair/start' and set(body) == {'kind', 'name'}:
        return start(registry, body['kind'], body['name'])
    if path == '/pair/poll' and set(body) == {'id'}:
        return poll(registry, body['id'])
    if path in ('/pair/pending', '/pair/accept', '/pair/deny') and set(body) in ({'node'}, {'node', 'code'}):
        owner_node(registry, body['node'], token)
        if path == '/pair/pending':
            return {'requests': pending(registry)}
        return (accept if path == '/pair/accept' else deny)(registry, body.get('code'))
    raise ValueError('invalid pairing request')


def build_pairing_tools(post, node='agent-main'):
    """Main-agent tools: the owner says "연결 수락해" / "거절해" to Daevis."""
    import json
    from .loop import Tool

    def call(path, code=None):
        try:
            reply = post(path, {'node': node, **({'code': str(code).strip()} if code else {})})
        except Exception as error:
            return f'error: 연결 요청을 처리하지 못했어요 ({type(error).__name__}). 요청이 없거나 이미 끝났을 수 있어요.'
        return json.dumps(reply, ensure_ascii=False)

    code = {'type': 'object', 'properties': {'code': {'type': 'string', 'description': '새 기기 화면에 뜬 4자리 번호. 요청이 하나면 생략'}},
            'additionalProperties': False}
    return [
        Tool('device_requests', '새 대비스 기기(PC 프로그램·폰 앱)의 연결 요청 목록.', {'type': 'object', 'properties': {}, 'additionalProperties': False},
             lambda args: call('/pair/pending')),
        Tool('device_accept', '새 대비스 기기의 연결 요청을 수락한다. "연결 수락해", "폰 연결 승인해"에 쓴다. 수락한 기기 이름을 답한다.', code,
             lambda args: call('/pair/accept', args.get('code'))),
        Tool('device_deny', '새 대비스 기기의 연결 요청을 거절한다.', code, lambda args: call('/pair/deny', args.get('code'))),
    ]
