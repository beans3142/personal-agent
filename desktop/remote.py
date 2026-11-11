"""Remote control of this PC by the owner's server agent.

The agent long-polls the relay for `pc_task` steps (screenshot, UI tree, click, files,
PowerShell, ...), runs each one and acknowledges it. Every step is appended to
~/.daevis/desktop/remote-actions.jsonl. The owner turns remote control off with
Ctrl+Alt+F12 or the switch in the app's settings; while off, steps are refused unrun.
"""
import json
from pathlib import Path
import threading
import time
import urllib.parse
import urllib.request

from desktop import settings

RESULT_LIMIT = 1900   # The relay keeps receipts up to 2048 JSON characters.
LOG_LIMIT = 5 * 1024 * 1024
HOTKEY = 'Ctrl+Alt+F12'


def short(value, limit=200):
    text = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
    return text if len(text) <= limit else text[:limit] + '…'


class Remote:
    def __init__(self, client, load_settings, log_path, ops=None):
        if ops is None:
            from desktop import pc_ops
            ops = pc_ops.OPS
        self.client, self.load_settings, self.log_path, self.ops = client, load_settings, Path(log_path), ops

    def run_once(self, wait=20):
        job = self.client.poll(wait)
        if not job:
            return False
        started = time.monotonic()
        payload = job.get('payload') or {}
        op, args = payload.get('op'), payload.get('args') or {}
        status, result = self.execute(job, op, args)
        try:
            self.client.ack(job['id'], status, result)
        finally:
            self.log({'time': time.strftime('%F %T'), 'op': op, 'args': {k: short(v) for k, v in args.items()},
                      'status': status, 'ms': int((time.monotonic() - started) * 1000),
                      **({'error': short(result.get('error'))} if result.get('error') else {})})
        return True

    def execute(self, job, op, args):
        if job.get('expires', 0) <= time.time():
            return 'expired', {'error': 'expired'}
        try:
            enabled = bool(self.load_settings().get('remote_control', True))
        except Exception:
            enabled = False  # Unreadable settings: stay safe.
        if not enabled:
            return 'failed', {'error': 'remote_off', 'message': f'이 PC에서 원격 제어가 꺼져 있어요 ({HOTKEY}로 켤 수 있어요).'}
        handler = self.ops.get(op)
        if handler is None:
            return 'failed', {'error': 'unknown op'}
        try:
            result = handler(args, self)
        except Exception as error:
            return 'failed', {'error': f'{type(error).__name__}: {short(str(error), 600)}'}
        return 'succeeded', self.fit({'ok': True, **(result or {})})

    def fit(self, result):
        """Results over the receipt limit go up as a JSON attachment; the receipt keeps a preview."""
        encoded = json.dumps(result, ensure_ascii=False)
        if len(encoded) <= RESULT_LIMIT:
            return result
        meta = self.upload('result.json', encoded.encode('utf-8'))
        return {'ok': True, 'data_file': meta['id'], 'preview': encoded[:800]}

    # Context for ops: attachments through the relay.
    def upload(self, name, data):
        return self.client.upload(name, data)

    def download(self, file_id):
        return self.client.download(file_id)

    def log(self, entry):
        try:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            if self.log_path.exists() and self.log_path.stat().st_size > LOG_LIMIT:
                self.log_path.replace(self.log_path.with_suffix('.old.jsonl'))
            with self.log_path.open('a', encoding='utf-8') as f:
                f.write(json.dumps(entry, ensure_ascii=False) + '\n')
        except OSError:
            pass

    def forever(self, on_error=lambda error: None, sleep=time.sleep):
        failures = 0
        while True:
            try:
                self.run_once()
                failures = 0
            except Exception as error:
                failures += 1
                on_error(error)
                sleep(min(60, 2 ** min(failures, 6)))


class RelayClient:
    """pc_task long polls, receipts and attachments with this PC's node credentials."""
    def __init__(self, config, post=None, opener=None):
        from desktop import relay
        self.base, self.node = config['url'].rstrip('/'), config['node']
        self.token = Path(config['token_file']).read_text(encoding='utf-8').strip()
        self.post = post or relay.http_post(self.base, self.token)
        self.opener = opener or relay.no_redirect_opener()

    def poll(self, wait):
        return self.post('/pc/poll', {'node': self.node, 'wait': wait})['job']

    def ack(self, job_id, status, result):
        self.post('/ack', {'node': self.node, 'id': job_id, 'status': status, 'result': result})

    def upload(self, name, data):
        request = urllib.request.Request(self.base + '/pc/upload', data=data, method='POST', headers={
            'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/octet-stream',
            'X-Daevis-Node': self.node, 'X-Daevis-Audience': 'agent-main', 'X-Daevis-Name': urllib.parse.quote(name)})
        with self.opener.open(request, timeout=120) as response:
            return json.loads(response.read(65536))

    def download(self, file_id):
        request = urllib.request.Request(self.base + '/pc/download', data=json.dumps({'node': self.node, 'id': file_id}).encode(),
                                         method='POST', headers={'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json'})
        with self.opener.open(request, timeout=120) as response:
            return urllib.parse.unquote(response.headers.get('X-Daevis-Name', 'file')), response.read()


def toggle(path=settings.PATH):
    values = settings.load(path)
    values['remote_control'] = not values.get('remote_control', True)
    settings.save(path, values)
    return values['remote_control']


def watch_hotkey(on_toggle, log=lambda line: None):
    """Ctrl+Alt+F12 flips remote control from anywhere; a high beep means on, a low one off."""
    def run():
        import ctypes
        from ctypes import wintypes
        user32 = ctypes.windll.user32
        MOD_ALT, MOD_CONTROL, MOD_NOREPEAT, VK_F12, WM_HOTKEY = 0x1, 0x2, 0x4000, 0x7B, 0x312
        if not user32.RegisterHotKey(None, 0xDAE, MOD_ALT | MOD_CONTROL | MOD_NOREPEAT, VK_F12):
            log(f'remote hotkey {HOTKEY} unavailable')
            return
        message = wintypes.MSG()
        while user32.GetMessageW(ctypes.byref(message), None, 0, 0) > 0:
            if message.message == WM_HOTKEY:
                try:
                    on_toggle()
                except Exception as error:
                    log(f'remote toggle failed ({type(error).__name__})')
    thread = threading.Thread(target=run, daemon=True, name='daevis-remote-hotkey')
    thread.start()
    return thread


def start(config_path, log=lambda line: None, folder=None):
    """Start the remote-control loop and the hotkey in background threads."""
    import winsound
    config = json.loads(Path(config_path).read_text(encoding='utf-8-sig'))
    folder = Path(folder or Path.home() / '.daevis' / 'desktop')
    runner = Remote(RelayClient(config), settings.load, folder / 'remote-actions.jsonl')

    def flip():
        on = toggle()
        log('remote control ' + ('on' if on else 'off'))
        winsound.Beep(1400 if on else 500, 180)
    watch_hotkey(flip, log)
    from desktop import file_index, pc_ops
    file_index.keep_fresh(pc_ops.default_roots, pc_ops.SKIP_DIRS, log)

    def loop():
        try:
            import comtypes
            comtypes.CoInitialize()  # UI Automation runs on this thread.
        except Exception:
            pass
        runner.forever(lambda error: log(f'remote poll failed ({type(error).__name__})'))
    thread = threading.Thread(target=loop, daemon=True, name='daevis-remote')
    thread.start()
    return thread
