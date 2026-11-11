"""Headless Daevis: wake word, spoken answers and the PC power receiver, with no window.

`Daevis.exe --agent` starts this at Windows login. The HUD window (`Daevis.exe`) is optional:
when it opens while the agent runs, it shows requests but leaves the microphone to the agent.
The listener is supervised: if it fails (no microphone yet at login, a driver hiccup), it is
retried after a short wait instead of leaving voice off until the next login.
"""
import json
import msvcrt
from pathlib import Path
import time
import traceback

from desktop import core, relay, settings, tts

FOLDER = Path.home() / '.daevis' / 'desktop'
RETRY_SECONDS = (10, 30, 60, 120)


def log(line, folder=None):
    try:
        with ((folder or FOLDER) / 'agent.log').open('a', encoding='utf-8') as f:
            print(time.strftime('%F %T'), line, file=f)
    except OSError:
        pass


def running(folder=FOLDER):
    """True while another process holds the agent lock (used by the window to stay off the microphone)."""
    path = folder / 'agent.lock'
    if not path.exists():
        return False
    try:
        with path.open('a+b') as f:
            msvcrt.locking(f.fileno(), msvcrt.LK_NBLCK, 1)
            msvcrt.locking(f.fileno(), msvcrt.LK_UNLCK, 1)
        return False
    except OSError:
        return True


class Speaker:
    def __init__(self):
        self.tts = tts.Speaker()

    def speak(self, text, voice, rate):
        self.tts.speak(text, voice, rate)


class Listening:
    """The 'listen' switch from settings, re-read at most every few seconds (the mic callback asks ~30 times a second)."""
    def __init__(self, load=settings.load, clock=time.monotonic, every=5):
        self.load, self.clock, self.every = load, clock, every
        self.at, self.value = None, True

    def __call__(self):
        now = self.clock()
        if self.at is None or now - self.at >= self.every:
            self.at = now
            try:
                self.value = bool(self.load()['listen'])
            except Exception:
                pass
        return self.value


def supervise(make_listener, sleep=time.sleep, retries=RETRY_SECONDS, forever=True):
    """Run the listener; when it stops, wait and start a new one. Returns after one round if forever=False."""
    attempt = 0
    while True:
        listener = make_listener()
        listener.start()
        listener.join()
        reason = getattr(listener, 'failure', None) or 'stopped'
        delay = retries[min(attempt, len(retries) - 1)]
        trace = getattr(listener, 'trace', None)
        # The first failure in a row carries the traceback; repeats only say why, to keep the log short.
        log(f'listener ended ({reason}); retry in {delay}s' + ('\n' + trace if trace and attempt == 0 else ''))
        attempt = attempt + 1 if not getattr(listener, 'was_listening', False) else 0
        if not forever:
            return reason
        sleep(delay)


def isolate(path=None):
    """Run from our own folder and drop the start folder from the import path.

    Windows starts login and scheduled programs in folders such as %TEMP%; a stray `h2.py`
    there was imported in place of the HTTP/2 library and stopped speech recognition.
    """
    import os
    import sys
    start = os.path.abspath(os.getcwd())
    keep = getattr(sys, '_MEIPASS', None)
    sys.path[:] = [p for p in sys.path if p not in ('', '.') and (os.path.abspath(p) != start or os.path.abspath(p) == keep)]
    os.chdir(path or FOLDER)


def main():
    FOLDER.mkdir(parents=True, exist_ok=True)
    isolate()
    lock = (FOLDER / 'agent.lock').open('a+b')
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        return 0  # Already running.
    log('agent start')
    try:
        values = settings.load()
        if values.get('autostart'):
            try:
                core.set_autostart(True)
            except OSError as error:
                log(f'autostart not set ({type(error).__name__})')
        config = None
        while config is None:
            config = relay.find_config()
            if config is None:
                log('PC not registered yet; waiting')
                time.sleep(60)
        client = relay.Relay(json.loads(Path(config).read_text(encoding='utf-8-sig')))
        controller = core.Controller(client, Speaker(), values, lambda event: None)
        try:
            core.retire_legacy_voice()
        except Exception:
            pass
        try:
            core.start_power_receiver(config)
        except Exception as error:
            log(f'power receiver not started ({type(error).__name__})')
        try:
            from desktop import remote
            remote.start(config, log)  # The owner's server agent may operate this PC (Ctrl+Alt+F12 turns it off).
        except Exception as error:
            log(f'remote control not started ({type(error).__name__})')

        listen = Listening()

        def make_listener():
            return core.Listener(lambda text: controller.request(text, 'voice'),
                                 lambda text: log('status: ' + text), listen)
        supervise(make_listener)
    except Exception:
        log('agent crashed\n' + traceback.format_exc())
        raise
