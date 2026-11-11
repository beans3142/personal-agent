"""File-name index of the whole PC, so a search for a file anywhere answers in about a second.

Walking every drive takes minutes (D: alone held over half a million files), so the agent
builds this index in a low-priority background thread at start and again every 12 hours.
Searches combine it with a live walk of the main user folders for anything newer.
"""
import os
from pathlib import Path
import sqlite3
import threading
import time
import unicodedata

PATH = Path.home() / '.daevis' / 'desktop' / 'files.sqlite3'
REFRESH_SECONDS = 12 * 3600


def norm(text):
    return unicodedata.normalize('NFC', text).casefold()


def folders(roots, skip, should_stop=lambda: False):
    """Yield (folder, [file names]) under roots, skipping dependency/system folders."""
    stack = [str(r) for r in reversed(list(roots))]
    while stack and not should_stop():
        folder = stack.pop()
        try:
            entries = list(os.scandir(folder))
        except OSError:
            continue
        if any(e.name == 'pyvenv.cfg' for e in entries):
            continue  # A Python virtual environment: thousands of library files, no documents.
        names = []
        for entry in entries:
            try:
                if entry.is_dir(follow_symlinks=False):
                    if entry.name.lower() not in skip and not entry.name.startswith(('.', '$')):
                        stack.append(entry.path)
                else:
                    names.append(entry.name)
            except OSError:
                continue
        yield folder, names


def walk(roots, skip, should_stop=lambda: False):
    """Yield (path, name, size, mtime) for every file under roots (live searches)."""
    for folder, names in folders(roots, skip, should_stop):
        for name in names:
            path = os.path.join(folder, name)
            try:
                stat = os.stat(path, follow_symlinks=False)
            except OSError:
                continue
            yield path, name, stat.st_size, stat.st_mtime


def build(path, roots, skip, should_stop=lambda: False, pause=lambda: None):
    """Write a fresh index next to the old one and swap it in, so searches never see a half-built index.

    Only folder paths and file names are stored (a PC with game assets held ~6 million files);
    size and date are read from disk for the matches of each search.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix('.building')
    temporary.unlink(missing_ok=True)
    db = sqlite3.connect(temporary)
    try:
        db.execute('CREATE TABLE dirs(id INTEGER PRIMARY KEY, path TEXT)')
        db.execute('CREATE TABLE files(dir INTEGER, name TEXT)')
        batch, count, done = [], 0, 0
        for number, (folder, names) in enumerate(folders(roots, skip, should_stop), 1):
            db.execute('INSERT INTO dirs VALUES(?,?)', (number, folder))
            batch += [(number, norm(name)) for name in names]
            if len(batch) >= 20000:
                db.executemany('INSERT INTO files VALUES(?,?)', batch)
                count += len(batch)
                batch = []
                pause()
        if should_stop():
            return None
        db.executemany('INSERT INTO files VALUES(?,?)', batch)
        count += len(batch)
        db.execute('CREATE TABLE meta(built REAL, files INTEGER)')
        db.execute('INSERT INTO meta VALUES(?,?)', (time.time(), count))
        db.commit()
    finally:
        db.close()
    os.replace(temporary, path)
    return count


def age(path=None):
    path = path or PATH
    try:
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
            return time.time() - db.execute('SELECT built FROM meta').fetchone()[0]
    except (sqlite3.Error, TypeError):
        return None


def search(words, exts, limit, path=None, scan=2000):
    """Files whose name has every word and one of exts, newest first; [] without an index.

    The stored names are NFC-casefolded, so callers pass words the same way.
    """
    path = path or PATH
    if not Path(path).exists():
        return []
    escape = lambda text: text.replace('!', '!!').replace('%', '!%').replace('_', '!_')
    clauses, values = [], []
    for word in words:
        clauses.append("f.name LIKE ? ESCAPE '!'")
        values.append('%' + escape(word) + '%')
    if exts:
        clauses.append('(' + ' OR '.join("f.name LIKE ? ESCAPE '!'" for _ in exts) + ')')
        values += ['%' + escape(e) for e in exts]
    query = ('SELECT d.path, f.name FROM files f JOIN dirs d ON d.id=f.dir'
             + (' WHERE ' + ' AND '.join(clauses) if clauses else '') + ' LIMIT ?')
    try:
        with sqlite3.connect(f'file:{path}?mode=ro', uri=True) as db:
            rows = db.execute(query, values + [scan]).fetchall()
    except sqlite3.Error:
        return []
    found = []
    for folder, name in rows:
        full = os.path.join(folder, name)
        try:
            stat = os.stat(full)  # Gone since the index was built: skip it.
        except OSError:
            continue
        found.append({'path': full, 'size': stat.st_size, '_t': stat.st_mtime})
    found.sort(key=lambda h: -h['_t'])
    return found[:limit * 3]


def keep_fresh(roots, skip, log=lambda line: None, path=None, every=REFRESH_SECONDS):
    path = path or PATH
    """Background thread: build when missing or older than `every`, at below-normal priority."""
    def pause():
        time.sleep(0.01)  # Yield between batches so the PC stays responsive.

    def run():
        try:
            import ctypes
            ctypes.windll.kernel32.SetThreadPriority(ctypes.windll.kernel32.GetCurrentThread(), -2)  # THREAD_PRIORITY_LOWEST
        except Exception:
            pass
        while True:
            current = age(path)
            if current is None or current >= every:
                started = time.monotonic()
                try:
                    count = build(path, roots(), skip, pause=pause)
                    log(f'file index built: {count} files in {time.monotonic() - started:.0f}s')
                except Exception as error:
                    log(f'file index failed ({type(error).__name__})')
            time.sleep(3600)
    thread = threading.Thread(target=run, daemon=True, name='daevis-file-index')
    thread.start()
    return thread
