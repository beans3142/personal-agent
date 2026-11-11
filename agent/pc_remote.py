"""Remote PC control on the power relay: pc_task jobs, long polls and file attachments.

The main agent ('agent-main') sends `pc_task` jobs to a registered PC. The PC's
Daevis agent long-polls `/pc/poll` for them (the older power receiver on the same
node keeps polling `/poll` and never sees them), runs the step and acknowledges
with a result of at most 2 KB. Screenshots, found files and long output travel as
attachments: one side uploads bytes for one named audience, the other downloads
them within an hour.
"""
import hashlib
import json
from pathlib import Path
import re
import secrets
import time

FILE_LIMIT = 100 * 1024 * 1024     # One attachment. Over Discord's 10 MB cap the owner gets a download link.
STORE_LIMIT = 600 * 1024 * 1024    # All attachments waiting on the relay host.
FILE_SECONDS = 3600
SHARE_SECONDS = 86400              # Download links for the owner last a day.
SHARE = re.compile(r'[A-Za-z0-9_-]{43}')
TASK = 'pc_task'
NAME = re.compile(r'[^\\/:*?"<>|\x00-\x1f]{1,120}')


class RemoteMixin:
    """Mixed into PCRegistry; uses its _db, _auth, _pc_auth, clock and DeviceStore.poll."""

    def _remote_tables(self, db):
        db.execute('CREATE TABLE IF NOT EXISTS pc_files(id TEXT PRIMARY KEY,uploader TEXT,audience TEXT,name TEXT,size INTEGER,sha256 TEXT,created REAL,expires REAL)')
        columns = {r['name'] for r in db.execute('PRAGMA table_info(pc_files)')}
        for name, kind in (('share', 'TEXT'), ('share_expires', 'REAL')):
            if name not in columns:
                db.execute(f'ALTER TABLE pc_files ADD COLUMN {name} {kind}')

    def ensure_pc_tasks(self):
        """Give every registered PC the pc_task capability and let the main agent send it."""
        with self._db() as db:
            self._remote_tables(db)
            agent = db.execute("SELECT digest FROM nodes WHERE name='agent-main'").fetchone()
            for row in db.execute('SELECT n.name,n.capabilities FROM pcs p JOIN nodes n ON n.name=p.node WHERE n.digest IS NOT NULL').fetchall():
                capabilities = json.loads(row['capabilities'])
                if TASK not in capabilities:
                    db.execute('UPDATE nodes SET capabilities=? WHERE name=?', (json.dumps(sorted(capabilities + [TASK])), row['name']))
                if agent and agent['digest']:
                    db.execute("INSERT OR IGNORE INTO controls VALUES('agent-main',?,?)", (row['name'], TASK))

    # --- jobs ------------------------------------------------------------------------------

    def poll(self, node, token, actions=None, exclude=()):
        # The legacy power receiver polls /poll; remote-control steps are for /pc/poll only.
        if actions is None and not exclude:
            exclude = (TASK,)
        return super().poll(node, token, actions, exclude)

    def task_poll(self, node, token, wait=20, step=0.3, sleep=time.sleep, monotonic=time.monotonic):
        """Long poll: return the next pc_task for this PC, or None after `wait` seconds."""
        self._pc_auth(node, token)
        if type(wait) not in (int, float) or not 0 <= wait <= 25:
            raise ValueError('wait must be 0..25 seconds')
        self._forget_tasks(None)
        deadline = monotonic() + wait
        while True:
            job = super().poll(node, token, (TASK,))
            if job or monotonic() >= deadline:
                return job
            sleep(step)

    def wait_status(self, source, token, job_id, wait=20, step=0.3, sleep=time.sleep, monotonic=time.monotonic):
        """The issuer's receipt, returned early once the job leaves queued/dispatched."""
        if type(wait) not in (int, float) or not 0 <= wait <= 25:
            raise ValueError('wait must be 0..25 seconds')
        deadline = monotonic() + wait
        while True:
            state = self.request_status(source, token, job_id)
            if state['status'] not in ('queued', 'dispatched'):
                self._forget_tasks(job_id)
                return state
            if monotonic() >= deadline:
                return state
            sleep(step)

    def _forget_tasks(self, job_id):
        """Results can hold screen text or file lists: clear one once its issuer has it, and drop
        finished steps after an hour so they never fill the relay's 1000-job history."""
        with self._db() as db:
            db.execute("UPDATE jobs SET result=NULL WHERE id=? AND action='pc_task'", (job_id,))
            db.execute("DELETE FROM jobs WHERE action='pc_task' AND status NOT IN ('queued','dispatched') AND created<?", (self.clock() - 3600,))

    def agent_pcs_list(self):
        return [{'label': p['label'], 'node': p['node'], 'default': bool(p['is_default']), 'online': p['online']} for p in self.inventory()]

    def agent_pcs(self, token):
        self._auth('agent-main', token)
        return self.agent_pcs_list()

    # --- files -----------------------------------------------------------------------------

    def _files_folder(self):
        folder = Path(self.path).parent / 'pc-files'
        folder.mkdir(parents=True, exist_ok=True)
        return folder

    def _purge_files(self, db):
        for row in db.execute('SELECT id FROM pc_files WHERE MAX(expires,COALESCE(share_expires,0))<=?', (self.clock(),)).fetchall():
            (self._files_folder() / row['id']).unlink(missing_ok=True)
            db.execute('DELETE FROM pc_files WHERE id=?', (row['id'],))

    def _device(self, db, node):
        return db.execute('SELECT 1 FROM pcs WHERE node=? UNION SELECT 1 FROM phones WHERE node=?', (node, node)).fetchone()

    def file_put(self, uploader, token, audience, name, data):
        """Store bytes for one reader: a PC's file for the main agent, or the agent's file for a device."""
        if uploader == 'agent-main':
            self._auth(uploader, token)
        else:
            self._pc_auth(uploader, token)
        if not isinstance(name, str) or not NAME.fullmatch(name) or name.strip(' .') == '' or '..' in name:
            raise ValueError('invalid file name')
        spooled = isinstance(data, Path)  # The HTTP upload streams into a temporary file first.
        if not spooled and not isinstance(data, (bytes, bytearray)):
            raise ValueError('invalid file data')
        size = data.stat().st_size if spooled else len(data)
        if size > FILE_LIMIT:
            raise ValueError('file too large')
        with self._db() as db:
            self._remote_tables(db)
            db.execute('BEGIN IMMEDIATE')
            self._purge_files(db)
            # The main agent may also keep a file for itself, to hand the owner a download link.
            allowed = (audience == 'agent-main' or self._device(db, audience)) if uploader == 'agent-main' else audience == 'agent-main'
            if not allowed:
                raise ValueError('invalid audience')
            used = db.execute('SELECT COALESCE(SUM(size),0) FROM pc_files').fetchone()[0]
            if used + size > STORE_LIMIT:
                raise ValueError('relay file storage full; try again later')
            file_id = secrets.token_hex(16)
            target = self._files_folder() / file_id
            if spooled:
                hasher = hashlib.sha256()
                with data.open('rb') as f:
                    for chunk in iter(lambda: f.read(1 << 20), b''):
                        hasher.update(chunk)
                digest = hasher.hexdigest()
                data.replace(target)
            else:
                target.write_bytes(bytes(data))
                digest = hashlib.sha256(data).hexdigest()
            now = self.clock()
            db.execute('INSERT INTO pc_files(id,uploader,audience,name,size,sha256,created,expires) VALUES(?,?,?,?,?,?,?,?)',
                       (file_id, uploader, audience, name, size, digest, now, now + FILE_SECONDS))
        return {'id': file_id, 'name': name, 'size': size, 'sha256': digest}

    def file_share(self, node, token, file_id):
        """A day-long download link for a file the main agent holds (too big for a Discord attachment)."""
        if node != 'agent-main':
            raise PermissionError('main agent only')
        self._auth(node, token)
        if not isinstance(file_id, str) or not re.fullmatch(r'[0-9a-f]{32}', file_id):
            raise ValueError('invalid file id')
        share = secrets.token_urlsafe(32)
        with self._db() as db:
            self._remote_tables(db)
            self._purge_files(db)
            updated = db.execute("UPDATE pc_files SET share=?,share_expires=? WHERE id=? AND (audience='agent-main' OR uploader='agent-main')",
                                 (hashlib.sha256(share.encode()).hexdigest(), self.clock() + SHARE_SECONDS, file_id)).rowcount
        if not updated:
            raise ValueError('file expired or unknown')
        return {'token': share, 'expires_hours': SHARE_SECONDS // 3600}

    def shared(self, share):
        """(name, path) behind a download link; anyone with the link may download until it expires."""
        if not isinstance(share, str) or not SHARE.fullmatch(share):
            raise ValueError('invalid link')
        with self._db() as db:
            self._remote_tables(db)
            self._purge_files(db)
            row = db.execute('SELECT id,name FROM pc_files WHERE share=? AND share_expires>?',
                             (hashlib.sha256(share.encode()).hexdigest(), self.clock())).fetchone()
        if not row:
            raise ValueError('link expired or unknown')
        return row['name'], self._files_folder() / row['id']

    def file_get(self, node, token, file_id):
        if node == 'agent-main':
            self._auth(node, token)
        else:
            self._pc_auth(node, token)
        if not isinstance(file_id, str) or not re.fullmatch(r'[0-9a-f]{32}', file_id):
            raise ValueError('invalid file id')
        with self._db() as db:
            self._remote_tables(db)
            self._purge_files(db)
            row = db.execute('SELECT * FROM pc_files WHERE id=?', (file_id,)).fetchone()
        if not row:
            raise ValueError('file expired or unknown')
        if node not in (row['audience'], row['uploader']):
            raise PermissionError('file is for another device')
        return row['name'], (self._files_folder() / file_id).read_bytes()
