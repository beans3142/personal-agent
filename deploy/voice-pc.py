#!/usr/bin/env python3
"""Daevis PC voice assistant: say "대비스, ..." and hear the answer.

Runs on the owner's Windows PC. Listening, wake-word detection and speech
recognition stay on this PC (faster-whisper, GPU when available); only the
recognised command text goes to the always-on power relay, using this PC's
existing node credentials. Answers are spoken with the Windows Korean voice.

Run: python voice-pc.py [--config ~/.daevis/pcs/<node>/node.json]
"""
import argparse
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import time
import urllib.request
import uuid

import numpy as np

RATE, FRAME = 16000, 480  # 30 ms frames
WAKE = re.compile(r'^\s*[대데다재자]\s*비\s*[스수쓰]\s*[,.!?~]*\s*(.*)$')
SAY_SCRIPT = ("Add-Type -AssemblyName System.Speech; $s=New-Object System.Speech.Synthesis.SpeechSynthesizer; "
              "try { $s.SelectVoice('Microsoft Heami Desktop') } catch {}; $s.Speak($env:DAEVIS_SAY)")


def after_wake(text):
    """Command after the wake word, '' for the wake word alone, None if not addressed."""
    match = WAKE.match(text or '')
    return match[1].strip() if match else None


class Segmenter:
    """Energy-based utterance cutter with an adaptive noise floor."""
    def __init__(self, rate=RATE, frame=FRAME, silence=0.6, max_seconds=15):
        self.frame_seconds = frame / rate
        self.end_frames = int(silence / self.frame_seconds)
        self.max_frames = int(max_seconds / self.frame_seconds)
        self.floor, self.frames, self.quiet, self.active, self.pre = 0.002, [], 0, False, []

    def feed(self, frame):
        level = float(np.sqrt(np.mean(np.square(frame))))
        loud = level > max(0.012, self.floor * 4)
        if not self.active:
            self.floor = 0.95 * self.floor + 0.05 * level
            self.pre = (self.pre + [frame])[-5:]
            if loud:
                self.active, self.frames, self.quiet = True, list(self.pre), 0
            return None
        self.frames.append(frame)
        self.quiet = 0 if loud else self.quiet + 1
        if self.quiet >= self.end_frames or len(self.frames) >= self.max_frames:
            keep = len(self.frames) - max(0, self.quiet - 5)  # Trim trailing silence.
            utterance = np.concatenate(self.frames[:keep])
            self.active, self.frames, self.pre = False, [], []
            return utterance
        return None


def speech_command(text):
    """PowerShell command for the Korean voice; text travels only in the environment."""
    env = dict(os.environ, DAEVIS_SAY=text)
    return ['powershell.exe', '-NoProfile', '-NonInteractive', '-Command', SAY_SCRIPT], env


def say(text):
    argv, env = speech_command(text)
    subprocess.run(argv, env=env, timeout=120, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                   stderr=subprocess.DEVNULL, creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))


def beep(high=True):
    try:
        import winsound
        winsound.Beep(988 if high else 660, 110)
    except Exception:
        pass


def ask(post, node, text, speak, sleep=time.sleep, limit=300):
    receipt = post('/request', {'node': node, 'target': 'voice', 'action': 'voice_command', 'payload': {'text': text},
                                'request_key': 'voice-' + uuid.uuid4().hex, 'ttl_seconds': 300})
    announced = False
    for _ in range(limit):
        state = post('/request-status', {'node': node, 'id': receipt['id']})
        result = state.get('result') or {}
        if result.get('say') and not announced:
            speak(result['say']); announced = True
        if state['status'] in ('succeeded', 'failed'):
            speak(result.get('reply') or ('처리했어요.' if state['status'] == 'succeeded' else '처리하지 못했어요.'))
            return state['status']
        if state['status'] in ('expired', 'uncertain', 'cancelled'):
            speak('답을 받지 못했어요.')
            return state['status']
        sleep(1)
    speak('답을 받지 못했어요.')
    return 'timeout'


def relay_post(config):
    token = Path(config['token_file']).read_text().strip()
    base = config['url'].rstrip('/')
    if not base.startswith('https://'):
        raise ValueError('relay must be HTTPS')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(NoRedirect)
    def post(endpoint, body):
        request = urllib.request.Request(base + endpoint, data=json.dumps(body).encode(), method='POST',
                                         headers={'Authorization': 'Bearer ' + token, 'Content-Type': 'application/json'})
        with opener.open(request, timeout=25) as response:
            return json.loads(response.read(65536))
    return post


def load_model(name):
    try:
        import nvidia.cublas, nvidia.cudnn
        for module in (nvidia.cublas, nvidia.cudnn):
            folder = os.path.join(list(module.__path__)[0], 'bin')
            os.add_dll_directory(folder); os.environ['PATH'] = folder + os.pathsep + os.environ['PATH']
    except Exception:
        pass
    from faster_whisper import WhisperModel
    try:
        return WhisperModel(name, device='cuda', compute_type='float16')
    except Exception:
        return WhisperModel('small', device='cpu', compute_type='int8')


PROMPT = '대비스, 카톡 보내줘, 전화 걸어줘, PC 꺼줘, 켜줘'


def transcribe(model, audio):
    segments, _ = model.transcribe(audio, language='ko', beam_size=1, vad_filter=False,
                                   initial_prompt=PROMPT, condition_on_previous_text=False)
    # Drop segments Whisper itself rates as non-speech or low confidence.
    text = ' '.join(s.text.strip() for s in segments if s.no_speech_prob < 0.6 and s.avg_logprob > -1.0).strip()
    return '' if echoes_prompt(text) else text


def echoes_prompt(text):
    """Whisper can hallucinate its own prompt on noise; never act on that."""
    squash = lambda s: re.sub(r'[\s,.!?]', '', s)
    heard = squash(text)
    # The bare wake word only triggers a beep and a second listen, so it may pass.
    return bool(heard) and heard in squash(PROMPT) and after_wake(text) != ''


def default_config():
    found = sorted(Path.home().glob('.daevis/pcs/*/node.json'))
    if not found:
        raise SystemExit('PC 등록 정보가 없어요. Discord에서 PC를 먼저 등록하세요.')
    return found[0]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path)
    parser.add_argument('--model', default='medium')
    args = parser.parse_args()
    config = json.loads((args.config or default_config()).read_text(encoding='utf-8-sig'))
    post = relay_post(config)
    folder = Path.home() / '.daevis' / 'voice'
    folder.mkdir(parents=True, exist_ok=True)
    import msvcrt
    lock = (folder / 'voice.lock').open('a+b')
    try:
        msvcrt.locking(lock.fileno(), msvcrt.LK_NBLCK, 1)
    except OSError:
        raise SystemExit(0)  # Already running.
    log = (folder / 'voice.log').open('a', encoding='utf-8', buffering=1)
    model = load_model(args.model)
    import sounddevice as sd
    frames = queue.Queue(maxsize=2000)
    def callback(data, *_):
        try:
            frames.put_nowait(data[:, 0].copy())
        except queue.Full:
            pass
    segmenter = Segmenter()
    print(time.strftime('%F %T'), 'listening', file=log)
    with sd.InputStream(samplerate=RATE, channels=1, dtype='float32', blocksize=FRAME, callback=callback):
        while True:
            utterance = segmenter.feed(frames.get())
            if utterance is None:
                continue
            command = after_wake(transcribe(model, utterance))
            if command is None:
                continue  # Not addressed to Daevis; nothing is logged or sent.
            beep()
            if not command:
                deadline = time.time() + 6
                while time.time() < deadline and command == '':
                    try:
                        chunk = segmenter.feed(frames.get(timeout=0.5))
                    except queue.Empty:
                        continue
                    if chunk is not None:
                        command = transcribe(model, chunk)
            if not command:
                continue
            print(time.strftime('%F %T'), 'request sent', file=log)
            try:
                ask(post, config['node'], command[:300], say)
            except Exception as error:
                print(time.strftime('%F %T'), 'request failed', type(error).__name__, file=log)
                say('서버에 연결하지 못했어요.')
            while not frames.empty():
                frames.get_nowait()  # Drop what the speaker said while answering.
            segmenter = Segmenter()


if __name__ == '__main__':
    main()
