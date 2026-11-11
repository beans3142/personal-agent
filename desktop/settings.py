"""Per-user app settings in ~/.daevis/desktop/settings.json."""
import json
from pathlib import Path

DEFAULTS = {'voice': 'ko-KR-SunHiNeural', 'rate': 0, 'speak_typed': False, 'listen': True, 'autostart': True,
            'remote_control': True}  # The owner's server agent may operate this PC (Ctrl+Alt+F12 flips it).
PATH = Path.home() / '.daevis' / 'desktop' / 'settings.json'


def load(path=PATH):
    try:
        stored = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        stored = {}
    result = dict(DEFAULTS)
    if isinstance(stored, dict):
        for key, default in DEFAULTS.items():
            if type(stored.get(key)) is type(default):
                result[key] = stored[key]
    return result


def save(path, values):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(values, ensure_ascii=False, indent=1), encoding='utf-8')
    temporary.replace(path)
