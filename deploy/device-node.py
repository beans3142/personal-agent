#!/usr/bin/env python3
"""Outbound node example; Python stdlib only. Never installed automatically.

Config JSON (private, chmod 600):
{"url":"https://game.daehoon.games/daevis", "node":"phone",
 "token_file":"/absolute/private/node-token", "actions":["phone_alarm"]}
Use actions:[] and server enrollment --heartbeat-only for connection-only QA.
For WoL add actions:["wol"], wol_mac and wol_broadcast (LAN IPv4 address).
Run: python device-node.py --config /absolute/private/node.json
Bidirectional terminal message (requires server owner grant source->target):
  python device-node.py --config node.json --send --target phone
    --action text_message --payload-file message.json --request-key my-message-1
message.json contains {"text":"hello"}; use the same key when retrying.
Read receipt: python device-node.py --config node.json --status-id RECEIPT_ID
text_message prints one JSON line in the receiving terminal/service journal;
it does not invoke a notification or guarantee that a person saw the message.
Phone requires Termux + Termux:API app and termux-api package with notification
permission. phone_alarm is an immediate sound notification, NOT a clock alarm.
Android Doze/notification settings may prevent timely/audible delivery.
WoL node must remain on in the target LAN; packet send does not prove PC boot.
Offline retries back off from 15 seconds to 5 minutes; reconnect can take up
to 5 minutes after server startup. HTTP 401/403 stops until owner review.
"""
import argparse
import ipaddress
import json
import os
from pathlib import Path
import re
import shutil
import socket
import sqlite3
import subprocess
import time
import urllib.error
import urllib.request
from urllib.parse import urlsplit


def validate_base_url(value):
    """CLI boundary: HTTPS origin, optionally the exact /daevis proxy prefix."""
    if not isinstance(value, str) or any(char.isspace() or ord(char) < 32 or ord(char) == 127 for char in value) or any(char in value for char in '\\?#'):
        raise ValueError('invalid node base URL')
    parsed = urlsplit(value)
    if (parsed.scheme != 'https' or not parsed.hostname or parsed.username is not None or
            parsed.password is not None or parsed.path not in {'', '/', '/daevis', '/daevis/'}):
        raise ValueError('node URL requires HTTPS and optional exact /daevis path')
    # Accessing port also rejects malformed or out-of-range port syntax.
    parsed.port
    return value.rstrip('/')


def magic_packet(mac):
    if not isinstance(mac, str) or not re.fullmatch(r'(?:[0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}', mac):
        raise ValueError('invalid configured MAC')
    return b'\xff' * 6 + bytes.fromhex(mac.replace(':', '')) * 16


def execute_action(job, config):
    action, payload = job['action'], job['payload']
    if action not in config.get('actions', []):
        raise ValueError('action not locally allowed')
    if action == 'text_message':
        if not isinstance(payload, dict) or set(payload) != {'text'} or not isinstance(payload['text'], str) or not 1 <= len(payload['text']) <= 240:
            raise ValueError('invalid terminal message')
        # JSON escapes terminal control characters, including escape/newlines.
        print(json.dumps({'type': 'text_message', 'id': job.get('id'), 'source': job.get('source'),
                          'text': payload['text']}, ensure_ascii=False), flush=True)
    elif action == 'phone_alarm':
        if set(payload) != {'text'} or not isinstance(payload['text'], str) or not 1 <= len(payload['text']) <= 240:
            raise ValueError('invalid notification')
        executable = shutil.which('termux-notification')
        if not executable:
            raise ValueError('Termux:API is not installed')
        # No action/button/intent or shell argument is accepted from the server.
        subprocess.run([executable, '--id', '7301', '--title', 'Personal agent',
                        '--content', payload['text'], '--sound', '--priority', 'high'],
                       check=True, timeout=20, stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    elif action == 'wol':
        if payload:
            raise ValueError('remote WoL targets are not accepted')
        packet = magic_packet(config.get('wol_mac'))
        broadcast = str(ipaddress.IPv4Address(config['wol_broadcast']))
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
            sock.settimeout(5)
            sock.sendto(packet, (broadcast, 9))
    else:
        raise ValueError('unsupported action')


def post(config, endpoint, body):
    token = Path(config['token_file']).read_text().strip()
    request = urllib.request.Request(config['url'].rstrip('/') + endpoint,
                                    data=json.dumps(body).encode(), method='POST',
                                    headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
    # Disable redirects: bearer credentials must never follow a redirect.
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    with urllib.request.build_opener(NoRedirect).open(request, timeout=25) as response:
        data = response.read(8193)
        if len(data) > 8192:
            raise ValueError('oversized response')
        return json.loads(data)


def send_request(config, target, action, payload, request_key, ttl_seconds=120):
    """Source identity comes from configured node + its bearer; never payload."""
    return post(config, '/request', {'node': config['node'], 'target': target,
                                   'action': action, 'payload': payload,
                                   'request_key': request_key, 'ttl_seconds': ttl_seconds})


def request_status(config, job_id):
    return post(config, '/request-status', {'node': config['node'], 'id': job_id})


def run_once(config, state):
    """Journal before action; never replay a command after a crash/ack loss."""
    # First retry result delivery only, never re-execute commands.
    for job_id, status in state.execute('SELECT id,status FROM journal WHERE acknowledged=0').fetchall():
        if status == 'running':
            status = 'uncertain'
        post(config, '/ack', {'node': config['node'], 'id': job_id, 'status': status})
        with state:
            state.execute('UPDATE journal SET status=?,acknowledged=1 WHERE id=?', (status, job_id))
    job = post(config, '/poll', {'node': config['node']})['job']
    if job is None:
        return
    with state:
        found = state.execute('SELECT id FROM journal WHERE id=?', (job['id'],)).fetchone()
        if found:
            return
        state.execute('INSERT INTO journal VALUES(?,?,0)', (job['id'], 'running'))
    if job['expires'] <= time.time():
        status = 'expired'
    else:
        try:
            execute_action(job, config)
            status = 'succeeded'
        except subprocess.TimeoutExpired:
            status = 'uncertain'
        except (ValueError, KeyError, OSError, subprocess.SubprocessError):
            status = 'failed'
    with state:
        state.execute('UPDATE journal SET status=? WHERE id=?', (status, job['id']))
    post(config, '/ack', {'node': config['node'], 'id': job['id'], 'status': status})
    with state:
        state.execute('UPDATE journal SET acknowledged=1 WHERE id=?', (job['id'],))


class PollBackoff:
    """Bound retries while the on-demand server is off; never log secrets."""
    delays = (15, 30, 60, 120, 300)

    def __init__(self):
        self.failures = 0

    def delay(self, error=None):
        if error is None:
            self.failures = 0
            return self.delays[0]
        if isinstance(error, urllib.error.HTTPError):
            if error.code in (401, 403):
                print('Node authorization rejected; stopped. Ask the owner to review enrollment.', flush=True)
                # Successful exit prevents Restart=on-failure from retrying a
                # revoked credential forever in the supplied service example.
                raise SystemExit(0)
            if 400 <= error.code < 500:
                print('Node request rejected; stopped for owner configuration review.', flush=True)
                raise SystemExit(0)
        delay = self.delays[min(self.failures, len(self.delays) - 1)]
        self.failures += 1
        return delay


def poll_forever(config, state, sleep=time.sleep, attempt=run_once):
    backoff = PollBackoff()
    while True:
        try:
            # Fail closed at a bounded journal size. Owner archives/re-enrolls.
            if state.execute('SELECT count(*) FROM journal').fetchone()[0] >= 1000:
                raise SystemExit('Node journal full; owner maintenance required.')
            attempt(config, state)
        except (OSError, ValueError, KeyError) as error:
            delay = backoff.delay(error)
            if backoff.failures == 1:
                print('Node connection/action check failed; retry with bounded backoff, without replay.', flush=True)
        else:
            delay = backoff.delay()
        sleep(delay)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--send', action='store_true', help='Issue one ACL-authorized request, print receipt only')
    mode.add_argument('--status-id', help='Read own issued receipt; no private message body is returned')
    parser.add_argument('--target')
    parser.add_argument('--action', choices=['phone_alarm', 'wol', 'text_message'])
    parser.add_argument('--payload-file', help='UTF-8 JSON file, maximum 4096 bytes')
    parser.add_argument('--request-key', help='Stable unique key per source request; reuse only for exact retry')
    parser.add_argument('--ttl-seconds', type=int, default=120)
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    config = json.loads(config_path.read_text())
    try:
        config['url'] = validate_base_url(config['url'])
    except (ValueError, KeyError):
        parser.error('url must be HTTPS with no credentials/query and only / or /daevis path')
    if args.send:
        if not all((args.target, args.action, args.payload_file, args.request_key)):
            parser.error('--send requires --target --action --payload-file --request-key')
        raw = Path(args.payload_file).read_bytes()
        if len(raw) > 4096:
            parser.error('payload file too large')
        receipt = send_request(config, args.target, args.action, json.loads(raw.decode('utf-8')), args.request_key, args.ttl_seconds)
        print(json.dumps(receipt))
        return
    if args.status_id:
        print(json.dumps(request_status(config, args.status_id)))
        return
    if any((args.target, args.action, args.payload_file, args.request_key)):
        parser.error('request arguments require --send')
    state_path = config_path.with_suffix('.journal.sqlite3')
    state = sqlite3.connect(state_path)
    os.chmod(state_path, 0o600)
    state.execute('CREATE TABLE IF NOT EXISTS journal(id TEXT PRIMARY KEY,status TEXT,acknowledged INTEGER)')
    state.commit()
    poll_forever(config, state)


if __name__ == '__main__':
    main()
