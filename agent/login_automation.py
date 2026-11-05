"""Owner-local delegation for the fixed Naver -> desktop Chat image workflow.

No tool can grant access. Removing/disabling login-policy.json revokes it on the
next check. A trusted live owner request is required, with one challenge budget.
"""
import hashlib
import json
import os
import time
from pathlib import Path


class LoginImagePermission:
    def __init__(self, data, request_id, owner_active):
        self.data = Path(data)
        self.request_id = request_id
        self.owner_active = owner_active

    def enabled(self, site):
        if site != 'naver':
            return False
        try:
            entry = json.loads((self.data / 'login-policy.json').read_text()).get(site, {})
            return entry.get('enabled') is True and entry.get('solver') == 'desktop'
        except (OSError, ValueError, AttributeError):
            return False

    def can_login(self, site):
        if not self.enabled(site) or not self.owner_active():
            return False
        try:
            path = self.data / 'login-automation-state.json'
            state = json.loads(path.read_text()) if path.exists() else {}
            return state.get(site, {}).get('paused') is not True
        except (OSError, ValueError, AttributeError):
            return False

    def record_outcome(self, site, status):
        if site != 'naver':
            return
        path = self.data / 'login-automation-state.json'
        state = json.loads(path.read_text()) if path.exists() else {}
        state[site] = {'paused': status != 'ok', 'reason': status, 'at': time.time()}
        self._save(path, state)

    def resume(self):
        # Called only by the owner-only controller, never a model tool.
        path = self.data / 'login-automation-state.json'
        state = json.loads(path.read_text()) if path.exists() else {}
        state['naver'] = {'paused': False, 'reason': 'owner_resumed', 'at': time.time()}
        self._save(path, state)

    def _save(self, path, value):
        self.data.mkdir(parents=True, exist_ok=True)
        temp = path.with_suffix('.tmp')
        with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temp.replace(path)

    def __call__(self, name, args):
        if name != 'login_image_challenge' or args != {'site': 'naver', 'solver': 'ChatGPT desktop'}:
            return False
        if not self.can_login('naver') or not isinstance(self.request_id, str) or not 1 <= len(self.request_id) <= 128:
            return False
        self.data.mkdir(parents=True, exist_ok=True)
        path = self.data / 'login-automation-budget.json'
        lock = path.with_suffix('.lock')
        try:
            fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
        except FileExistsError:
            return False
        os.close(fd)
        try:
            used = json.loads(path.read_text()) if path.exists() else []
            if not isinstance(used, list) or not self.owner_active() or not self.enabled('naver'):
                return False
            digest = hashlib.sha256(('naver\0' + self.request_id).encode()).hexdigest()
            if digest in used:
                return False
            temp = path.with_suffix('.tmp')
            with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as stream:
                json.dump((used + [digest])[-1024:], stream)
                stream.flush()
                os.fsync(stream.fileno())
            temp.replace(path)
            return True
        except (OSError, ValueError):
            return False
        finally:
            lock.unlink(missing_ok=True)
