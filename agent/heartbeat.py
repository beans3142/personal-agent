"""Small, single-writer health transition tracker; no sampling or delivery.

Two consecutive known failures raise one notice. Unknown samples break the
failure streak, preserve an announced problem, and never imply recovery.
Persist before returning notices so process restarts do not replay them.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re
import tempfile


_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,79}\Z")
_MAX_KEYS = 128
_MAX_STATE_BYTES = 32_768


def _valid_key(key):
    return isinstance(key, str) and _KEY.fullmatch(key) is not None


class HeartbeatTracker:
    def __init__(self, path):
        self.path = Path(path)
        self._state = {}
        try:
            with self.path.open("rb") as stream:
                content = stream.read(_MAX_STATE_BYTES + 1)
            if len(content) > _MAX_STATE_BYTES:
                raise ValueError()
            state = json.loads(content)
            if not isinstance(state, dict) or len(state) > _MAX_KEYS:
                raise ValueError()
            for key, value in state.items():
                if (not _valid_key(key) or not isinstance(value, dict)
                        or set(value) != {"failures", "notified"}
                        or type(value["failures"]) is not int or not 0 <= value["failures"] <= 2
                        or type(value["notified"]) is not bool):
                    raise ValueError()
            self._state = state
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError):
            raise ValueError("heartbeat state unavailable") from None

    def update(self, samples: dict[str, bool | None]) -> list[str]:
        if (not isinstance(samples, dict) or len(samples) > _MAX_KEYS
                or any(not _valid_key(key) or (value is not None and type(value) is not bool)
                       for key, value in samples.items())
                or len(set(samples) | set(self._state)) > _MAX_KEYS):
            raise ValueError("invalid heartbeat samples")
        state = {key: dict(value) for key, value in self._state.items()}
        notices = []
        for key in sorted(samples):
            value = samples[key]
            entry = state.setdefault(key, {"failures": 0, "notified": False})
            label = {"android": "Android", "n8n": "n8n", "memory": "메모리"}.get(key, key)
            if key.startswith("node:"):
                label = "기기 " + key[5:]
            if value is False:
                entry["failures"] = min(2, entry["failures"] + 1)
                if entry["failures"] == 2 and not entry["notified"]:
                    notices.append(f"{label} 상태 이상이 2회 연속 확인됐어요.")
                    entry["notified"] = True
            else:
                entry["failures"] = 0
                if value is True and entry["notified"]:
                    notices.append(f"{label} 상태가 회복됐어요.")
                    entry["notified"] = False
        if state != self._state:
            self._save(state)
            self._state = state
        return notices

    def _save(self, state):
        temporary = None
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            fd, temporary = tempfile.mkstemp(prefix=".heartbeat-", dir=self.path.parent)
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                os.chmod(temporary, 0o600)
                json.dump(state, stream, sort_keys=True, separators=(",", ":"))
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        except OSError:
            raise ValueError("heartbeat state could not be saved") from None
        finally:
            if temporary is not None:
                try:
                    os.unlink(temporary)
                except OSError:
                    pass
