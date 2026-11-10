"""Discord voice messages from the owner's phone: transcribe on the server and handle as text.

The phone needs nothing installed: hold the mic button in the Daevis DM. If the
main server was off, the power bot starts it and the message is picked up from
the DM history once the gateway is ready.
"""
from datetime import datetime, timedelta, timezone
import io
import json
import logging
import os
from pathlib import Path
import re
import threading

log = logging.getLogger(__name__)
DATA = Path(os.environ.get('AGENT_DATA', Path(__file__).resolve().parent.parent / 'data'))
STATE = DATA / 'voice-processed.json'
AUDIO = ('.ogg', '.oga', '.m4a', '.mp3', '.wav', '.aac', '.webm')
MAX_BYTES = 8_000_000
PROMPT = '대비스, 카톡 보내줘, 전화 걸어줘, PC 꺼줘, 켜줘, 배민'
_model, _lock = None, threading.Lock()


def voice_attachment(message):
    for item in getattr(message, 'attachments', None) or []:
        kind = (getattr(item, 'content_type', '') or '').split(';')[0]
        name = (getattr(item, 'filename', '') or '').lower()
        if (kind.startswith('audio/') or name.endswith(AUDIO)) and 0 < (getattr(item, 'size', 0) or 0) <= MAX_BYTES:
            return item
    return None


def _decode(data):
    """Any Discord audio (ogg/opus, m4a, wav) to 16 kHz mono float32.

    faster-whisper's own decoder passes an option newer PyAV releases removed.
    """
    import av
    import numpy as np
    chunks = []
    with av.open(io.BytesIO(data)) as container:
        resampler = av.AudioResampler(format='s16', layout='mono', rate=16000)
        for frame in container.decode(audio=0):
            chunks.extend(f.to_ndarray() for f in resampler.resample(frame))
        chunks.extend(f.to_ndarray() for f in resampler.resample(None))
    if not chunks:
        return np.zeros(0, np.float32)
    return np.concatenate(chunks, axis=1).flatten().astype(np.float32) / 32768.0


def transcribe_bytes(data):
    global _model
    from faster_whisper import WhisperModel
    with _lock:  # One model, one transcription at a time on the small server.
        if _model is None:
            _model = WhisperModel('small', device='cpu', compute_type='int8', cpu_threads=2,
                                  download_root=str(DATA / 'whisper'))
        audio = _decode(data)
        if len(audio) > 16000 * 90:
            audio = audio[:16000 * 90]
        segments, _ = _model.transcribe(audio, language='ko', beam_size=1, vad_filter=True,
                                        initial_prompt=PROMPT, condition_on_previous_text=False)
        text = ' '.join(s.text.strip() for s in segments if s.no_speech_prob < 0.6 and s.avg_logprob > -1.0).strip()
    squash = lambda s: re.sub(r'[\s,.!?]', '', s)
    if text and squash(text) in squash(PROMPT):
        return ''  # Whisper repeated its own prompt on silence.
    return re.sub(r'^\s*[대데다재자]\s*비\s*[스수쓰]\s*[,.!?~]*\s*', '', text)[:300].strip()


def _load():
    try:
        return set(json.loads(STATE.read_text()))
    except (OSError, ValueError):
        return set()


def processed(message_id):
    return str(message_id) in _load()


def mark_processed(message_id):
    ids = list(_load() | {str(message_id)})[-300:]
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(ids))


async def transcribe_message(bot, message):
    """Transcript of an unhandled owner voice message, '' when unclear, None when not one."""
    import asyncio
    item = voice_attachment(message)
    if item is None or processed(message.id):
        return None
    mark_processed(message.id)  # At most once, even if transcription fails.
    try:
        text = await asyncio.to_thread(transcribe_bytes, await item.read())
    except Exception as error:
        log.warning('voice transcription failed (%s)', type(error).__name__)
        text = ''
    if not text:
        await message.channel.send('음성을 알아듣지 못했어요. 조금 더 또렷하게 다시 보내 주세요.')
    return text


async def process_backlog(bot, window=timedelta(minutes=15)):
    """Handle voice messages that arrived while the main server was off."""
    dm = await bot.owner_dm()
    since = datetime.now(timezone.utc) - window
    async for message in dm.history(limit=30, after=since):
        if message.author.id == bot.owner_id and not message.content.strip() and voice_attachment(message) and not processed(message.id):
            await bot.on_message(message)
