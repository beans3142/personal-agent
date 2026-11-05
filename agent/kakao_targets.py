"""Private cache of verified lookup metadata, never message bodies or UI indices.

These entries are hints, not send authorization. Every use must search and
revalidate the live native room; duplicate names must still stop the sender.
"""
import json
import os
import time
from pathlib import Path


class KakaoTargets:
    def __init__(self, path):
        self.path = Path(path)

    def _load(self):
        try:
            data = json.loads(self.path.read_text())
            return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            return {}

    def _key(self, alias, kind):
        return kind + ':' + alias.strip().casefold()

    def get(self, alias, kind):
        entry = self._load().get(self._key(alias, kind))
        if not isinstance(entry, dict) or time.time() - entry.get('verified_at', 0) > 30 * 86400:
            return None
        return dict(entry)

    def remember(self, alias, title, kind, members):
        if kind not in {'individual', 'group'} or not alias.strip() or not title.strip():
            raise ValueError('invalid target metadata')
        if kind == 'group' and (type(members) is not int or members < 2):
            raise ValueError('group member count must be observed')
        if kind == 'individual' and members is not None:
            raise ValueError('individual target cannot have group members')
        data = self._load();key = self._key(alias, kind);old = data.get(key, {})
        data[key] = {'alias': alias, 'title': title, 'kind': kind, 'members': members,
                     'verified_at': time.time(), 'hits': old.get('hits', 0) + 1}
        data = dict(sorted(data.items(), key=lambda item: item[1].get('verified_at', 0), reverse=True)[:256])
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp = self.path.with_suffix('.tmp')
        with os.fdopen(os.open(temp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), 'w') as stream:
            json.dump(data, stream, ensure_ascii=False)
            stream.flush();os.fsync(stream.fileno())
        temp.replace(self.path)

    def frequent(self, limit=10):
        now = time.time()
        entries = [e for e in self._load().values() if isinstance(e, dict) and now - e.get('verified_at', 0) <= 30 * 86400]
        return sorted(entries, key=lambda e: (-e.get('hits', 0), -e['verified_at']))[:limit]
