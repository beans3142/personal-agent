import sys
import types

from desktop import agent, core


class FakeListener:
    runs = []

    def __init__(self, failure=None, was_listening=False):
        self.failure, self.was_listening = failure, was_listening

    def start(self):
        FakeListener.runs.append(self.failure)

    def join(self):
        pass


def test_supervisor_restarts_failed_listener_with_backoff(tmp_path, monkeypatch):
    monkeypatch.setattr(agent, 'FOLDER', tmp_path)
    results = iter([FakeListener('AssertionError: no input device'), FakeListener('AssertionError: no input device'), FakeListener(None, True)])
    slept = []

    class Stop(Exception):
        pass

    def sleep(seconds):
        slept.append(seconds)
        if len(slept) == 3:
            raise Stop

    FakeListener.runs = []
    try:
        agent.supervise(lambda: next(results), sleep=sleep, retries=(10, 30, 60))
    except Stop:
        pass
    assert FakeListener.runs == ['AssertionError: no input device', 'AssertionError: no input device', None]
    assert slept == [10, 30, 60]
    text = (tmp_path / 'agent.log').read_text(encoding='utf-8')
    assert 'listener ended (AssertionError: no input device); retry in 10s' in text


def test_listening_switch_is_cached():
    calls = []
    now = [0.0]
    switch = agent.Listening(load=lambda: calls.append(1) or {'listen': False}, clock=lambda: now[0], every=5)
    assert switch() is False and switch() is False and len(calls) == 1
    now[0] = 6
    switch()
    assert len(calls) == 2


def test_autostart_registers_agent_and_removes_window_entry(monkeypatch):
    store = {'DaevisDesktop': '"x" --background'}

    class Key:
        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    fake = types.SimpleNamespace(HKEY_CURRENT_USER=1, KEY_SET_VALUE=2, REG_SZ=1, OpenKey=lambda *a: Key(),
                                 SetValueEx=lambda key, name, _, kind, value: store.__setitem__(name, value),
                                 DeleteValue=lambda key, name: store.pop(name) if name in store else (_ for _ in ()).throw(OSError()))
    monkeypatch.setitem(sys.modules, 'winreg', fake)
    core.set_autostart(True, exe='C:/Daevis.exe')
    assert store == {'DaevisAgent': '"C:/Daevis.exe" --agent'}
    core.set_autostart(False, exe='C:/Daevis.exe')
    assert store == {}


def test_start_agent_detaches_a_separate_process(monkeypatch):
    seen = {}
    monkeypatch.setattr(sys, 'frozen', True, raising=False)
    monkeypatch.setattr(sys, 'executable', 'C:/Daevis.exe')
    core.start_agent(popen=lambda argv, **kw: seen.update(argv=argv, **kw))
    assert seen['argv'] == ['C:/Daevis.exe', '--agent'] and seen['creationflags'] and seen['close_fds']


def test_running_reports_a_held_lock(tmp_path):
    import msvcrt
    assert agent.running(tmp_path) is False
    lock = (tmp_path / 'agent.lock').open('a+b')
    msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    try:
        assert agent.running(tmp_path) is True
    finally:
        msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
        lock.close()


def test_isolate_drops_start_folder_from_import_path(tmp_path, monkeypatch):
    import os
    start = tmp_path / 'temp'
    start.mkdir()
    (start / 'h2.py').write_text('raise RuntimeError("shadowed")')
    home = tmp_path / 'home'
    home.mkdir()
    monkeypatch.chdir(start)
    monkeypatch.setattr(sys, 'path', ['', str(start), 'C:/lib'])
    agent.isolate(home)
    assert sys.path == ['C:/lib'] and os.path.abspath(os.getcwd()) == str(home)
