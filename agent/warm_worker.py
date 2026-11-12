"""Keep one imported, single-use Hermes process ready; never reuse a request context."""
import atexit
import json
import os
import signal
import subprocess
import threading


def stop(process):
    if process.poll() is None:
        try:
            if os.name == 'posix':
                os.killpg(process.pid, signal.SIGKILL)
            else:
                process.kill()
        except ProcessLookupError:
            pass
    try:
        process.communicate(timeout=2)
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass


class PreparedExecutor:
    def __init__(self, argv, *, env, cold=None, boot_timeout=30):
        if cold is None:
            from .hermes_runner import run_process
            cold = run_process
        self.argv, self.env, self.cold = list(argv), dict(env), cold
        # A standby must have no active request lease or private request text.
        for key in list(self.env):
            if key.startswith('DAEVIS_MCP_') and key not in {'DAEVIS_MCP_ALLOW_TOOLS', 'DAEVIS_MCP_APPROVAL'}:
                self.env.pop(key)
        self.env.pop('AGENT_OUTBOX', None)
        self.boot_timeout = boot_timeout
        self.lock, self.slot, self.closed, self.hits = threading.Lock(), None, False, 0
        atexit.register(self.close)
        self.prepare()

    def prepare(self):
        with self.lock:
            if self.closed or self.slot is not None:
                return
            try:
                process = subprocess.Popen(self.argv + ['--prepared'], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=subprocess.DEVNULL, text=True, encoding='utf-8', env=self.env,
                                           start_new_session=True)
            except OSError:
                return
            ready = threading.Event()
            slot = {'process': process, 'ready': ready, 'ok': False}
            self.slot = slot
        def read_ready():
            try:
                line = process.stdout.readline()
                slot['ok'] = json.loads(line).get('type') == 'prepared'
            except (ValueError, OSError):
                pass
            finally:
                ready.set()
        threading.Thread(target=read_ready, daemon=True).start()

    def wait_ready(self, timeout=None):
        with self.lock:
            slot = self.slot
        return bool(slot and slot['ready'].wait(timeout) and slot['ok'] and slot['process'].poll() is None)

    def __call__(self, argv, *, input, env, timeout, **kwargs):
        if list(argv) != self.argv:
            return self.cold(argv, input=input, env=env, timeout=timeout, **kwargs)
        with self.lock:
            slot, self.slot = self.slot, None
        process = slot['process'] if slot else None
        try:
            if not slot or not slot['ready'].wait(self.boot_timeout) or not slot['ok'] or process.poll() is not None:
                if process:
                    stop(process)
                # No request was handed to the standby: cold execution is safe.
                return self.cold(argv, input=input, env=env, timeout=timeout, **kwargs)
            payload = json.loads(input)
            payload['_prepared_env'] = dict(env)
            self.hits += 1
            stdout, _ = process.communicate(json.dumps(payload, ensure_ascii=False), timeout=timeout)
            return subprocess.CompletedProcess(argv, process.returncode, stdout, '')
        finally:
            if process:
                stop(process)
            self.prepare()

    def close(self):
        with self.lock:
            self.closed = True
            slot, self.slot = self.slot, None
        if slot:
            stop(slot['process'])
        atexit.unregister(self.close)
