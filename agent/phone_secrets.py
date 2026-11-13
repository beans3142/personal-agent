"""Private credential handoff to a registered phone, never through model/job payloads."""
import json
import re


class PhoneSecrets:
    def __init__(self, registry, provider):
        self.registry, self.provider = registry, provider
        with registry._db() as db:
            db.execute('CREATE TABLE IF NOT EXISTS phone_unlock_attempts(job TEXT PRIMARY KEY,node TEXT,attempted REAL)')

    def release(self, node, token, job_id):
        if not isinstance(node, str) or not isinstance(job_id, str):
            raise PermissionError('unauthorized')
        self.registry._auth(node, token)
        now = self.registry.clock()
        with self.registry._db() as db:
            db.execute('BEGIN IMMEDIATE')
            job = db.execute("SELECT j.id FROM jobs j JOIN phones p ON p.node=j.node WHERE j.id=? AND j.node=? "
                             "AND j.action='phone_task' AND j.status='dispatched' AND j.issuer='agent-main' AND j.expires>?",
                             (job_id, node, now)).fetchone()
            if not job or db.execute('SELECT 1 FROM phone_unlock_attempts WHERE job=? OR (node=? AND attempted>?)',
                                     (job_id, node, now - 300)).fetchone():
                raise PermissionError('unlock unavailable for this request')
            # Reserve before calling the provider: failures never trigger repeated password entry.
            db.execute('INSERT INTO phone_unlock_attempts VALUES(?,?,?)', (job_id, node, now))
        try:
            password = self.provider(node)
            if not isinstance(password, str) or not 1 <= len(password) <= 128 or any(ord(c) < 32 for c in password):
                raise ValueError()
        except Exception:
            raise ValueError('credential unavailable') from None
        return {'password': password}


def parameter_provider(ssm):
    def read(node):
        if not re.fullmatch(r'phone-[a-f0-9]{16}', node):
            raise ValueError('invalid phone')
        return ssm.get_parameter(Name='/personal-agent/phone-unlock/' + node, WithDecryption=True)['Parameter']['Value']
    return read


def secret_handler(parent, service):
    class Handler(parent):
        def do_POST(self):
            if self.path != '/phone/unlock-secret':
                return super().do_POST()
            self.connection.settimeout(10)
            try:
                size = int(self.headers.get('Content-Length', '0'))
                if not 0 < size <= 1024: raise ValueError()
                body = json.loads(self.rfile.read(size))
                if not isinstance(body, dict) or set(body) != {'node', 'id'}: raise ValueError()
                auth = self.headers.get('Authorization', '')
                if not auth.startswith('Bearer '): raise PermissionError()
                reply, code = service.release(body['node'], auth[7:], body['id']), 200
            except PermissionError:
                reply, code = {'error': 'unauthorized'}, 403
            except (ValueError, TypeError):
                reply, code = {'error': 'credential unavailable'}, 400
            except Exception:
                reply, code = {'error': 'credential unavailable'}, 503
            data = json.dumps(reply).encode()
            self.send_response(code)
            self.send_header('Content-Type', 'application/json')
            self.send_header('Cache-Control', 'no-store')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
    return Handler
