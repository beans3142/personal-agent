"""Methods the HUD page may call (window.pywebview.api.*) and events pushed to it.

pywebview exposes every public attribute of this object to JavaScript, so all
state lives in underscore names and only the calls below are public.
"""
import threading

from desktop import relay, settings, tts

LIMITS = {'rate': (-50, 50)}


class Bridge:
    def __init__(self, settings_path=settings.PATH, emit=lambda event: None, autostart=None):
        self._path = settings_path
        self._settings = settings.load(settings_path)
        self._emit = emit
        self._autostart = autostart
        self._speaker = tts.Speaker()
        self._relay = self._controller = self._window = None
        self._on_register = None
        self._ready = threading.Event()  # Set once the page can receive events.
        self._status = ''  # Last listener status, for a page that loads after it was sent.

    # --- wiring (Python side only) ----------------------------------------------------
    def _connect(self, client, controller=None):
        self._relay, self._controller = client, controller

    def _say(self, text, voice=None, rate=None):
        """Speak with the HUD showing the speaking state."""
        self._emit({'kind': 'mode', 'mode': 'speaking'})
        try:
            self._speaker.speak(text, voice or self._settings['voice'], self._settings['rate'] if rate is None else rate)
        finally:
            self._emit({'kind': 'mode', 'mode': 'idle'})

    # --- called from the page ------------------------------------------------------------
    def boot(self):
        voices = [{'id': v.id, 'name': v.label.split(' (')[0], 'detail': v.label.split(' (')[1].rstrip(')') if ' (' in v.label else '',
                   'online': v.engine == 'edge'} for v in tts.VOICES]
        self._ready.set()
        return {'registered': self._relay is not None, 'pc': getattr(self._relay, 'label', ''),
                'settings': dict(self._settings), 'voices': voices, 'status': self._status,
                'features': {'wake': True, 'autostart': True, 'window': True, 'push': False}}

    def register(self, pasted):
        try:
            path = relay.enroll(pasted)
        except ValueError as error:
            return {'ok': False, 'error': str(error)}
        except Exception:
            return {'ok': False, 'error': '등록하지 못했어요. 코드가 만료됐으면 Discord에서 다시 받아 주세요.'}
        if self._on_register:
            self._on_register(path)
        return {'ok': True}

    def send(self, text, thread=None, wait=False):
        text = (text or '').strip()
        if not text or self._controller is None:
            return {'ok': False, 'error': '보낼 내용을 적어 주세요.'}
        if thread is not None and not (isinstance(thread, str) and len(thread) == 32):
            thread = None
        queued = self._controller.busy.locked()
        worker = threading.Thread(target=self._controller.request, args=(text, 'typed', thread), daemon=True)
        worker.start()
        if wait:
            worker.join()
        return {'ok': True, 'queued': queued}

    def history(self, before=None, q=None):
        try:
            return self._relay.history(before=before, q=(q or '').strip()[:100] or None)
        except Exception:
            return {'error': '기록을 불러오지 못했어요.', 'items': [], 'more': False}

    def answer_payment(self, job, approval, value):
        if value not in ('allow', 'deny'):
            return {'ok': False}
        try:
            self._relay.approve(job, approval, value)
            return {'ok': True}
        except Exception:
            return {'ok': False, 'error': '결제 응답을 보내지 못했어요. Discord 버튼으로 답해 주세요.'}

    def set_setting(self, key, value):
        default = settings.DEFAULTS.get(key)
        if default is None or type(value) is not type(default):
            return {'ok': False}
        if key == 'voice' and value not in {v.id for v in tts.VOICES}:
            return {'ok': False}
        if key in LIMITS and not LIMITS[key][0] <= value <= LIMITS[key][1]:
            return {'ok': False}
        self._settings[key] = value
        settings.save(self._path, self._settings)
        if key == 'autostart' and self._autostart:
            try:
                self._autostart(value)
            except OSError:
                pass
        return {'ok': True}

    def preview(self, voice_id, rate, wait=False):
        worker = threading.Thread(target=self._say, args=(tts.SAMPLE, voice_id, int(rate)), daemon=True)
        worker.start()
        if wait:
            worker.join()
        return {'ok': True}

    def stop_speaking(self):
        self._speaker.stop()
        return {'ok': True}

    def minimize(self):
        if self._window:
            self._window.minimize()

    def hide(self):
        # Closing keeps the assistant listening; "종료" in settings really quits.
        self.minimize()

    def quit(self):
        if self._window:
            self._window.destroy()
