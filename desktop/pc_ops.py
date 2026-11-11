"""The steps the server agent can run on this PC. Each takes (args, ctx) and returns a dict.

ctx.upload(name, bytes) hands a file to the server agent; ctx.download(id) fetches one
the agent sent here. Results over the receipt size go up as attachments automatically.
"""
from datetime import datetime
import io
import os
from pathlib import Path
import string
import subprocess
import time
import unicodedata

from desktop import uia

HOME = Path.home()
DOWNLOADS = HOME / 'Downloads'
SEND_LIMIT = 100 * 1024 * 1024  # The relay's per-file cap.
OUTPUT_LIMIT = 200_000
SKIP_DIRS = {'windows', 'program files', 'program files (x86)', 'programdata', 'appdata', '$recycle.bin', 'system volume information',
             'node_modules', '.git', '__pycache__', '.venv', 'venv', 'site-packages', '.cache', '.gradle', '.m2', '.nuget',
             'recovery', 'msocache', '$windows.~ws', '$windows.~bt', 'windowsapps'}
_snapshot = None


def _norm(text):
    return unicodedata.normalize('NFC', text).casefold()


# --- screen and windows ------------------------------------------------------------------------

def _grab():
    from PIL import ImageGrab
    user32 = __import__('ctypes').windll.user32
    return ImageGrab.grab(all_screens=True), (user32.GetSystemMetrics(76), user32.GetSystemMetrics(77))


def screenshot(args, ctx):
    """All monitors as one JPEG; x_screen = origin + x_image / scale."""
    image, origin = _grab()
    width = max(320, min(int(args.get('max_width', 1600)), 3840))
    scale = min(1.0, width / image.width)
    if scale < 1:
        image = image.resize((round(image.width * scale), round(image.height * scale)))
    buffer = io.BytesIO()
    image.convert('RGB').save(buffer, 'JPEG', quality=int(args.get('quality', 70)))
    meta = ctx.upload(datetime.now().strftime('screen-%Y%m%d-%H%M%S.jpg'), buffer.getvalue())
    return {'file': meta, 'width': image.width, 'height': image.height, 'scale': scale, 'origin': list(origin)}


def windows(args, ctx):
    return {'windows': uia.windows()}


_tree_instance = None


def _tree():
    global _tree_instance
    if _tree_instance is None:
        _tree_instance = uia.ComTree()
    return _tree_instance


def ui(args, ctx):
    """Numbered UI elements of one window (title contains `window`) or the window in front."""
    global _snapshot
    tree = _tree()
    target = args.get('window')
    if args.get('activate', True) and target:
        match = [w for w in uia.windows() if _norm(str(target)) in _norm(w['title'])]
        if match:
            uia.activate(match[0]['hwnd'])
    root = tree.window(target)
    _snapshot = uia.Snapshot(tree)
    items = _snapshot.take(root, limit=max(10, min(int(args.get('limit', 150)), 400)), depth=max(1, min(int(args.get('depth', 12)), 30)))
    return {'window': items[0]['name'] if items else '', 'elements': items}


def act(args, ctx):
    """click / double_click / right_click / invoke / type / set_value / key / scroll / focus, on an element id or x,y."""
    action = args.get('action')
    element = args.get('element')
    tree = _tree() if element else None
    node = (_snapshot or uia.Snapshot(None)).element(element) if element else None
    point = None
    if node is not None:
        point = tree.center(node) if action in ('click', 'double_click', 'right_click', 'scroll') else None
    elif 'x' in args and 'y' in args:
        point = (int(args['x']), int(args['y']))
    if action in ('click', 'double_click', 'right_click'):
        if point is None:
            raise ValueError('click needs an element id or x and y')
        uia.click(*point, button='right' if action == 'right_click' else 'left', double=action == 'double_click')
    elif action == 'invoke':
        if node is None or not tree.invoke(node):
            raise ValueError('element cannot be invoked; use click')
    elif action == 'focus':
        if node is None:
            raise ValueError('focus needs an element id')
        tree.focus(node)
    elif action == 'set_value':
        if node is None or not tree.set_value(node, str(args.get('text', ''))):
            raise ValueError('element has no value; focus it and use type')
    elif action == 'type':
        if node is not None:
            tree.focus(node)
        elif point is not None:
            uia.click(*point)
        uia.type_text(str(args.get('text', '')))
    elif action == 'key':
        uia.press(str(args.get('keys', '')))
    elif action == 'scroll':
        if point is None:
            user32 = __import__('ctypes').windll.user32
            point = (user32.GetSystemMetrics(0) // 2, user32.GetSystemMetrics(1) // 2)
        uia.scroll(*point, int(args.get('amount', -3)))
    else:
        raise ValueError('unknown action')
    time.sleep(float(args.get('settle', 0.4)))
    return {'done': action}


def open_target(args, ctx):
    """A file, folder, URL or app name (anything the Run dialog accepts)."""
    target = str(args.get('target', '')).strip()
    if not target:
        raise ValueError('target is empty')
    try:
        os.startfile(target)
    except OSError:
        subprocess.Popen(['cmd.exe', '/c', 'start', '', target], creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return {'opened': target}


# --- files ---------------------------------------------------------------------------------------

def default_roots():
    roots = [HOME]
    for letter in string.ascii_uppercase:
        drive = Path(f'{letter}:\\')
        if letter != 'C' and drive.exists():
            roots.append(drive)
    return roots


def _indexed(words, exts, limit):
    """Windows Search index (user folders) answers in well under a second; empty if unavailable."""
    try:
        import win32com.client
        connection = win32com.client.Dispatch('ADODB.Connection')
        connection.Open("Provider=Search.CollatorDSO;Extended Properties='Application=Windows';")
        clauses = ["System.FileName LIKE '%" + w.replace("'", "''").replace('%', '') + "%'" for w in words] or ['1=1']
        if exts:
            clauses.append('(' + ' OR '.join("System.FileExtension='" + e.replace("'", '') + "'" for e in exts) + ')')
        records = win32com.client.Dispatch('ADODB.Recordset')
        records.Open(f'SELECT TOP {limit * 3} System.ItemPathDisplay, System.Size, System.DateModified FROM SystemIndex WHERE '
                     + ' AND '.join(clauses), connection)
        found = []
        while not records.EOF:
            path = records.Fields.Item(0).Value
            try:
                stat = os.stat(path)  # The index shows localised folder names; keep only paths that open.
                found.append({'path': path, 'size': stat.st_size, '_t': stat.st_mtime})
            except (OSError, TypeError):
                pass
            records.MoveNext()
        return found
    except Exception:
        return []


def priority_roots():
    """Where people keep their own files; walked live on every search."""
    names = ['Desktop', 'Documents', 'Downloads', 'Pictures', 'Videos', 'Music']
    roots = [HOME / n for n in names] + sorted(HOME.glob('OneDrive*'))
    return [r for r in roots if r.is_dir()]


def _walk(roots, words, exts, deadline, hits):
    from desktop import file_index
    for path, name, size, mtime in file_index.walk(roots, SKIP_DIRS, lambda: time.monotonic() >= deadline):
        name = _norm(name)
        if (not exts or os.path.splitext(name)[1] in exts) and all(word in name for word in words):
            hits.append({'path': path, 'size': size, '_t': mtime})
    return time.monotonic() < deadline


def find(args, ctx):
    """File names containing every query word, newest first, from the whole PC by default.

    Sources: the agent's whole-PC name index (built in the background), the Windows Search
    index, and a live walk of the main user folders for files newer than the index.
    """
    from desktop import file_index
    words = [_norm(w) for w in str(args.get('query', '')).split()]
    exts = {('.' + str(e).lower().lstrip('.')) for e in args.get('exts') or []}
    limit = max(1, min(int(args.get('limit', 30)), 200))
    deadline = time.monotonic() + max(0, min(float(args.get('seconds', 30)), 120))
    hits, result = [], {}
    if args.get('roots'):
        complete = _walk([Path(r) for r in args['roots']], words, exts, deadline, hits)
    else:
        hits += file_index.search(words, sorted(exts), limit)
        hits += _indexed(words, sorted(exts), limit)
        complete = _walk(priority_roots(), words, exts, deadline, hits)
        index_age = file_index.age()
        if index_age is None:
            # No whole-PC index yet (first minutes after install): walk everything in the time left.
            complete = _walk(default_roots(), words, exts, deadline, hits) and complete
            result['index'] = 'building'
        else:
            result['index_age_hours'] = round(index_age / 3600, 1)
    unique = {}
    for hit in hits:
        unique.setdefault(os.path.normcase(hit['path']), hit)
    ordered = sorted(unique.values(), key=lambda h: -h['_t'])
    files = [{'path': h['path'], 'size': h['size'], 'modified': datetime.fromtimestamp(h['_t']).strftime('%Y-%m-%d %H:%M')} for h in ordered[:limit]]
    return {'files': files, 'matches': len(ordered), 'complete': complete, **result}


def send_file(args, ctx):
    path = Path(str(args.get('path', '')))
    if not path.is_file():
        raise ValueError(f'not a file: {path}')
    size = path.stat().st_size
    if size > SEND_LIMIT:
        raise ValueError(f'file is {size / 1048576:.1f} MB; the limit is {SEND_LIMIT // 1048576} MB')
    return {'file': ctx.upload(path.name, path.read_bytes()), 'path': str(path)}


def _safe_name(name):
    name = Path(str(name).replace('\\', '/')).name
    name = ''.join('_' if c in '<>:"/\\|?*' or ord(c) < 32 else c for c in name).strip(' .')
    return name or 'file'


def save_file(args, ctx):
    """A file the server agent uploaded, saved into Downloads without overwriting anything."""
    name, data = ctx.download(str(args.get('file', '')))
    name = _safe_name(args.get('name') or name)
    stem, suffix = os.path.splitext(name)
    target, n = DOWNLOADS / name, 1
    while target.exists():
        target, n = DOWNLOADS / f'{stem} ({n}){suffix}', n + 1
    DOWNLOADS.mkdir(parents=True, exist_ok=True)
    target.write_bytes(data)
    return {'path': str(target), 'size': len(data)}


# --- system ---------------------------------------------------------------------------------------

def _run(argv):
    subprocess.run(argv, check=True, timeout=30, stdin=subprocess.DEVNULL, capture_output=True,
                   creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def _lock():
    if not __import__('ctypes').windll.user32.LockWorkStation():
        raise OSError('lock failed')


KEY_ACTIONS = {'mute': 'volume_mute', 'volume_up': 'volume_up', 'volume_down': 'volume_down', 'play_pause': 'media_play_pause',
               'next': 'media_next', 'previous': 'media_prev', 'stop': 'media_stop'}


def system(args, ctx):
    """Volume and media keys, lock, sleep, and shutdown/restart with a 60 s window to cancel."""
    action = args.get('action')
    if action in KEY_ACTIONS:
        for _ in range(max(1, min(int(args.get('times', 1)), 50))):
            uia.press(KEY_ACTIONS[action])
    elif action == 'volume':
        level = max(0, min(int(args.get('level', 50)), 100))
        for _ in range(50):
            uia.press('volume_down')  # Each key step is 2 %.
        for _ in range(level // 2):
            uia.press('volume_up')
    elif action == 'lock':
        _lock()
    elif action == 'sleep':
        _run(['rundll32.exe', 'powrprof.dll,SetSuspendState', '0,1,0'])
    elif action == 'shutdown':
        _run(['shutdown.exe', '/s', '/t', '60'])
    elif action == 'restart':
        _run(['shutdown.exe', '/r', '/t', '60'])
    elif action == 'cancel_shutdown':
        _run(['shutdown.exe', '/a'])
    else:
        raise ValueError('unknown system action')
    return {'done': action}


def shell(args, ctx):
    """One PowerShell command as this user; output (stdout+stderr) and exit code."""
    command = str(args.get('command', ''))
    if not command.strip():
        raise ValueError('command is empty')
    timeout = max(1, min(int(args.get('timeout', 60)), 280))
    cwd = args.get('cwd') or str(HOME)
    process = subprocess.Popen(['powershell.exe', '-NoProfile', '-NonInteractive', '-ExecutionPolicy', 'Bypass', '-Command',
                                '[Console]::OutputEncoding=[Text.Encoding]::UTF8; ' + command],
                               cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                               creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    try:
        output, _ = process.communicate(timeout=timeout)
        timed_out = False
    except subprocess.TimeoutExpired:
        subprocess.run(['taskkill', '/F', '/T', '/PID', str(process.pid)], capture_output=True,
                       creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        output, _ = process.communicate()
        timed_out = True
    text = output.decode('utf-8', errors='replace')
    if len(text) > OUTPUT_LIMIT:
        text = text[:OUTPUT_LIMIT] + '\n[truncated]'
    return {'exit_code': process.returncode, 'output': text, 'timed_out': timed_out}


OPS = {'screenshot': screenshot, 'windows': windows, 'ui': ui, 'act': act, 'open': open_target, 'find': find,
       'send_file': send_file, 'save_file': save_file, 'system': system, 'shell': shell}
