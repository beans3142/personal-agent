"""Owner-enrolled outbound nodes. No listener is started on import.

Run owner CLI with ``python -m agent.device_nodes --help``. Defaults to loopback;
an operator may select an RFC1918 IPv4 address for a trusted private proxy hop.
Remote nodes must use HTTPS at the reverse proxy; restrict private-hop ingress.
Delivery is at most once: lost dispatch/ack becomes uncertain, never replayed.
"""
from __future__ import annotations

import argparse
from contextlib import contextmanager
import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from agent.loop import Tool

ACTIONS = frozenset({'phone_alarm', 'wol', 'text_message'})


def validate_bind_address(value):
    """Accept IPv4 loopback or explicit RFC1918 ranges, never wildcard/public."""
    address = ipaddress.IPv4Address(value)
    rfc1918 = ('10.0.0.0/8', '172.16.0.0/12', '192.168.0.0/16')
    if not address.is_loopback and not any(address in ipaddress.IPv4Network(network) for network in rfc1918):
        raise ValueError('bind must be IPv4 loopback or RFC1918 private address')
    return str(address)

class DeviceStore:
    def __init__(self, path, clock=time.time):
        self.path, self.clock = Path(path), clock
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._db() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS nodes (
                  name TEXT PRIMARY KEY, digest TEXT, capabilities TEXT, seen REAL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS jobs (
                  id TEXT PRIMARY KEY, node TEXT, action TEXT, payload TEXT,
                  request_key TEXT, created REAL, expires REAL, status TEXT,
                  UNIQUE(node, request_key));
                CREATE TABLE IF NOT EXISTS controls (
                  source TEXT, target TEXT, action TEXT, PRIMARY KEY(source,target,action));
            ''')
            columns = {row['name'] for row in db.execute('PRAGMA table_info(jobs)')}
            for name in ('issuer', 'issuer_key'):
                if name not in columns:
                    db.execute(f'ALTER TABLE jobs ADD COLUMN {name} TEXT')
            db.execute('CREATE UNIQUE INDEX IF NOT EXISTS job_issuer_key ON jobs(issuer,issuer_key) WHERE issuer IS NOT NULL')
        os.chmod(self.path, 0o600)

    @contextmanager
    def _db(self):
        db = sqlite3.connect(self.path, timeout=10)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    def enroll(self, name, capabilities):
        if not re.fullmatch(r'[a-zA-Z0-9_-]{1,48}', name):
            raise ValueError('invalid node name')
        if not isinstance(capabilities, list) or not set(capabilities) <= ACTIONS:
            raise ValueError('unsupported capabilities')
        token = secrets.token_urlsafe(32)
        with self._db() as db:
            try:
                db.execute('INSERT INTO nodes(name,digest,capabilities) VALUES(?,?,?)',
                           (name, hashlib.sha256(token.encode()).hexdigest(), json.dumps(sorted(set(capabilities)))))
            except sqlite3.IntegrityError:
                raise ValueError('node already enrolled; use a new name after revocation') from None
        return token  # Only owner-local enrollment CLI receives this value.

    def authenticate(self, name, token):
        if not isinstance(token, str) or len(token) > 256:
            return False
        with self._db() as db:
            row = db.execute('SELECT digest FROM nodes WHERE name=?', (name,)).fetchone()
        expected = row['digest'] if row and row['digest'] else '0' * 64
        matched = hmac.compare_digest(expected, hashlib.sha256(token.encode()).hexdigest())
        return bool(row and row['digest'] and matched)

    def _auth(self, name, token):
        if not self.authenticate(name, token):
            raise PermissionError('unauthorized node')

    def revoke(self, name):
        with self._db() as db:
            db.execute('UPDATE nodes SET digest=NULL WHERE name=?', (name,))
            db.execute("UPDATE jobs SET status='cancelled' WHERE node=? AND status='queued'", (name,))
            db.execute("UPDATE jobs SET status='cancelled' WHERE issuer=? AND status='queued'", (name,))
            db.execute('DELETE FROM controls WHERE source=? OR target=?', (name, name))

    def grant(self, source, target, action):
        """Owner-local provisioning only; no network or model tool grants access."""
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            source_row = db.execute('SELECT digest FROM nodes WHERE name=?', (source,)).fetchone()
            target_row = db.execute('SELECT digest,capabilities FROM nodes WHERE name=?', (target,)).fetchone()
            if not source_row or not source_row['digest'] or not target_row or not target_row['digest'] or action not in ACTIONS or action not in json.loads(target_row['capabilities']):
                raise ValueError('source/target must be enrolled and target must support action')
            db.execute('INSERT OR IGNORE INTO controls VALUES(?,?,?)', (source, target, action))

    def ungrant(self, source, target, action):
        with self._db() as db:
            db.execute('DELETE FROM controls WHERE source=? AND target=? AND action=?', (source, target, action))
            db.execute("UPDATE jobs SET status='cancelled' WHERE issuer=? AND node=? AND action=? AND status='queued'", (source, target, action))

    def request(self, source, token, target, action, payload, request_key, ttl_seconds=120):
        return self._enqueue(target, action, payload, request_key, ttl_seconds, source, token)

    def request_status(self, source, token, job_id):
        self._auth(source, token)
        with self._db() as db:
            self._expire(db)
            row = db.execute('SELECT id,node,action,status,expires FROM jobs WHERE id=? AND issuer=?', (job_id, source)).fetchone()
            if not row:
                raise PermissionError('receipt not available to this issuer')
            return dict(row)

    def _expire(self, db):
        now = self.clock()
        db.execute("UPDATE jobs SET status='expired' WHERE status='queued' AND expires<=?", (now,))
        db.execute("UPDATE jobs SET status='uncertain' WHERE status='dispatched' AND expires<=?", (now,))

    def list_nodes(self):
        with self._db() as db:
            rows = db.execute('SELECT name,capabilities,seen,digest IS NOT NULL AS enabled FROM nodes ORDER BY name').fetchall()
        return [dict(name=r['name'], capabilities=json.loads(r['capabilities']), last_seen=r['seen'] or None,
                     online=bool(r['enabled'] and r['seen'] and self.clock() - r['seen'] <= 90),
                     enabled=bool(r['enabled'])) for r in rows]

    def list_jobs(self):
        with self._db() as db:
            self._expire(db)
            rows = db.execute('SELECT id,node,action,created,expires,status FROM jobs ORDER BY created DESC,rowid DESC LIMIT 100').fetchall()
        return [dict(r) for r in rows]

    def enqueue(self, node, action, payload, request_key, ttl_seconds=120):
        return self._enqueue(node, action, payload, request_key, ttl_seconds)

    def _enqueue(self, node, action, payload, request_key, ttl_seconds, issuer=None, token=None):
        if not isinstance(payload, dict) or action not in ACTIONS:
            raise ValueError('unsupported action')
        if action in {'phone_alarm', 'text_message'}:
            if set(payload) != {'text'} or not isinstance(payload['text'], str) or not 1 <= len(payload['text']) <= 240:
                raise ValueError('message requires text (1..240 chars) only')
        elif payload:
            raise ValueError('wol uses only the node-local configured target')
        if not isinstance(request_key, str) or not re.fullmatch(r'[A-Za-z0-9_.:-]{1,80}', request_key):
            raise ValueError('invalid request key')
        if type(ttl_seconds) is not int or not 1 <= ttl_seconds <= 300:
            raise ValueError('ttl_seconds must be 1..300')
        encoded = json.dumps(payload, sort_keys=True)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            self._expire(db)
            if issuer is not None:
                source = db.execute('SELECT digest FROM nodes WHERE name=?', (issuer,)).fetchone()
                expected = source['digest'] if source and source['digest'] else '0' * 64
                if not isinstance(token, str) or not source or not source['digest'] or not hmac.compare_digest(expected, hashlib.sha256(token.encode()).hexdigest()):
                    raise PermissionError('unauthorized source')
                if not db.execute('SELECT 1 FROM controls WHERE source=? AND target=? AND action=?', (issuer, node, action)).fetchone():
                    raise PermissionError('source is not granted this target/action')
            row = db.execute('SELECT digest,capabilities FROM nodes WHERE name=?', (node,)).fetchone()
            if not row or not row['digest'] or action not in json.loads(row['capabilities']):
                raise ValueError('node unavailable or action not enrolled')
            if issuer is not None:
                existing = db.execute('SELECT * FROM jobs WHERE issuer=? AND issuer_key=?', (issuer, request_key)).fetchone()
            else:
                existing = db.execute('SELECT * FROM jobs WHERE node=? AND request_key=?', (node, request_key)).fetchone()
            if existing:
                if existing['node'] != node or existing['issuer'] != issuer or existing['action'] != action or existing['payload'] != encoded:
                    raise ValueError('request key already used for different action')
                return {'id': existing['id'], 'status': existing['status'], 'deduplicated': True}
            if db.execute("SELECT count(*) FROM jobs WHERE status IN ('queued','dispatched')").fetchone()[0] >= 32:
                raise ValueError('queue full')
            # Retain dedup keys; fail closed rather than prune and permit replay.
            if db.execute('SELECT count(*) FROM jobs').fetchone()[0] >= 1000:
                raise ValueError('history full; owner must archive database before further requests')
            job_id = secrets.token_hex(16)
            now = self.clock()
            # Node-originated keys have a separate issuer-scoped unique index.
            # NULL avoids collisions with owner tools' (node,request_key) keys.
            db.execute('INSERT INTO jobs(id,node,action,payload,request_key,created,expires,status,issuer,issuer_key) VALUES(?,?,?,?,?,?,?,?,?,?)',
                       (job_id, node, action, encoded, request_key if issuer is None else None,
                        now, now + ttl_seconds, 'queued', issuer, request_key if issuer is not None else None))
        return {'id': job_id, 'status': 'queued', 'deduplicated': False}

    def poll(self, node, token):
        self._auth(node, token)
        with self._db() as db:
            db.execute('BEGIN IMMEDIATE')
            if not db.execute('SELECT digest FROM nodes WHERE name=?', (node,)).fetchone()['digest']:
                raise PermissionError('revoked node')
            self._expire(db)
            db.execute('UPDATE nodes SET seen=? WHERE name=?', (self.clock(), node))
            row = db.execute("SELECT * FROM jobs WHERE node=? AND status='queued' ORDER BY created,rowid LIMIT 1", (node,)).fetchone()
            if row:
                db.execute("UPDATE jobs SET status='dispatched' WHERE id=?", (row['id'],))
                return dict(id=row['id'], source=row['issuer'] or 'owner', action=row['action'],
                            payload=json.loads(row['payload']), expires=row['expires'])
        return None

    def ack(self, node, token, job_id, status):
        self._auth(node, token)
        if status not in {'succeeded', 'failed', 'uncertain', 'expired'}:
            raise ValueError('invalid result status')
        with self._db() as db:
            row = db.execute('SELECT status FROM jobs WHERE id=? AND node=?', (job_id, node)).fetchone()
            if not row or row['status'] not in {'dispatched', 'uncertain', status}:
                raise ValueError('invalid acknowledgement')
            db.execute('UPDATE jobs SET status=? WHERE id=? AND node=?', (status, job_id, node))


def build_device_tools(data_path, env=None):
    store = DeviceStore(Path(data_path) / 'device-nodes.sqlite3')
    def schema(properties, required=()):
        return {'type': 'object', 'properties': properties, 'required': list(required), 'additionalProperties': False}
    return [
        Tool('device_nodes', 'List enrolled devices and recent heartbeat availability. Offline nodes are not connected.', schema({}), lambda a: json.dumps(store.list_nodes())),
        Tool('device_jobs', 'List recent action delivery/results. succeeded means node command completed, not hardware verification.', schema({}), lambda a: json.dumps(store.list_jobs())),
        Tool('device_enqueue', 'Queue terminal text message, immediate phone sound notification or configured LAN WoL packet. Approval required. A queued action is not executed success. Use stable request_key for retries.',
             schema({'node': {'type': 'string'}, 'action': {'type': 'string', 'enum': sorted(ACTIONS)},
                     'payload': {'type': 'object'}, 'request_key': {'type': 'string'},
                     'ttl_seconds': {'type': 'integer', 'minimum': 1, 'maximum': 300}},
                    ('node', 'action', 'payload', 'request_key')),
             lambda a: json.dumps(store.enqueue(**a)), needs_approval=True),
    ]


def handler_for(store):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Do not log credentials or alarm text.

        def do_POST(self):
            self.connection.settimeout(10)
            try:
                length = int(self.headers.get('Content-Length', '0'))
                if not 0 < length <= 4096:
                    raise ValueError('invalid length')
                body = json.loads(self.rfile.read(length))
                if not isinstance(body, dict):
                    raise ValueError('invalid object')
                authorization = self.headers.get('Authorization', '')
                if not authorization.startswith('Bearer '):
                    raise PermissionError('missing bearer authorization')
                token = authorization[7:]
                if self.path == '/poll' and set(body) == {'node'}:
                    result = {'job': store.poll(body['node'], token)}
                elif self.path == '/ack' and set(body) == {'node', 'id', 'status'}:
                    store.ack(body['node'], token, body['id'], body['status'])
                    result = {'ok': True}
                elif self.path == '/request' and set(body) in ({'node', 'target', 'action', 'payload', 'request_key'}, {'node', 'target', 'action', 'payload', 'request_key', 'ttl_seconds'}):
                    result = store.request(body['node'], token, body['target'], body['action'], body['payload'], body['request_key'], body.get('ttl_seconds', 120))
                elif self.path == '/request-status' and set(body) == {'node', 'id'}:
                    result = store.request_status(body['node'], token, body['id'])
                else:
                    raise ValueError('invalid request')
                code = 200
            except PermissionError:
                code, result = 401, {'error': 'unauthorized'}
            except (ValueError, TypeError, KeyError, OverflowError):
                code, result = 400, {'error': 'invalid request'}
            except Exception:
                code, result = 500, {'error': 'internal failure'}
            data = json.dumps(result).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', default='data')
    sub = parser.add_subparsers(dest='command', required=True)
    enroll = sub.add_parser('enroll')
    enroll.add_argument('name')
    mode = enroll.add_mutually_exclusive_group(required=True)
    mode.add_argument('--capability', action='append', choices=sorted(ACTIONS))
    mode.add_argument('--heartbeat-only', action='store_true', help='No device actions; availability QA only')
    enroll.add_argument('--token-file', required=True, help='New private file; token never printed')
    revoke = sub.add_parser('revoke')
    revoke.add_argument('name')
    for command in ('grant', 'ungrant'):
        acl = sub.add_parser(command)
        acl.add_argument('source')
        acl.add_argument('target')
        acl.add_argument('--action', required=True, choices=sorted(ACTIONS))
    serve = sub.add_parser('serve')
    serve.add_argument('--port', type=int, default=8791)
    serve.add_argument('--bind', type=validate_bind_address, default='127.0.0.1',
                       help='IPv4 loopback or explicit RFC1918 address; protect private hop with network ingress rules')
    args = parser.parse_args()
    store = DeviceStore(Path(args.data) / 'device-nodes.sqlite3')
    if args.command == 'enroll':
        # Exclusive private file: do not overwrite an existing credential.
        fd = os.open(args.token_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        try:
            with os.fdopen(fd, 'w') as stream:
                token = store.enroll(args.name, args.capability or [])
                stream.write(token + '\n')
        except Exception:
            Path(args.token_file).unlink(missing_ok=True)
            raise
        print('Enrolled; transfer the private token file directly to the node.')
    elif args.command == 'revoke':
        store.revoke(args.name)
        print('Revoked.')
    elif args.command in {'grant', 'ungrant'}:
        getattr(store, args.command)(args.source, args.target, args.action)
        print('Control grant updated.')
    else:
        ThreadingHTTPServer((args.bind, args.port), handler_for(store)).serve_forever()


if __name__ == '__main__':
    main()
