"""Window-independent parts of the desktop app: request flow, wake-word listener,
PC power receiver, legacy voice client handover and login start-up."""
import importlib.util
import json
import os
from pathlib import Path
import queue
import sqlite3
import subprocess
import sys
import threading
import time

FAILED = {'expired': '답을 받지 못했어요.', 'uncertain': '답을 받지 못했어요.', 'cancelled': '요청이 취소됐어요.',
          'timeout': '답을 받지 못했어요.', 'unknown': '요청을 찾지 못했어요.'}


def resource(name):
    """deploy/ files: next to the source tree, or unpacked inside the exe."""
    base = Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1] / 'deploy'))
    return base / name


def load_script(name, module):
    spec = importlib.util.spec_from_file_location(module, resource(name))
    loaded = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(loaded)
    return loaded


class Controller:
    """Sends one request, follows its receipt and turns it into UI events and speech."""
    def __init__(self, relay, speaker, settings, emit):
        self.relay, self.speaker, self.settings, self._emit = relay, speaker, settings, emit
        self.busy = threading.Lock()

    def emit(self, event):
        try:
            self._emit(event)
        except Exception:
            pass  # A broken display or console must not end the request.

    def say(self, text):
        self.speaker.speak(text, self.settings['voice'], self.settings['rate'])

    def request(self, text, via, thread=None):
        speak = via == 'voice' or self.settings['speak_typed']
        with self.busy:
            try:
                job = self.relay.submit(text, via, thread=thread) if thread else self.relay.submit(text, via)
            except ValueError as error:
                self.emit({'kind': 'answer', 'job': None, 'ok': False, 'text': str(error)})
                return
            except Exception:
                self.emit({'kind': 'answer', 'job': None, 'ok': False, 'text': '서버에 연결하지 못했어요. 인터넷 연결을 확인해 주세요.'})
                if speak:
                    self.say('서버에 연결하지 못했어요.')
                return
            self.emit({'kind': 'started', 'job': job, 'text': text, 'via': via, 'thread': thread or job})
            seen = {'say': False, 'approval': None}

            def update(state):
                result = state.get('result') or {}
                if result.get('say') and not seen['say']:
                    seen['say'] = True
                    self.emit({'kind': 'step', 'job': job, 'text': result['say']})
                    if speak:
                        self.say(result['say'])
                if result.get('step'):
                    self.emit({'kind': 'step', 'job': job, 'text': result['step'], 'steps': result.get('steps', [])})
                approval = result.get('approval')
                if approval and approval.get('id') != seen['approval']:
                    seen['approval'] = approval.get('id')
                    self.emit({'kind': 'approval', 'job': job, 'approval': approval})
                elif not approval and seen['approval']:
                    seen['approval'] = None
                    self.emit({'kind': 'approval_closed', 'job': job})

            try:
                final = self.relay.follow(job, update)
            except Exception:
                final = {'status': 'unknown', 'result': None}
            if seen['approval']:
                self.emit({'kind': 'approval_closed', 'job': job})
            result = final.get('result') or {}
            ok = final.get('status') == 'succeeded'
            short = result.get('reply') or (('처리했어요.' if ok else '처리하지 못했어요.') if final.get('status') in ('succeeded', 'failed')
                                            else FAILED.get(final.get('status'), '답을 받지 못했어요.'))
            self.emit({'kind': 'answer', 'job': job, 'ok': ok, 'text': result.get('text') or short})
            if speak:
                self.say(short)

    def answer_payment(self, job, approval, value):
        self.relay.approve(job, approval, value)


# --- speech recognition ------------------------------------------------------------

def cuda_dirs(env=os.environ):
    """cuBLAS/cuDNN DLL folders from pip's nvidia packages in the owner's Python installs."""
    roots = []
    local, roaming = env.get('LOCALAPPDATA'), env.get('APPDATA')
    if local:
        roots += Path(local).glob('Packages/PythonSoftwareFoundation.Python.*/LocalCache/local-packages/Python3*/site-packages/nvidia')
        roots += Path(local).glob('Programs/Python/Python3*/Lib/site-packages/nvidia')
    if roaming:
        roots += Path(roaming).glob('Python/Python3*/site-packages/nvidia')
    return [folder for root in roots for name in ('cublas', 'cudnn') if (folder := root / name / 'bin').is_dir()]


def prepare_cuda():
    for folder in cuda_dirs():
        try:
            os.add_dll_directory(str(folder))
            os.environ['PATH'] = str(folder) + os.pathsep + os.environ['PATH']
        except OSError:
            pass


class Listener(threading.Thread):
    """Wake word "대비스" + faster-whisper, reusing deploy/voice-pc.py unchanged."""
    def __init__(self, on_command, on_state, enabled, model='medium', on_event=lambda event: None):
        super().__init__(daemon=True)
        self.on_command, self.on_state, self.enabled, self.model_name = on_command, on_state, enabled, model
        self.on_event, self.frames_seen = on_event, 0
        self.failure, self.was_listening = None, False

    def run(self):
        try:
            voice = load_script('voice-pc.py', 'daevis_voice_pc')
            folder = Path.home() / '.daevis' / 'voice'
            folder.mkdir(parents=True, exist_ok=True)
            import msvcrt
            self.lock = (folder / 'voice.lock').open('a+b')
            try:
                msvcrt.locking(self.lock.fileno(), msvcrt.LK_NBLCK, 1)
            except OSError:
                self.failure = 'microphone held by another Daevis'
                self.on_state('다른 음성 비서가 마이크를 쓰고 있어요')
                return
            self.on_state('음성 인식 모델 불러오는 중…')
            prepare_cuda()
            import ctranslate2
            # Without an NVIDIA GPU go straight to the small CPU model instead of downloading medium first.
            model = voice.load_model(self.model_name if ctranslate2.get_cuda_device_count() > 0 else 'small')
            import numpy as np
            import sounddevice as sd
            frames = queue.Queue(maxsize=2000)
            def callback(data, *_):
                self.frames_seen += 1
                if self.frames_seen % 3 == 0 and self.enabled():  # Microphone level for the HUD, ~10 times a second.
                    self.on_event({'kind': 'level', 'value': round(float(np.sqrt(np.mean(np.square(data[:, 0])))), 4)})
                if self.enabled():
                    try:
                        frames.put_nowait(data[:, 0].copy())
                    except queue.Full:
                        pass
            segmenter = voice.Segmenter()
            with sd.InputStream(samplerate=voice.RATE, channels=1, dtype='float32', blocksize=voice.FRAME, callback=callback):
                self.was_listening = True
                self.on_state('"대비스"라고 부르면 들어요')
                while True:
                    utterance = segmenter.feed(frames.get())
                    if utterance is None:
                        continue
                    started = time.monotonic()
                    command = voice.wake_command(model, utterance)
                    self.on_event({'kind': 'voice_diagnostic',
                                   'seconds': round(len(utterance) / voice.RATE, 2),
                                   'peak': round(float(np.max(np.abs(utterance))), 5),
                                   'recognition_seconds': round(time.monotonic() - started, 2),
                                   'wake': command is not None})
                    if command is None:
                        continue
                    voice.beep()
                    self.on_event({'kind': 'mode', 'mode': 'wake'})
                    if not command:
                        deadline = time.time() + 6
                        while time.time() < deadline and command == '':
                            try:
                                chunk = segmenter.feed(frames.get(timeout=0.5))
                            except queue.Empty:
                                continue
                            if chunk is not None:
                                command = voice.transcribe(model, chunk)
                    if voice.usable(command):
                        self.on_command(command[:500])
                    else:
                        self.on_event({'kind': 'mode', 'mode': 'idle'})
                    while not frames.empty():
                        frames.get_nowait()  # Drop what the speaker said while answering.
                    segmenter = voice.Segmenter()
        except Exception as error:
            import traceback
            self.failure = f'{type(error).__name__}: {error}'[:300]
            self.trace = traceback.format_exc()[-1500:]
            self.on_state(f'음성 인식을 시작하지 못했어요 ({type(error).__name__})')
        finally:
            lock = getattr(self, 'lock', None)
            if lock is not None:
                try:
                    lock.close()  # Let the next listener take the microphone lock.
                except OSError:
                    pass


# --- PC power receiver -----------------------------------------------------------------

def start_power_receiver(config_path):
    """Run deploy/device-node.py's receiver in this process unless its Python runner already does."""
    import msvcrt
    folder = Path(config_path).parent
    lock = (folder / 'runner.lock').open('a+b')
    lock.seek(0); lock.write(b'0'); lock.flush(); lock.seek(0)
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        lock.close()
        return None  # The installed Python receiver is running.
    node = load_script('device-node.py', 'daevis_device_node')
    config = json.loads(Path(config_path).read_text(encoding='utf-8-sig'))
    config['url'] = node.validate_base_url(config['url'])
    state = sqlite3.connect(str(Path(config_path).with_suffix('.journal.sqlite3')), check_same_thread=False)
    state.execute('CREATE TABLE IF NOT EXISTS journal(id TEXT PRIMARY KEY,status TEXT,acknowledged INTEGER)')
    state.commit()
    thread = threading.Thread(target=lambda: node.poll_forever(config, state), daemon=True)
    thread.lock = lock
    thread.start()
    return thread


# --- start-up ------------------------------------------------------------------------------

RUN_KEY = r'Software\Microsoft\Windows\CurrentVersion\Run'


def retire_legacy_voice(run=subprocess.run):
    """The app now listens for the wake word; stop the separate script so it is not heard twice."""
    import winreg
    removed = False
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
            winreg.DeleteValue(key, 'DaevisVoice')
            removed = True
    except OSError:
        pass
    run(['powershell.exe', '-NoProfile', '-NonInteractive', '-Command',
         "Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'voice-pc\\.py' } | ForEach-Object { Stop-Process -Id $_.ProcessId -Force }"],
        timeout=30, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
    return removed


def set_autostart(enabled, exe=None):
    """At login start only the headless agent (voice + PC power); the window opens when the owner wants it."""
    import winreg
    exe = exe or sys.executable
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN_KEY, 0, winreg.KEY_SET_VALUE) as key:
        try:
            winreg.DeleteValue(key, 'DaevisDesktop')  # Older builds started the window at login.
        except OSError:
            pass
        if enabled:
            winreg.SetValueEx(key, 'DaevisAgent', 0, winreg.REG_SZ, f'"{exe}" --agent')
        else:
            try:
                winreg.DeleteValue(key, 'DaevisAgent')
            except OSError:
                pass


def start_agent(popen=subprocess.Popen):
    """Launch the headless agent as its own process so voice keeps running after the window closes."""
    if getattr(sys, 'frozen', False):
        argv = [sys.executable, '--agent']
    else:
        argv = [sys.executable, '-m', 'desktop.app', '--agent']
    flags = getattr(subprocess, 'DETACHED_PROCESS', 0) | getattr(subprocess, 'CREATE_NEW_PROCESS_GROUP', 0) | getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    return popen(argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                 creationflags=flags, close_fds=True, cwd=str(Path.home()))
