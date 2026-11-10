"""Answer voices: Microsoft neural voices through edge-tts, Windows Heami offline.

edge-tts (LGPLv3) calls the online service behind Edge's "Read aloud". It is not
an official API and can stop working, so every failure falls back to the
Windows voice that ships with Korean Windows.
"""
import ctypes
from dataclasses import dataclass
import os
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
SAMPLE = '안녕하세요, 대비스예요. 오늘 일정은 오후 세 시 회의 하나예요.'
SAY_SCRIPT = ("Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "try { $s.SelectVoice('Microsoft Heami Desktop') } catch {}; $s.Rate=[int]$env:DAEVIS_RATE; $s.Speak($env:DAEVIS_SAY)")


def voice(voice_id):
    return next((v for v in VOICES if v.id == voice_id), next(v for v in VOICES if v.id == DEFAULT_VOICE))


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
    def __init__(self, edge=synth_edge, play=None, windows=speak_windows, folder=None):
        self.stop_event = threading.Event()
        self.edge, self.windows = edge, windows
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
