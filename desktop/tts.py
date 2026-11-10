"""Answer voices: Microsoft neural voices through edge-tts, Windows Heami offline,
and character voices from a local voice-clone server.

edge-tts (LGPLv3) calls the online service behind Edge's "Read aloud". It is not
an official API and can stop working, so every failure falls back to the
Windows voice that ships with Korean Windows.

Character voices: each folder in ~/.daevis/voices/<name>/ holds profile.json and
short reference clips per emotion. Speech is made by a GPT-SoVITS server
(api_v2.py) running on this PC; only loopback servers are accepted.
"""
import ctypes
from dataclasses import dataclass
import json
import os
import re
import urllib.parse
import urllib.request
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import uuid


@dataclass(frozen=True)
class Voice:
    id: str
    label: str
    engine: str


VOICES = [
    Voice('ko-KR-SunHiNeural', '선희 · 여성 (Microsoft 신경망 음성)', 'edge'),
    Voice('ko-KR-InJoonNeural', '인준 · 남성 (Microsoft 신경망 음성)', 'edge'),
    Voice('ko-KR-HyunsuMultilingualNeural', '현수 · 남성, 다국어 (Microsoft 신경망 음성)', 'edge'),
    Voice('windows', '해미 · 여성 (Windows 내장 음성)', 'windows'),
]
DEFAULT_VOICE = 'ko-KR-SunHiNeural'
VOICES_DIR = Path.home() / '.daevis' / 'voices'
EMOTIONS = ('neutral', 'happy', 'sad', 'serious')
SAMPLE = '안녕하세요, 대비스예요. 오늘 일정은 오후 세 시 회의 하나예요.'
SAY_SCRIPT = ("Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "try { $s.SelectVoice('Microsoft Heami Desktop') } catch {}; $s.Rate=[int]$env:DAEVIS_RATE; $s.Speak($env:DAEVIS_SAY)")


def load_profile(folder):
    """A character voice profile, or None if it is unusable or points beyond this PC."""
    try:
        data = json.loads((Path(folder) / 'profile.json').read_text(encoding='utf-8'))
        host = urllib.parse.urlsplit(data['server']).hostname
        if host not in ('127.0.0.1', 'localhost', '::1') or not isinstance(data.get('emotions'), dict):
            return None
        if not any((Path(folder) / e.get('ref', '')).is_file() for e in data['emotions'].values() if isinstance(e, dict)):
            return None
        return data
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None


def all_voices(voices_dir=None):
    """Built-in voices followed by the character voices found on this PC."""
    found = []
    folder = Path(voices_dir or VOICES_DIR)
    for sub in sorted(folder.iterdir()) if folder.is_dir() else []:
        data = load_profile(sub) if sub.is_dir() else None
        if data:
            found.append(Voice('clone:' + sub.name, str(data.get('label') or sub.name) + ' (내 PC 음성 서버, 감정 표현)', 'clone'))
    return VOICES + found


def voice(voice_id, voices_dir=None):
    choices = all_voices(voices_dir) if str(voice_id).startswith('clone:') else VOICES
    return next((v for v in choices if v.id == voice_id), next(v for v in VOICES if v.id == DEFAULT_VOICE))


def emotion_for(text, ok=True):
    """A simple tone for the answer: apologies and failures sad, payments serious, good news happy."""
    text = text or ''
    if not ok or re.search(r'죄송|미안|실패|못했|못 했|안 돼|안돼|오류|문제가', text):
        return 'sad'
    if re.search(r'결제|승인|주의|경고|위험|확인해 주세요', text):
        return 'serious'
    if re.search(r'!|완료|마쳤|성공|좋아요|축하|반가|도착했', text):
        return 'happy'
    return 'neutral'


def clone_request(data, folder, text, rate):
    emotions = data['emotions']
    mood = emotion_for(text)
    pick = emotions.get(mood) if isinstance(emotions.get(mood), dict) and (Path(folder) / emotions[mood].get('ref', '')).is_file() else None
    pick = pick or emotions.get('neutral') or next(e for e in emotions.values() if isinstance(e, dict))
    lang = data.get('lang', 'ko')
    return data['server'].rstrip('/') + '/tts', {
        'text': text, 'text_lang': lang, 'ref_audio_path': str(Path(folder) / pick['ref']), 'prompt_text': pick.get('text', ''),
        'prompt_lang': lang, 'media_type': 'wav', 'streaming_mode': False, 'speed_factor': round(1 + max(-50, min(50, int(rate))) / 100, 2)}


def post_wav(url, body):
    request = urllib.request.Request(url, data=json.dumps(body, ensure_ascii=False).encode(), method='POST',
                                     headers={'Content-Type': 'application/json'})
    with urllib.request.urlopen(request, timeout=120) as response:
        data = response.read(50_000_000)
    if not data.startswith(b'RIFF'):
        raise OSError('voice server did not return audio')
    return data


def _clamp(rate):
    return max(-50, min(50, int(rate)))


def edge_rate(rate):
    return f'{_clamp(rate):+d}%'


def windows_rate(rate):
    return int(_clamp(rate) / 10)


def windows_command(text, rate):
    """Text and speed travel in the environment, never in the command line."""
    env = dict(os.environ, DAEVIS_SAY=text, DAEVIS_RATE=str(rate))
    return ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', SAY_SCRIPT], env


def speak_windows(text, rate):
    argv, env = windows_command(text, windows_rate(rate))
    subprocess.run(argv, env=env, timeout=180, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def synth_edge(text, voice_id, rate, path):
    import asyncio
    import edge_tts
    asyncio.run(edge_tts.Communicate(text, voice_id, rate=rate).save(str(path)))


def play_mp3(path, stop=None):
    """Play through the Windows MCI player (no extra audio library)."""
    mci = ctypes.windll.winmm.mciSendStringW
    alias = 'daevis' + uuid.uuid4().hex[:8]
    if mci(f'open "{path}" type mpegvideo alias {alias}', None, 0, 0):
        raise OSError('audio device unavailable')
    try:
        mci(f'play {alias}', None, 0, 0)
        status = ctypes.create_unicode_buffer(32)
        while True:
            mci(f'status {alias} mode', status, 32, 0)
            if status.value != 'playing' or (stop is not None and stop.is_set()):
                break
            time.sleep(0.1)
    finally:
        mci(f'close {alias}', None, 0, 0)


class Speaker:
    def __init__(self, edge=synth_edge, play=None, windows=speak_windows, folder=None, voices_dir=None, clone_post=post_wav):
        self.stop_event = threading.Event()
        self.edge, self.windows = edge, windows
        self.voices_dir, self.clone_post = Path(voices_dir or VOICES_DIR), clone_post
        self.play = play or (lambda path: play_mp3(path, self.stop_event))
        self.folder = Path(folder or tempfile.gettempdir())
        self.lock = threading.Lock()

    def speak(self, text, voice_id, rate):
        """Returns the engine that spoke, or None for empty text."""
        text = (text or '').strip()
        if not text:
            return None
        with self.lock:
            self.stop_event.clear()
            if str(voice_id).startswith('clone:'):
                folder = self.voices_dir / str(voice_id)[6:]
                data = load_profile(folder)
                path = self.folder / f'daevis-say-{uuid.uuid4().hex}.wav'
                try:
                    if data:
                        url, body = clone_request(data, folder, text, rate)
                        path.write_bytes(self.clone_post(url, body))
                        self.play(path)
                        return 'clone'
                except Exception:
                    pass  # Voice server not running: answer with the default voice instead.
                finally:
                    path.unlink(missing_ok=True)
                voice_id = DEFAULT_VOICE
            if voice(voice_id).engine == 'edge':
                path = self.folder / f'daevis-say-{uuid.uuid4().hex}.mp3'
                try:
                    self.edge(text, voice(voice_id).id, edge_rate(rate), path)
                    self.play(path)
                    return 'edge'
                except Exception:
                    pass  # Offline or blocked: the Windows voice still answers.
                finally:
                    path.unlink(missing_ok=True)
            self.windows(text, rate)
            return 'windows'

    def stop(self):
        self.stop_event.set()
